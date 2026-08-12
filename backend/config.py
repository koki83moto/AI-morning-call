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


# --- Twilio ---------------------------------------------------------------
TWILIO_ACCOUNT_SID = _get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = _get("TWILIO_PHONE_NUMBER")

# ConversationRelay の音声設定。空文字なら属性自体を出力せず Twilio の既定に任せる。
TWILIO_TTS_PROVIDER = _get("TWILIO_TTS_PROVIDER")
TWILIO_VOICE = _get("TWILIO_VOICE")

# Twilio Webhook の署名検証。ngrok 経由の URL が一致しない等で困るときだけ false にする。
TWILIO_VALIDATE_SIGNATURE = _get_bool("TWILIO_VALIDATE_SIGNATURE", True)

# --- Anthropic ------------------------------------------------------------
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
# 要件定義書に従い既定は Haiku 4.5。精度不足なら claude-sonnet-5 等に差し替える。
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-haiku-4-5")

# --- アプリ ---------------------------------------------------------------
# Twilio の Webhook が到達できる公開 URL（ngrok または本番 URL）
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
    required = {
        "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
        "TWILIO_PHONE_NUMBER": TWILIO_PHONE_NUMBER,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "APP_BASE_URL": APP_BASE_URL,
        "API_KEY": API_KEY,
    }
    return [name for name, value in required.items() if not value]
