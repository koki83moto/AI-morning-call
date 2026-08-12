"""FastAPI エントリポイント。

起動:
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, scheduler, twilio_client
from .auth import require_api_key, verify_call_token, verify_twilio_signature
from .conversation import handle_conversation_relay
from .db import get_session, init_db
from .llm_client import parse_schedule_request
from .models import Call, CallStatus, ConversationLog, Setting, utcnow
from .schemas import ChatScheduleRequest, PhoneSettingRequest, ScheduleCallRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("morning_call")

PHONE_SETTING_KEY = "phone_number"


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing_required()
    if missing:
        logger.warning(
            "未設定の環境変数があります: %s（.env.example を参照してください）",
            ", ".join(missing),
        )

    init_db()
    scheduler.start()
    scheduler.reload_pending_calls()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="AI モーニングコール",
    description="指定した時刻に AI が電話をかけて起こしてくれる個人向け PoC。",
    version="1.0.0",
    lifespan=lifespan,
)

FRONTEND_DIR = config.BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# --- 基本 -----------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index():
    index_html = FRONTEND_DIR / "index.html"
    if index_html.exists():
        return FileResponse(index_html)
    return RedirectResponse("/docs")


@app.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "now": datetime.now(config.TZ).isoformat(),
        "timezone": config.TIMEZONE,
        "model": config.ANTHROPIC_MODEL,
        "scheduled_jobs": len(scheduler.scheduler.get_jobs()),
        "missing_env": config.missing_required(),
    }


# --- v1: 予約と一覧 --------------------------------------------------------


@app.post("/calls/schedule", tags=["calls"], dependencies=[Depends(require_api_key)])
async def schedule_call(payload: ScheduleCallRequest, session: Session = Depends(get_session)):
    call_at_utc = payload.call_at.astimezone(timezone.utc).replace(tzinfo=None)
    if call_at_utc <= utcnow():
        raise HTTPException(status_code=400, detail="call_at は未来の日時を指定してください。")

    call = Call(
        phone_number=payload.phone_number,
        call_at=call_at_utc,
        message=(payload.message or config.DEFAULT_MESSAGE).strip(),
        status=CallStatus.SCHEDULED.value,
        use_conversation=payload.use_conversation,
    )
    session.add(call)
    session.commit()
    session.refresh(call)

    scheduler.schedule_call(call.id, call_at_utc)
    logger.info(
        "予約を登録しました call_id=%s at=%s", call.id, payload.call_at.isoformat()
    )
    return call.as_dict()


@app.get("/calls", tags=["calls"], dependencies=[Depends(require_api_key)])
async def list_calls(
    limit: int = 50,
    status: str | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Call).order_by(Call.call_at.desc()).limit(min(limit, 200))
    if status:
        stmt = stmt.where(Call.status == status)
    calls = session.scalars(stmt).all()
    return {"calls": [call.as_dict() for call in calls]}


@app.get("/calls/{call_id}", tags=["calls"], dependencies=[Depends(require_api_key)])
async def get_call(call_id: int, session: Session = Depends(get_session)):
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="指定された通話が見つかりません。")

    logs = session.scalars(
        select(ConversationLog)
        .where(ConversationLog.call_id == call_id)
        .order_by(ConversationLog.id)
    ).all()
    return {**call.as_dict(), "logs": [log.as_dict() for log in logs]}


@app.delete("/calls/{call_id}", tags=["calls"], dependencies=[Depends(require_api_key)])
async def cancel_call(call_id: int, session: Session = Depends(get_session)):
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="指定された通話が見つかりません。")
    if call.status != CallStatus.SCHEDULED.value:
        raise HTTPException(
            status_code=409,
            detail=f"status={call.status} の通話はキャンセルできません。",
        )

    call.status = CallStatus.CANCELED.value
    session.commit()
    scheduler.cancel_job(call_id)
    return {"canceled": call_id}


# --- v3: チャットからの予約と設定 ------------------------------------------


@app.post("/chat/schedule", tags=["chat"], dependencies=[Depends(require_api_key)])
async def chat_schedule(payload: ChatScheduleRequest, session: Session = Depends(get_session)):
    phone_number = payload.phone_number or _get_setting(session, PHONE_SETTING_KEY)
    if not phone_number:
        return {
            "scheduled": False,
            "reply": "先に電話番号を登録してください（例: +819012345678）。",
        }

    now = datetime.now(config.TZ)
    parsed = await parse_schedule_request(payload.text, now)

    if not parsed.understood or not parsed.call_at:
        return {"scheduled": False, "reply": parsed.reply}

    try:
        call_at = datetime.fromisoformat(parsed.call_at)
    except ValueError:
        return {
            "scheduled": False,
            "reply": "日時をうまく解釈できませんでした。「明日の7時に起こして」のように教えてください。",
        }

    if call_at.tzinfo is None:
        call_at = call_at.replace(tzinfo=config.TZ)

    call_at_utc = call_at.astimezone(timezone.utc).replace(tzinfo=None)
    if call_at_utc <= utcnow():
        return {
            "scheduled": False,
            "reply": "指定された時刻がすでに過ぎています。未来の時刻を指定してください。",
        }

    call = Call(
        phone_number=phone_number,
        call_at=call_at_utc,
        message=(parsed.message or config.DEFAULT_MESSAGE).strip(),
        status=CallStatus.SCHEDULED.value,
    )
    session.add(call)
    session.commit()
    session.refresh(call)

    scheduler.schedule_call(call.id, call_at_utc)
    return {"scheduled": True, "reply": parsed.reply, "call": call.as_dict()}


@app.get("/settings/phone", tags=["settings"], dependencies=[Depends(require_api_key)])
async def get_phone(session: Session = Depends(get_session)):
    return {"phone_number": _get_setting(session, PHONE_SETTING_KEY)}


@app.put("/settings/phone", tags=["settings"], dependencies=[Depends(require_api_key)])
async def put_phone(payload: PhoneSettingRequest, session: Session = Depends(get_session)):
    setting = session.get(Setting, PHONE_SETTING_KEY)
    if setting is None:
        session.add(Setting(key=PHONE_SETTING_KEY, value=payload.phone_number))
    else:
        setting.value = payload.phone_number
    session.commit()
    return {"phone_number": payload.phone_number}


def _get_setting(session: Session, key: str) -> str | None:
    setting = session.get(Setting, key)
    return setting.value if setting else None


# --- Twilio Webhook -------------------------------------------------------


@app.post("/twilio/voice/{call_id}", tags=["twilio"], include_in_schema=False)
async def twilio_voice(
    call_id: int,
    request: Request,
    token: str | None = None,
    session: Session = Depends(get_session),
):
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="指定された通話が見つかりません。")

    verify_call_token(call.token, token)
    await verify_twilio_signature(request)

    if call.use_conversation:
        twiml = twilio_client.build_conversation_relay_twiml(
            call_id=call.id,
            token=call.token,
            greeting=f"{call.message} 起きていますか？",
        )
    else:
        twiml = twilio_client.build_say_twiml(call.message)
        call.status = CallStatus.COMPLETED.value
        session.commit()

    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/status/{call_id}", tags=["twilio"], include_in_schema=False)
async def twilio_status(
    call_id: int,
    request: Request,
    token: str | None = None,
    session: Session = Depends(get_session),
):
    call = session.get(Call, call_id)
    if call is None:
        return Response(status_code=204)

    verify_call_token(call.token, token)
    await verify_twilio_signature(request)

    form = await request.form()
    call_status = str(form.get("CallStatus", ""))
    logger.info("Twilio ステータス call_id=%s status=%s", call_id, call_status)

    session.add(
        ConversationLog(
            call_id=call_id,
            role="system",
            content=f"Twilio CallStatus: {call_status}",
        )
    )

    # 相手が出なかった場合はここで再架電を予約する
    if call_status in {"no-answer", "busy", "failed", "canceled"}:
        if call.status in {
            CallStatus.DIALING.value,
            CallStatus.SCHEDULED.value,
            CallStatus.IN_PROGRESS.value,
        }:
            call.status = CallStatus.NO_ANSWER.value
            session.commit()
            scheduler.create_followup_call(call_id)
    elif call_status == "completed" and call.status == CallStatus.DIALING.value:
        # ConversationRelay に繋がらずに終わったケース
        call.status = CallStatus.NO_ANSWER.value

    session.commit()
    return Response(status_code=204)


# --- ConversationRelay WebSocket ------------------------------------------


@app.websocket("/conversation-relay")
async def conversation_relay(websocket: WebSocket):
    await handle_conversation_relay(websocket)
