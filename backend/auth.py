"""認証まわり。

- 管理 API: `X-API-Key` ヘッダ（`.env` の API_KEY）
- Twilio Webhook: Twilio 署名検証 + 通話ごとのトークン
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status
from twilio.request_validator import RequestValidator

from . import config


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY が未設定です。.env を確認してください。",
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key が不正です。",
        )


def verify_call_token(expected: str, provided: str | None) -> None:
    if not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="token が不正です。")


async def verify_twilio_signature(request: Request) -> None:
    """Twilio からの POST であることを署名で検証する。

    ngrok の URL 書き換えなどで検証が通らない場合は
    TWILIO_VALIDATE_SIGNATURE=false で無効化できる（通話トークンによる保護は残る）。
    """
    if not config.TWILIO_VALIDATE_SIGNATURE:
        return

    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        raise HTTPException(status_code=403, detail="X-Twilio-Signature がありません。")

    # Twilio は APP_BASE_URL 側の URL で署名しているので、そちらを基準に組み立てる
    url = f"{config.APP_BASE_URL}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    if not validator.validate(url, params, signature):
        raise HTTPException(status_code=403, detail="Twilio 署名の検証に失敗しました。")
