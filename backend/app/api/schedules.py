"""统一调度中心 API：周期任务视图 + 开关 + 立即执行 + 钟点调整。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..jobs import schedules
from ..models import ScheduleState

router = APIRouter(prefix="/api/schedules")


def _view(st: ScheduleState) -> dict:
    sch = schedules._by_key(st.key)
    return {
        "key": st.key,
        "label": sch.label if sch else st.key,
        "hour": schedules.effective_hour(st),
        "interval_hours": sch.interval_hours if sch else None,
        "daily": bool(sch and (sch.daily_hour is not None or sch.hour_from_settings)),
        "enabled": schedules.effective_enabled(st),
        "enabled_self": st.enabled,
        "last_run_at": st.last_run_at,
        "next_run_at": st.next_run_at,
        "last_status": st.last_status,
        "run_count": st.run_count,
    }


@router.get("")
def list_schedules() -> dict:
    schedules.ensure_states()
    with Session(engine) as s:
        items = s.exec(select(ScheduleState).order_by(ScheduleState.key)).all()
        return {"items": [_view(st) for st in items]}


@router.post("/{key}/toggle")
def toggle_schedule(key: str) -> dict:
    with Session(engine) as s:
        st = s.exec(select(ScheduleState).where(ScheduleState.key == key)).first()
        if st is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        st.enabled = not st.enabled
        if not st.enabled:
            st.next_run_at = None  # 停用即停摆，重新启用后重新排程
        s.add(st)
        s.commit()
        s.refresh(st)
        return _view(st)


class HourIn(BaseModel):
    hour: int


@router.put("/{key}/hour")
def set_hour(key: str, body: HourIn) -> dict:
    if not 0 <= body.hour <= 23:
        raise HTTPException(status_code=400, detail="hour 必须在 0~23")
    with Session(engine) as s:
        st = s.exec(select(ScheduleState).where(ScheduleState.key == key)).first()
        if st is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        st.hour = body.hour
        st.next_run_at = None  # 重新排程
        s.add(st)
        s.commit()
        s.refresh(st)
        return _view(st)


@router.post("/{key}/run")
async def run_now(key: str) -> dict:
    """立即执行一次（记录运行态；与排程互不干扰）。"""
    schedules.ensure_states()
    try:
        msg = await schedules._execute(key)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        msg = f"失败：{type(e).__name__}: {str(e)[:120]}"
        schedules._set_result(key, msg)
        raise HTTPException(status_code=500, detail=msg)
    with Session(engine) as s:
        from datetime import datetime, timezone
        st = s.exec(select(ScheduleState).where(ScheduleState.key == key)).first()
        if st is not None:
            st.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
            st.last_status = f"ok：{msg}（手动）"
            st.run_count = (st.run_count or 0) + 1
            s.add(st)
            s.commit()
    return {"status": "ok", "message": msg}
