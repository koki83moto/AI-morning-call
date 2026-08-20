"""Telnyx TeXML API での発信と TeXML 生成。

TeXML は Twilio の TwiML 互換なので、文字列として組み立てる方針は据え置き。
属性値は必ずエスケープすること。

発信は TeXML Calls API を使う。Telnyx には 2 系統あり、どちらも同じボディを取る:

    POST /v2/texml/Accounts/{account_sid}/Calls   … 新しい形式。account_sid が必要
    POST /v2/texml/calls/{application_id}          … 旧形式。account_sid が不要

Account ID は Mission Control Portal に表示されない場合があるため、
TELNYX_ACCOUNT_SID が未設定なら自動的に後者へフォールバックする。
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx

from . import config

logger = logging.getLogger(__name__)

TELNYX_API_BASE = "https://api.telnyx.com/v2"

# 発信 API のタイムアウト（秒）。ここで長く待つとジョブが詰まるので短めにする。
_REQUEST_TIMEOUT = 30.0


def _calls_request() -> tuple[str, dict[str, str]]:
    """発信先の URL と、パスに含められない追加ボディを返す。"""
    if config.TELNYX_ACCOUNT_SID:
        return (
            f"{TELNYX_API_BASE}/texml/Accounts/{config.TELNYX_ACCOUNT_SID}/Calls",
            {"ApplicationSid": config.TELNYX_APPLICATION_SID},
        )
    # account_sid が分からない場合はアプリ ID をパスに含める旧形式を使う
    return f"{TELNYX_API_BASE}/texml/calls/{config.TELNYX_APPLICATION_SID}", {}


def voice_webhook_url(call_id: int, token: str) -> str:
    query = urlencode({"token": token})
    return f"{config.APP_BASE_URL}/telnyx/voice/{call_id}?{query}"


def status_webhook_url(call_id: int, token: str) -> str:
    query = urlencode({"token": token})
    return f"{config.APP_BASE_URL}/telnyx/status/{call_id}?{query}"


def relay_websocket_url(call_id: int, token: str) -> str:
    query = urlencode({"call_id": call_id, "token": token})
    return f"{config.websocket_base_url()}/conversation-relay?{query}"


def build_say_texml(message: str) -> str:
    """v1: 固定メッセージを読み上げるだけの TeXML。

    Telnyx の <Say> は voice を指定しないと既定の英語音声になり、language 属性は
    無視される（language が効くのは voice="alice" のときだけ）。
    日本語を読ませるには TELNYX_VOICE に ja-JP の音声 ID を設定すること。
    """
    body = escape(message)
    attrs = f"language={quoteattr(config.TELNYX_LANGUAGE)}"
    if config.TELNYX_VOICE:
        attrs += f" voice={quoteattr(config.TELNYX_VOICE)}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say {attrs}>{body}</Say>"
        '<Pause length="1"/>'
        f"<Say {attrs}>{body}</Say>"
        "</Response>"
    )


def relay_voice() -> str:
    """ConversationRelay で使う音声 ID。専用の設定がなければ <Say> 用を流用する。"""
    return config.TELNYX_RELAY_VOICE or config.TELNYX_VOICE


def build_conversation_relay_texml(call_id: int, token: str, greeting: str) -> str:
    """v2: ConversationRelay に接続する TeXML。

    注意: speechModel はトップレベル属性ではなく <Language> 子要素に置く。
    トップレベルに書くと設定全体が効かず、音声がまったく鳴らないことがある。
    """
    voice = relay_voice()

    attributes = [
        f"url={quoteattr(relay_websocket_url(call_id, token))}",
        f"language={quoteattr(config.TELNYX_LANGUAGE)}",
        f"welcomeGreeting={quoteattr(greeting)}",
    ]
    if config.TELNYX_TRANSCRIPTION_PROVIDER:
        attributes.append(
            f"transcriptionProvider={quoteattr(config.TELNYX_TRANSCRIPTION_PROVIDER)}"
        )
    if config.TELNYX_TTS_PROVIDER:
        attributes.append(f"ttsProvider={quoteattr(config.TELNYX_TTS_PROVIDER)}")
    if voice:
        attributes.append(f"voice={quoteattr(voice)}")

    # 言語ごとの詳細指定。文字起こしモデルはここでしか指定できない。
    lang_attrs = [f"code={quoteattr(config.TELNYX_LANGUAGE)}"]
    if voice:
        lang_attrs.append(f"voice={quoteattr(voice)}")
    if config.TELNYX_TRANSCRIPTION_PROVIDER:
        lang_attrs.append(
            f"transcriptionProvider={quoteattr(config.TELNYX_TRANSCRIPTION_PROVIDER)}"
        )
    if config.TELNYX_SPEECH_MODEL:
        lang_attrs.append(f"speechModel={quoteattr(config.TELNYX_SPEECH_MODEL)}")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f"<ConversationRelay {' '.join(attributes)}>"
        f"<Language {' '.join(lang_attrs)}/>"
        "</ConversationRelay>"
        "</Connect>"
        "</Response>"
    )


class CallPlacementError(RuntimeError):
    pass


def _extract_sid(payload: object) -> str | None:
    """レスポンスから CallSid を取り出す。キー名の揺れを吸収する。"""
    if not isinstance(payload, dict):
        return None
    # Telnyx は data でラップして返す場合がある
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(body, dict):
        return None
    for key in ("sid", "call_sid", "CallSid", "call_control_id", "id"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def place_call(*, to: str, call_id: int, token: str) -> str | None:
    """発信して Telnyx の CallSid を返す。失敗時は CallPlacementError。

    SID が取れなかった場合でも発信自体は成功しているので None を返して続行する。
    """
    endpoint, extra = _calls_request()
    payload = {
        "To": to,
        "From": config.TELNYX_PHONE_NUMBER,
        "Url": voice_webhook_url(call_id, token),
        "StatusCallback": status_webhook_url(call_id, token),
        "StatusCallbackMethod": "POST",
        **extra,
    }
    headers = {
        "Authorization": f"Bearer {config.TELNYX_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = httpx.post(
            endpoint, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT
        )
    except httpx.HTTPError as exc:  # ネットワーク断・タイムアウトなど
        logger.exception("Telnyx 発信で通信エラー (call_id=%s)", call_id)
        raise CallPlacementError(str(exc)) from exc

    if response.status_code >= 400:
        # エラー本文には原因（残高不足・国際発信の未許可など）が入るので残す
        detail = response.text[:1000]
        logger.error(
            "Telnyx 発信に失敗しました (call_id=%s) status=%s body=%s",
            call_id,
            response.status_code,
            detail,
        )
        raise CallPlacementError(f"HTTP {response.status_code}: {detail}")

    try:
        sid = _extract_sid(response.json())
    except ValueError:
        sid = None

    logger.info("発信しました call_id=%s sid=%s", call_id, sid)
    return sid
