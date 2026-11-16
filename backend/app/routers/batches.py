from fastapi import APIRouter, Query

from app.services import handover as svc

router = APIRouter(prefix="/batches", tags=["在途批次"])


@router.get("")
def get_batches(owner_shift_id: int | None = Query(default=None)):
    return svc.list_batches(owner_shift_id)


@router.post("/{batch_id}/settle")
def settle(batch_id: int):
    """结清：仅在途批可结清，托管中的批不允许绕开交班流程。"""
    return svc.settle_batch(batch_id)


@router.post("/{batch_id}/freeze")
def freeze(batch_id: int):
    """冻结：在途批冻结后不进新交班快照。"""
    return svc.freeze_batch(batch_id)


@router.post("/{batch_id}/unfreeze")
def unfreeze(batch_id: int):
    """解冻：回到在途，可立即被新发起的交班单快照纳入。"""
    return svc.unfreeze_batch(batch_id)
