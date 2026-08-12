"""ConversationRelay（Twilio）用の WebSocket ハンドラ。

Twilio 側が音声認識と読み上げをやってくれるので、ここではテキストのやり取りだけを扱う。

受信するメッセージ:
    {"type": "setup",   "callSid": "...", "from": "...", "to": "..."}
    {"type": "prompt",  "voicePrompt": "はい起きました", "last": true}
    {"type": "interrupt", ...}
    {"type": "dtmf",    "digit": "1"}
    {"type": "error",   "description": "..."}

送信するメッセージ:
    {"type": "text", "token": "読み上げるテキスト", "last": true}
    {"type": "end",  "handoffData": "{...}"}
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from . import config, scheduler
from .db import session_scope
from .models import Call, CallStatus, ConversationLog
from .llm_client import Judgement, judge_wakeup

logger = logging.getLogger(__name__)


# --- DB ヘルパー（同期処理なので to_thread で逃がす） ----------------------


def _load_call(call_id: int, token: str) -> dict | None:
    with session_scope() as session:
        call = session.get(Call, call_id)
        if call is None or call.token != token:
            return None
        return {"id": call.id, "message": call.message, "status": call.status}


def _mark_in_progress(call_id: int, call_sid: str | None) -> None:
    with session_scope() as session:
        call = session.get(Call, call_id)
        if call is None:
            return
        call.status = CallStatus.IN_PROGRESS.value
        if call_sid and not call.twilio_call_sid:
            call.twilio_call_sid = call_sid


def _add_log(call_id: int, role: str, content: str, decision: str | None = None) -> None:
    with session_scope() as session:
        session.add(
            ConversationLog(call_id=call_id, role=role, content=content, decision=decision)
        )


def _finalize(call_id: int, status: str) -> None:
    with session_scope() as session:
        call = session.get(Call, call_id)
        if call is not None and call.status == CallStatus.IN_PROGRESS.value:
            call.status = status


async def _db(func, *args) -> object:
    return await asyncio.to_thread(func, *args)


# --- 読み上げ時間の見積り --------------------------------------------------


def _estimate_speech_seconds(text: str) -> float:
    """読み上げが終わる前に通話を切らないための待ち時間の目安。"""
    return min(1.5 + len(text) * 0.18, 12.0)


# --- WebSocket ハンドラ ----------------------------------------------------


class RelaySession:
    def __init__(self, websocket: WebSocket, call_id: int, wake_message: str):
        self.ws = websocket
        self.call_id = call_id
        self.wake_message = wake_message
        self.history: list[dict] = []
        self.silence_strikes = 0
        self.turns = 0
        self.outcome: str | None = None  # awake / snooze / no_answer

    async def send_text(self, text: str) -> None:
        await self.ws.send_json({"type": "text", "token": text, "last": True})
        await _db(_add_log, self.call_id, "assistant", text)

    async def send_end(self, reason: str) -> None:
        payload = json.dumps({"callId": self.call_id, "reason": reason}, ensure_ascii=False)
        try:
            await self.ws.send_json({"type": "end", "handoffData": payload})
        except Exception:
            logger.debug("end メッセージの送信に失敗（既に切断済みの可能性）")

    async def say_and_end(self, text: str, reason: str) -> None:
        await self.send_text(text)
        # 読み上げが終わる前に切らないよう少し待つ
        await asyncio.sleep(_estimate_speech_seconds(text))
        await self.send_end(reason)

    async def handle_user_utterance(self, text: str) -> bool:
        """ユーザー発話を処理する。通話を終える場合 True を返す。"""
        self.turns += 1
        self.silence_strikes = 0
        self.history.append({"role": "user", "content": text})
        await _db(_add_log, self.call_id, "user", text)

        judgement: Judgement = await judge_wakeup(self.history, self.wake_message)
        self.history.append({"role": "assistant", "content": judgement.reply})
        await _db(_add_log, self.call_id, "assistant", judgement.reply, judgement.decision)

        logger.info(
            "判定 call_id=%s decision=%s end_call=%s",
            self.call_id,
            judgement.decision,
            judgement.end_call,
        )

        if judgement.decision == "awake":
            self.outcome = "awake"
            await self.say_and_end(judgement.reply, "awake")
            return True

        if judgement.decision == "snooze":
            self.outcome = "snooze"
            await self.say_and_end(judgement.reply, "snooze")
            return True

        # unclear: 聞き直す。ただし往復が続きすぎたら打ち切る。
        if self.turns >= config.MAX_TURNS:
            self.outcome = "snooze"
            await self.say_and_end(
                f"うまく聞き取れないので、{config.SNOOZE_MINUTES}分後にまたかけますね。",
                "max_turns",
            )
            return True

        await self.send_text(judgement.reply)
        return False

    async def handle_silence(self) -> bool:
        """無音が続いたときの処理。通話を終える場合 True を返す。"""
        self.silence_strikes += 1
        logger.info(
            "無音を検知 call_id=%s strikes=%s", self.call_id, self.silence_strikes
        )

        if self.silence_strikes >= config.MAX_SILENCE_STRIKES:
            self.outcome = "no_answer"
            await _db(
                _add_log,
                self.call_id,
                "system",
                "応答がなかったため通話を終了しました。",
            )
            await self.say_and_end(
                f"応答がないので、{config.SNOOZE_MINUTES}分後にまたかけますね。",
                "no_answer",
            )
            return True

        await self.send_text("もしもし、聞こえていますか。起きていますか。")
        return False


async def handle_conversation_relay(websocket: WebSocket) -> None:
    params = websocket.query_params
    raw_call_id = params.get("call_id")
    token = params.get("token")

    if not raw_call_id or not token:
        await websocket.close(code=1008)
        return

    try:
        call_id = int(raw_call_id)
    except ValueError:
        await websocket.close(code=1008)
        return

    call = await _db(_load_call, call_id, token)
    if call is None:
        logger.warning("不正な ConversationRelay 接続 call_id=%s", raw_call_id)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("ConversationRelay 接続 call_id=%s", call_id)

    session = RelaySession(websocket, call_id, str(call["message"]))
    await _db(_mark_in_progress, call_id, None)

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=config.SILENCE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                if await session.handle_silence():
                    break
                continue

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("解釈できないメッセージ: %s", raw[:200])
                continue

            msg_type = message.get("type")

            if msg_type == "setup":
                await _db(_mark_in_progress, call_id, message.get("callSid"))
                continue

            if msg_type == "prompt":
                if not message.get("last", True):
                    continue  # 途中経過は無視して確定した発話だけ扱う
                text = (message.get("voicePrompt") or "").strip()
                if not text:
                    continue
                if await session.handle_user_utterance(text):
                    break
                continue

            if msg_type == "dtmf":
                digit = message.get("digit", "")
                if await session.handle_user_utterance(f"（プッシュ操作: {digit}）"):
                    break
                continue

            if msg_type == "interrupt":
                logger.debug("interrupt を受信 call_id=%s", call_id)
                continue

            if msg_type == "error":
                description = message.get("description", "")
                logger.error("ConversationRelay エラー call_id=%s: %s", call_id, description)
                await _db(_add_log, call_id, "system", f"ConversationRelay エラー: {description}")
                continue

            logger.debug("未対応のメッセージ種別: %s", msg_type)

    except WebSocketDisconnect:
        logger.info("ConversationRelay が切断されました call_id=%s", call_id)
    except Exception:
        logger.exception("ConversationRelay の処理で例外が発生 call_id=%s", call_id)
    finally:
        # ここは同期処理にしておく。await を挟むと切断時のタスクキャンセルで
        # 再架電の予約が失われることがあるため。
        _finish(session)


def _finish(session: RelaySession) -> None:
    """通話終了時の後始末。await を挟まないので途中でキャンセルされない。"""
    call_id = session.call_id
    outcome = session.outcome

    if outcome == "awake":
        _finalize(call_id, CallStatus.COMPLETED.value)
        return

    if outcome is None:
        # 判定が付かないまま切断された（相手が途中で切った等）→ 応答なし扱い
        outcome = "no_answer"
        _add_log(call_id, "system", "起床を確認できないまま通話が終了しました。")

    status = CallStatus.COMPLETED.value if outcome == "snooze" else CallStatus.NO_ANSWER.value
    _finalize(call_id, status)

    followup_id = scheduler.create_followup_call(call_id)
    if followup_id:
        logger.info("再架電を予約しました call_id=%s -> %s", call_id, followup_id)
