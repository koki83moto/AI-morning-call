"""認証まわり。

- 管理 API: `X-API-Key` ヘッダ、またはログインで発行されるセッション Cookie
- Telnyx Webhook: 通話ごとのトークン（＋任意で Ed25519 署名検証）

Cookie は API_KEY を鍵にした HMAC 署名付きの文字列で、サーバ側に状態を持たない。
API_KEY を変えれば発行済みの Cookie は全て無効になる。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

from fastapi import Cookie, Header, HTTPException, Request, status

from . import config

logger = logging.getLogger(__name__)

SESSION_COOKIE = "morning_call_session"


def _sign(payload: str) -> str:
    return hmac.new(config.API_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_session_token(ttl_seconds: int | None = None) -> str:
    """`有効期限.署名` 形式のセッショントークンを作る。"""
    ttl = config.SESSION_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    expires_at = str(int(time.time()) + ttl)
    return f"{expires_at}.{_sign(expires_at)}"


def session_token_valid(token: str | None) -> bool:
    if not token or not config.API_KEY:
        return False
    expires_at, _, signature = token.partition(".")
    if not signature:
        return False
    if not hmac.compare_digest(signature, _sign(expires_at)):
        return False
    try:
        return int(expires_at) > time.time()
    except ValueError:
        return False


def cookie_secure(request: Request) -> bool:
    """HTTPS で来たリクエストにだけ Secure 属性を付ける。

    APP_BASE_URL で判定すると、ngrok の https URL を設定したまま
    http://localhost で開発したときに Cookie が返らなくなる。
    リバースプロキシ配下では X-Forwarded-Proto を見る。
    """
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip() == "https"


def is_authenticated(
    x_api_key: str | None = None, session: str | None = None
) -> bool:
    if x_api_key and config.API_KEY and hmac.compare_digest(x_api_key, config.API_KEY):
        return True
    return session_token_valid(session)


async def require_api_key(
    x_api_key: str | None = Header(default=None),
    morning_call_session: str | None = Cookie(default=None),
) -> None:
    if not config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY が未設定です。.env を確認してください。",
        )
    if not is_authenticated(x_api_key, morning_call_session):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要です（X-API-Key ヘッダ、またはログイン）。",
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
