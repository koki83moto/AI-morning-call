"""Twilio Voice API での発信と TwiML 生成。

TwiML は Twilio SDK のヘルパー（バージョンによって ConversationRelay 対応が異なる）に
依存せず、文字列として組み立てる。属性値は必ずエスケープすること。
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from . import config

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def voice_webhook_url(call_id: int, token: str) -> str:
    query = urlencode({"token": token})
    return f"{config.APP_BASE_URL}/twilio/voice/{call_id}?{query}"


def status_webhook_url(call_id: int, token: str) -> str:
    query = urlencode({"token": token})
    return f"{config.APP_BASE_URL}/twilio/status/{call_id}?{query}"


def relay_websocket_url(call_id: int, token: str) -> str:
    query = urlencode({"call_id": call_id, "token": token})
    return f"{config.websocket_base_url()}/conversation-relay?{query}"


def build_say_twiml(message: str) -> str:
    """v1: 固定メッセージを読み上げるだけの TwiML。"""
    body = escape(message)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say language="ja-JP">{body}</Say>'
        '<Pause length="1"/>'
        f'<Say language="ja-JP">{body}</Say>'
        "</Response>"
    )


def build_conversation_relay_twiml(call_id: int, token: str, greeting: str) -> str:
    """v2: ConversationRelay に接続する TwiML。"""
    attributes = [
        f"url={quoteattr(relay_websocket_url(call_id, token))}",
        'language="ja-JP"',
        f"welcomeGreeting={quoteattr(greeting)}",
    ]
    if config.TWILIO_TTS_PROVIDER:
        attributes.append(f"ttsProvider={quoteattr(config.TWILIO_TTS_PROVIDER)}")
    if config.TWILIO_VOICE:
        attributes.append(f"voice={quoteattr(config.TWILIO_VOICE)}")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f"<ConversationRelay {' '.join(attributes)}/>"
        "</Connect>"
        "</Response>"
    )


class CallPlacementError(RuntimeError):
    pass


def place_call(*, to: str, call_id: int, token: str) -> str:
    """発信して Twilio の CallSid を返す。失敗時は CallPlacementError。"""
    try:
        call = get_client().calls.create(
            to=to,
            from_=config.TWILIO_PHONE_NUMBER,
            url=voice_webhook_url(call_id, token),
            method="POST",
            status_callback=status_webhook_url(call_id, token),
            status_callback_method="POST",
            status_callback_event=["completed"],
            machine_detection="Enable",
        )
    except TwilioRestException as exc:
        logger.error("Twilio 発信に失敗しました (call_id=%s): %s", call_id, exc)
        raise CallPlacementError(str(exc)) from exc
    except Exception as exc:  # ネットワーク断など
        logger.exception("Twilio 発信で予期しないエラー (call_id=%s)", call_id)
        raise CallPlacementError(str(exc)) from exc

    logger.info("発信しました call_id=%s sid=%s", call_id, call.sid)
    return call.sid
