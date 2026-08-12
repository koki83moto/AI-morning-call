"""SQLite への接続とセッション管理。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from . import config

_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(config.DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    from .models import Base  # 循環 import を避けるため関数内で読む

    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """コミット／ロールバックを面倒みるセッションコンテキスト。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI の依存性注入用。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
