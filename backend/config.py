"""環境変数の読み込みとアプリ全体の設定値。

`.env` をリポジトリルートに置いておけば自動で読み込まれる。
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# --- Telnyx ---------------------------------------------------------------
TELNYX_API_KEY = _get("TELNYX_API_KEY")

# 任意。設定すると /v2/texml/Accounts/{account_sid}/Calls を使う。
# Mission Control Portal に表示されないことがあるため、空なら
# /v2/texml/calls/{application_id}（account_sid 不要の旧形式）にフォールバックする。
TELNYX_ACCOUNT_SID = _get("TELNYX_ACCOUNT_SID")

# TeXML Application の ID。発信時の ApplicationSid として渡す。
TELNYX_APPLICATION_SID = _get("TELNYX_APPLICATION_SID")

TELNYX_PHONE_NUMBER = _get("TELNYX_PHONE_NUMBER")

# <Say>（v1）の読み上げ音声。空だと英語音声になるので ja-JP の音声 ID を入れる。
TELNYX_TTS_PROVIDER = _get("TELNYX_TTS_PROVIDER")
TELNYX_VOICE = _get("TELNYX_VOICE")

# ConversationRelay（v2）の読み上げ音声。<Say> とは別系統で、
# Telnyx がホストしている音声（Telnyx.*）でないと鳴らないことがあるため分けている。
# 空なら TELNYX_VOICE を流用する。
TELNYX_RELAY_VOICE = _get("TELNYX_RELAY_VOICE")

# 文字起こしの提供元とモデル。日本語は Deepgram の場合 flux が非対応なので nova-3 を使う。
# speechModel は <ConversationRelay> のトップレベル属性ではなく <Language> 子要素に置く。
TELNYX_TRANSCRIPTION_PROVIDER = _get("TELNYX_TRANSCRIPTION_PROVIDER", "deepgram")
TELNYX_SPEECH_MODEL = _get("TELNYX_SPEECH_MODEL", "nova-3")

# ConversationRelay に渡す言語コード
TELNYX_LANGUAGE = _get("TELNYX_LANGUAGE", "ja-JP")

# Webhook の Ed25519 署名検証。Mission Control Portal の
# Keys & Credentials → Public Key に表示される base64 の公開鍵。
TELNYX_PUBLIC_KEY = _get("TELNYX_PUBLIC_KEY")

# TeXML の Webhook が Ed25519 署名付きで届くかは環境によって異なるため既定は無効。
# 通話ごとのトークン（?token=...）による保護は常に有効。
TELNYX_VALIDATE_SIGNATURE = _get_bool("TELNYX_VALIDATE_SIGNATURE", False)

# 署名のタイムスタンプが古すぎるものは弾く（リプレイ対策）
TELNYX_SIGNATURE_TOLERANCE_SECONDS = _get_int("TELNYX_SIGNATURE_TOLERANCE_SECONDS", 300)

# --- Anthropic ------------------------------------------------------------
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
# 要件定義書に従い既定は Haiku 4.5。精度不足なら claude-sonnet-5 等に差し替える。
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-haiku-4-5")

# --- アプリ ---------------------------------------------------------------
# Telnyx の Webhook が到達できる公開 URL（ngrok または本番 URL）
APP_BASE_URL = _get("APP_BASE_URL").rstrip("/")

# 管理 API を守る固定 APIキー
API_KEY = _get("API_KEY")

TIMEZONE = _get("TIMEZONE", "Asia/Tokyo")
TZ = ZoneInfo(TIMEZONE)

DATABASE_URL = _get("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'morning_call.db').as_posix()}")

# 再架電の間隔（分）と上限回数。上限は課金暴走を防ぐためのストッパー。
SNOOZE_MINUTES = _get_int("SNOOZE_MINUTES", 5)
MAX_RETRIES = _get_int("MAX_RETRIES", 3)

# ConversationRelay の無音判定
SILENCE_TIMEOUT_SECONDS = _get_int("SILENCE_TIMEOUT_SECONDS", 15)
MAX_SILENCE_STRIKES = _get_int("MAX_SILENCE_STRIKES", 2)

# 1通話あたりの最大やり取り回数（無限ループ防止）
MAX_TURNS = _get_int("MAX_TURNS", 8)

DEFAULT_MESSAGE = _get("DEFAULT_MESSAGE", "おはようございます。起きる時間ですよ。")


def websocket_base_url() -> str:
    """APP_BASE_URL を ws/wss スキームに変換したもの。"""
    if APP_BASE_URL.startswith("https://"):
        return "wss://" + APP_BASE_URL[len("https://") :]
    if APP_BASE_URL.startswith("http://"):
        return "ws://" + APP_BASE_URL[len("http://") :]
    return APP_BASE_URL


def missing_required() -> list[str]:
    """起動時チェック用。未設定の必須環境変数名を返す。"""
    # TELNYX_ACCOUNT_SID は任意（未設定なら account_sid 不要の形式で発信する）
    required = {
        "TELNYX_API_KEY": TELNYX_API_KEY,
        "TELNYX_APPLICATION_SID": TELNYX_APPLICATION_SID,
        "TELNYX_PHONE_NUMBER": TELNYX_PHONE_NUMBER,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "APP_BASE_URL": APP_BASE_URL,
        "API_KEY": API_KEY,
    }
    return [name for name, value in required.items() if not value]
