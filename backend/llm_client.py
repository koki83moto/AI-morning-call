"""Claude API 呼び出し。

- `judge_wakeup`: 通話中の返答から「起きたか」を判定し、読み上げる返事も同時に作る
- `parse_schedule_request`: チャット入力から予約日時とメッセージを構造化抽出

どちらも tool use（`tool_choice` でツールを強制）で JSON を受け取る。
モデルは config.ANTHROPIC_MODEL（既定 claude-haiku-4-5）。精度が足りなければ
.env の ANTHROPIC_MODEL を claude-sonnet-5 などに変えるだけで切り替えられる。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import anthropic

from . import config

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# --- 起床判定 --------------------------------------------------------------

JUDGE_SYSTEM = """あなたは電話でモーニングコールをかけるアシスタントです。
相手が本当に起きたかどうかを、返答の内容から判定してください。

判定基準:
- awake: はっきり起きたと分かる返答（「起きた」「もう起きてる」「大丈夫」「ありがとう」など）
- snooze: まだ眠そう、あと少し寝たい、生返事（「うーん」「あと5分」「まだ眠い」など）
- unclear: 意味が取れない、聞き取れていない、無関係な内容

返事(reply)は日本語で、電話で読み上げる前提の短い口語にしてください（1〜2文）。
- awake のときはお礼を言って通話を終える内容にし、end_call を true にする
- snooze のときは「じゃあ5分後にまたかけるね」と伝えて通話を終える内容にし、end_call を true にする
- unclear のときはもう一度優しく聞き直す内容にし、end_call は false にする
"""

JUDGE_TOOL = {
    "name": "report_wakeup_judgement",
    "description": "相手が起きたかどうかの判定結果と、電話で読み上げる返事を報告する。",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["awake", "snooze", "unclear"],
                "description": "起床判定の結果",
            },
            "reply": {
                "type": "string",
                "description": "電話で読み上げる日本語の返事（1〜2文の口語）",
            },
            "end_call": {
                "type": "boolean",
                "description": "この返事のあとに通話を終了するかどうか",
            },
        },
        "required": ["decision", "reply", "end_call"],
    },
}


@dataclass
class Judgement:
    decision: str
    reply: str
    end_call: bool


def _fallback_judgement() -> Judgement:
    return Judgement(
        decision="unclear",
        reply="ごめんなさい、うまく聞き取れませんでした。もう一度お願いします。",
        end_call=False,
    )


def _extract_tool_input(response, tool_name: str) -> dict | None:
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return dict(block.input)
    return None


async def judge_wakeup(history: list[dict], wake_message: str) -> Judgement:
    """会話履歴から起床判定と返事を得る。

    history は [{"role": "user"|"assistant", "content": "..."}] の配列。
    """
    system = JUDGE_SYSTEM + f"\n\n今回のモーニングコールの主旨: {wake_message}"

    try:
        response = await get_client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=512,
            system=system,
            messages=history,
            tools=[JUDGE_TOOL],
            tool_choice={"type": "tool", "name": JUDGE_TOOL["name"]},
        )
    except anthropic.APIError as exc:
        logger.exception("Claude API 呼び出しに失敗しました: %s", exc)
        return _fallback_judgement()

    payload = _extract_tool_input(response, JUDGE_TOOL["name"])
    if not payload:
        logger.warning("Claude から tool_use ブロックが返りませんでした")
        return _fallback_judgement()

    decision = str(payload.get("decision", "unclear"))
    if decision not in {"awake", "snooze", "unclear"}:
        decision = "unclear"
    reply = str(payload.get("reply") or "").strip() or _fallback_judgement().reply
    end_call = bool(payload.get("end_call", decision != "unclear"))
    return Judgement(decision=decision, reply=reply, end_call=end_call)


# --- チャットからの予約抽出 (v3) -------------------------------------------

SCHEDULE_SYSTEM = """あなたはモーニングコール予約アシスタントです。
ユーザーの日本語の文章から、電話をかける日時と、電話で伝えるメッセージを抽出します。

ルール:
- 日時は必ず ISO 8601 形式・タイムゾーン付きで出力する（例: 2026-08-13T07:00:00+09:00）
- 「明日の7時」のような相対表現は、与えられた現在時刻を基準に解釈する
- 時刻だけ指定された場合、その時刻が現在より過去なら翌日として解釈する
- 「朝7時」は 07:00、「夜9時」は 21:00 のように、日本語の常識に沿って解釈する
- メッセージが指定されていなければ、その場面に合う自然な日本語の呼びかけを考えて入れる
- 日時が読み取れない場合は understood=false にし、reply で何を聞きたいか短く尋ねる
- reply は必ず日本語で、予約が成立したときは「いつ・どんな内容で予約したか」を1〜2文で伝える
"""

SCHEDULE_TOOL = {
    "name": "register_morning_call",
    "description": "抽出したモーニングコールの予約内容を登録する。",
    "input_schema": {
        "type": "object",
        "properties": {
            "understood": {
                "type": "boolean",
                "description": "日時を特定できたかどうか",
            },
            "call_at": {
                "type": "string",
                "description": "発信日時。ISO 8601・タイムゾーン付き（例: 2026-08-13T07:00:00+09:00）",
            },
            "message": {
                "type": "string",
                "description": "電話で伝える日本語のメッセージ",
            },
            "reply": {
                "type": "string",
                "description": "ユーザーへ返すチャットの返事（日本語）",
            },
        },
        "required": ["understood", "reply"],
    },
}


@dataclass
class ParsedSchedule:
    understood: bool
    call_at: str | None
    message: str | None
    reply: str


async def parse_schedule_request(text: str, now: datetime) -> ParsedSchedule:
    system = (
        SCHEDULE_SYSTEM
        + f"\n\n現在時刻: {now.isoformat()}（タイムゾーン: {config.TIMEZONE}）"
    )

    try:
        response = await get_client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": text}],
            tools=[SCHEDULE_TOOL],
            tool_choice={"type": "tool", "name": SCHEDULE_TOOL["name"]},
        )
    except anthropic.APIError as exc:
        logger.exception("Claude API 呼び出しに失敗しました: %s", exc)
        return ParsedSchedule(
            understood=False,
            call_at=None,
            message=None,
            reply="AI への問い合わせに失敗しました。少し待ってからもう一度お試しください。",
        )

    payload = _extract_tool_input(response, SCHEDULE_TOOL["name"])
    if not payload:
        return ParsedSchedule(
            understood=False,
            call_at=None,
            message=None,
            reply="うまく解釈できませんでした。「明日の7時に起こして」のように教えてください。",
        )

    understood = bool(payload.get("understood"))
    call_at = payload.get("call_at")
    message = payload.get("message")
    reply = str(payload.get("reply") or "").strip() or "予約内容を確認してください。"
    return ParsedSchedule(
        understood=understood,
        call_at=str(call_at) if call_at else None,
        message=str(message) if message else None,
        reply=reply,
    )
