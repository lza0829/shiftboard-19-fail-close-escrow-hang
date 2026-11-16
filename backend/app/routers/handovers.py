from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(prefix="/handovers", tags=["交班"])


class StartBody(BaseModel):
    from_shift_id: int = Field(..., description="交班班次")
    to_shift_id: int = Field(..., description="接班班次")


class FailBody(BaseModel):
    reason: str = Field(default="人工失败关闭", description="失败关闭原因")


@router.get("")
def get_handovers():
    return svc.list_handovers()


@router.get("/{handover_id}")
def get_one(handover_id: int):
    return svc.get_handover(handover_id)


@router.get("/{handover_id}/closure")
def get_closure(handover_id: int):
    """只读闭环证明：锁态、双方班次态、托管清空结果、是否允许再完成。"""
    return svc.get_closure_proof(handover_id)


@router.post("/start")
def start(body: StartBody):
    return svc.start_handover(body.from_shift_id, body.to_shift_id)


@router.post("/{handover_id}/complete")
def complete(handover_id: int):
    return svc.complete_handover(handover_id)


@router.post("/{handover_id}/fail")
def fail(handover_id: int, body: FailBody):
    return svc.fail_handover(handover_id, body.reason)
