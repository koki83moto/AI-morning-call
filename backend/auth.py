"""認証まわり。

- 管理 API: `X-API-Key` ヘッダ（`.env` の API_KEY）
- Telnyx Webhook: 通話ごとのトークン（＋任意で Ed25519 署名検証）
"""

from __future__ import annotations

import base64
import hmac
import logging
import time

from fastapi import Header, HTTPException, Request, status

from . import config

logger = logging.getLogger(__name__)


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


async def verify_telnyx_signature(request: Request) -> None:
    """Telnyx からの POST であることを Ed25519 署名で検証する。

    Telnyx は `{timestamp}|{リクエストボディ}` に対する署名を
    telnyx-signature-ed25519 ヘッダ（base64）に入れて送る。

    TeXML の Webhook に署名が付くかは環境によって異なるため、既定では無効
    （TELNYX_VALIDATE_SIGNATURE=false）。無効でも通話ごとのトークンで保護される。
    """
    if not config.TELNYX_VALIDATE_SIGNATURE:
        return

    if not config.TELNYX_PUBLIC_KEY:
        raise HTTPException(
            status_code=500,
            detail="TELNYX_VALIDATE_SIGNATURE=true ですが TELNYX_PUBLIC_KEY が未設定です。",
        )

    signature = request.headers.get("telnyx-signature-ed25519")
    timestamp = request.headers.get("telnyx-timestamp")
    if not signature or not timestamp:
        raise HTTPException(status_code=403, detail="Telnyx の署名ヘッダがありません。")

    # 古い署名の使い回しを防ぐ
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        raise HTTPException(status_code=403, detail="telnyx-timestamp が不正です。") from None
    if age > config.TELNYX_SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(status_code=403, detail="Telnyx の署名が古すぎます。")

    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="署名検証には PyNaCl が必要です。pip install PyNaCl を実行してください。",
        ) from exc

    body = await request.body()
    signed_payload = timestamp.encode() + b"|" + body

    try:
        verify_key = VerifyKey(base64.b64decode(config.TELNYX_PUBLIC_KEY))
        verify_key.verify(signed_payload, base64.b64decode(signature))
    except BadSignatureError:
        raise HTTPException(status_code=403, detail="Telnyx 署名の検証に失敗しました。") from None
    except Exception as exc:  # 鍵や署名の形式が壊れている場合
        logger.error("Telnyx 署名検証で例外: %s", exc)
        raise HTTPException(status_code=403, detail="Telnyx 署名を検証できませんでした。") from exc
