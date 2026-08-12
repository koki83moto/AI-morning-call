"""SQLAlchemy モデル。日時はすべて UTC の naive datetime で保存する。"""

from __future__ import annotations

import enum
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token() -> str:
    return secrets.token_urlsafe(24)


class CallStatus(str, enum.Enum):
    SCHEDULED = "scheduled"      # 予約済み・発信待ち
    DIALING = "dialing"          # Twilio に発信を依頼した
    IN_PROGRESS = "in_progress"  # 通話中（ConversationRelay 接続済み）
    COMPLETED = "completed"      # 起床確認できて正常終了
    NO_ANSWER = "no_answer"      # 応答なし／無音
    FAILED = "failed"            # 発信エラー
    MISSED = "missed"            # サーバ停止中に予定時刻を過ぎた
    CANCELED = "canceled"        # ユーザーがキャンセル


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(32))
    call_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC naive
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=CallStatus.SCHEDULED.value, index=True)

    # Twilio の Webhook / WebSocket を認証するためのワンタイム的なトークン
    token: Mapped[str] = mapped_column(String(64), default=new_token, index=True)

    twilio_call_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 再架電の連鎖を追うための情報
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    parent_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )

    # v2 の会話ロジックを使うか（False なら固定メッセージを読み上げるだけ）
    use_conversation: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    logs: Mapped[list["ConversationLog"]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="ConversationLog.id"
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "phone_number": self.phone_number,
            "call_at": self.call_at.replace(tzinfo=timezone.utc).isoformat(),
            "message": self.message,
            "status": self.status,
            "twilio_call_sid": self.twilio_call_sid,
            "error": self.error,
            "retry_count": self.retry_count,
            "parent_call_id": self.parent_call_id,
            "use_conversation": self.use_conversation,
            "created_at": self.created_at.replace(tzinfo=timezone.utc).isoformat(),
        }


class ConversationLog(Base):
    """通話中の発話と判定結果。外部には送らずローカル DB にのみ保存する。"""

    __tablename__ = "conversation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant / system
    content: Mapped[str] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)  # awake / snooze / unclear
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    call: Mapped[Call] = relationship(back_populates="logs")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "call_id": self.call_id,
            "role": self.role,
            "content": self.content,
            "decision": self.decision,
            "created_at": self.created_at.replace(tzinfo=timezone.utc).isoformat(),
        }


class Setting(Base):
    """電話番号などの単純な永続設定（v3 の UI 用）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
