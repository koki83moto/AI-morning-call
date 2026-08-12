"""リクエスト／レスポンスのスキーマ。"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class ScheduleCallRequest(BaseModel):
    phone_number: str = Field(..., examples=["+819012345678"])
    call_at: datetime = Field(..., examples=["2026-08-13T07:00:00+09:00"])
    message: str | None = Field(default=None, examples=["おはよう、起きる時間だよ"])
    use_conversation: bool = True

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        v = v.strip().replace("-", "").replace(" ", "")
        if not E164.match(v):
            raise ValueError("phone_number は E.164 形式で指定してください（例: +819012345678）")
        return v

    @field_validator("call_at")
    @classmethod
    def _require_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "call_at にはタイムゾーンを付けてください（例: 2026-08-13T07:00:00+09:00）"
            )
        return v


class ChatScheduleRequest(BaseModel):
    text: str = Field(..., examples=["明日の7時に優しく起こして"])
    phone_number: str | None = None

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().replace("-", "").replace(" ", "")
        if not E164.match(v):
            raise ValueError("phone_number は E.164 形式で指定してください（例: +819012345678）")
        return v


class PhoneSettingRequest(BaseModel):
    phone_number: str

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        v = v.strip().replace("-", "").replace(" ", "")
        if not E164.match(v):
            raise ValueError("phone_number は E.164 形式で指定してください（例: +819012345678）")
        return v
