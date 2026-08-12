"""APScheduler による発信ジョブの管理。

ジョブはメモリ上にのみ保持し、正しい状態は SQLite が持つ。
起動時に DB を読み直して未来の予約だけ再登録する（過去のものは missed 扱いにして
再発信しない ＝ 無限リトライで課金が膨らむのを防ぐ）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from . import config, twilio_client
from .db import session_scope
from .models import Call, CallStatus, ConversationLog, utcnow

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=timezone.utc)


def job_id(call_id: int) -> str:
    return f"call-{call_id}"


def start() -> None:
    if not scheduler.running:
        scheduler.start()
        logger.info("スケジューラを起動しました")


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("スケジューラを停止しました")


def schedule_call(call_id: int, call_at_utc: datetime) -> None:
    """指定時刻に発信ジョブを登録する。call_at_utc は naive UTC。"""
    run_date = call_at_utc.replace(tzinfo=timezone.utc)
    scheduler.add_job(
        _fire,
        trigger="date",
        run_date=run_date,
        args=[call_id],
        id=job_id(call_id),
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info("発信ジョブを登録しました call_id=%s run_date=%s", call_id, run_date.isoformat())


def cancel_job(call_id: int) -> None:
    try:
        scheduler.remove_job(job_id(call_id))
    except Exception:
        # 既に実行済み／未登録なら何もしなくてよい
        pass


def reload_pending_calls() -> None:
    """起動時に DB から予約を読み直す。"""
    now = utcnow()
    with session_scope() as session:
        pending = session.scalars(
            select(Call).where(Call.status == CallStatus.SCHEDULED.value)
        ).all()

        missed = 0
        restored = 0
        for call in pending:
            if call.call_at <= now:
                call.status = CallStatus.MISSED.value
                call.error = "サーバ停止中に予定時刻を過ぎたため発信しませんでした。"
                missed += 1
            else:
                schedule_call(call.id, call.call_at)
                restored += 1

    if missed or restored:
        logger.info("予約を復元しました: 再登録=%s 期限切れ=%s", restored, missed)


def _fire(call_id: int) -> None:
    """予定時刻に呼ばれる発信ジョブ本体。"""
    with session_scope() as session:
        call = session.get(Call, call_id)
        if call is None:
            logger.warning("発信対象が見つかりません call_id=%s", call_id)
            return
        if call.status != CallStatus.SCHEDULED.value:
            logger.info(
                "status=%s のため発信をスキップします call_id=%s", call.status, call_id
            )
            return

        to = call.phone_number
        token = call.token
        call.status = CallStatus.DIALING.value

    try:
        sid = twilio_client.place_call(to=to, call_id=call_id, token=token)
    except twilio_client.CallPlacementError as exc:
        # 要件どおり自動リトライはしない。ログと DB に残して終わる。
        with session_scope() as session:
            failed = session.get(Call, call_id)
            if failed is not None:
                failed.status = CallStatus.FAILED.value
                failed.error = str(exc)[:2000]
        logger.error("発信に失敗しました call_id=%s: %s", call_id, exc)
        return

    with session_scope() as session:
        dialed = session.get(Call, call_id)
        if dialed is not None:
            dialed.twilio_call_sid = sid


def create_followup_call(parent_call_id: int, *, minutes: int | None = None) -> int | None:
    """再架電を予約する。上限に達している場合は None を返す。"""
    delay = config.SNOOZE_MINUTES if minutes is None else minutes

    with session_scope() as session:
        parent = session.get(Call, parent_call_id)
        if parent is None:
            return None

        if parent.retry_count >= config.MAX_RETRIES:
            logger.info(
                "再架電の上限 (%s回) に達したため予約しません call_id=%s",
                config.MAX_RETRIES,
                parent_call_id,
            )
            session.add(
                ConversationLog(
                    call_id=parent_call_id,
                    role="system",
                    content=f"再架電の上限（{config.MAX_RETRIES}回）に達したため打ち切りました。",
                )
            )
            return None

        next_at = utcnow() + timedelta(minutes=delay)
        followup = Call(
            phone_number=parent.phone_number,
            call_at=next_at,
            message=parent.message,
            status=CallStatus.SCHEDULED.value,
            retry_count=parent.retry_count + 1,
            parent_call_id=parent.id,
            use_conversation=parent.use_conversation,
        )
        session.add(followup)
        session.flush()

        followup_id = followup.id
        session.add(
            ConversationLog(
                call_id=parent_call_id,
                role="system",
                content=f"{delay}分後に再架電を予約しました（call_id={followup_id}）。",
            )
        )

    schedule_call(followup_id, next_at)
    return followup_id
