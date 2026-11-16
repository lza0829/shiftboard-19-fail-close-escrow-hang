"""失败解冻重开闭环测试：
1. 失败关闭同事务释放锁、恢复班次、托管整批回在途并清托管，关闭快照+不可变事件落库；
2. 失败单完成接口拒绝再跑，新发起按发起时刻重快照、独立清单挂新单，不复用旧清单；
3. 解冻后立即开新单，清单键集合随结清/冻结变化，已结清键不得再次过户；
4. 失败单与新单都留档，总览进行中数与锁、托管集合同源。
"""
from pathlib import Path
import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
DATA_DIR = ROOT / "backend" / "data_test_fail"
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ.setdefault("FIXTURES_DIR", str(ROOT / "fixtures"))

from app.main import app  # noqa: E402
from app.db import connect  # noqa: E402


@pytest.fixture()
def client():
    db = DATA_DIR / "app.db"
    if db.exists():
        db.unlink()
    with TestClient(app) as c:
        yield c


def _shift_map(client):
    return {s["id"]: s for s in client.get("/api/shifts").json()}


def _batch_map(client):
    return {b["id"]: b for b in client.get("/api/batches").json()}


def test_fail_close_releases_lock_and_returns_escrow_in_one_tx(client):
    # 发起交班：4 个在途批进托管，双方班次上锁
    started = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    assert started.json()["status"] == "进行中"

    detail = client.get(f"/api/handovers/{hid}").json()
    assert len(detail["escrow_items"]) == 4
    assert {b["status"] for b in detail["items"]} == {"托管中"}

    shifts = _shift_map(client)
    assert shifts[1]["status"] == "交班中"
    assert shifts[2]["status"] == "接班中"

    # 托管中的批不能绕开流程直接结清
    blocked = client.post(f"/api/batches/{detail['items'][0]['id']}/settle")
    assert blocked.status_code == 409

    # 失败关闭
    failed = client.post(f"/api/handovers/{hid}/fail", json={"reason": "接班核验不过"}).json()
    assert failed["status"] == "失败"
    # 关闭快照列固化
    assert failed["released_lock"] == 1
    assert failed["escrow_returned_count"] == 4
    assert failed["closed_at"]
    assert failed["closure_reason"] == "接班核验不过"

    # 锁释放：班次恢复到交班前状态
    shifts = _shift_map(client)
    assert shifts[1]["status"] == "值班中"
    assert shifts[2]["status"] == "空闲"

    # 托管整批回交班方在途，归属未变，托管清空无悬空
    batches = _batch_map(client)
    for bid in (1, 2, 3, 4):
        assert batches[bid]["status"] == "在途"
        assert batches[bid]["owner_shift_id"] == 1
    detail_after = client.get(f"/api/handovers/{hid}").json()
    assert detail_after["escrow_items"] == []

    # 不可变事件表留痕：发起 + 失败关闭
    event_types = [e["event_type"] for e in detail_after["events"]]
    assert event_types == ["交班发起", "交班失败关闭"]
    fail_payload = detail_after["events"][-1]["payload"]
    assert fail_payload["锁释放"] is True
    assert fail_payload["托管归位批次数"] == 4

    # 事件触发器：不可更新、不可删除
    with connect() as conn:
        with pytest.raises(Exception):
            conn.execute("UPDATE handover_events SET event_type = 'x' WHERE handover_id = ?", (hid,))
        with pytest.raises(Exception):
            conn.execute("DELETE FROM handover_events WHERE handover_id = ?", (hid,))


def test_failed_order_cannot_complete_and_reopen_resnapshots(client):
    started = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    hid1 = started.json()["id"]
    client.post(f"/api/handovers/{hid1}/fail", json={"reason": "首次失败"})

    # 失败单完成接口必须拒绝，禁止复活旧单续跑
    again = client.post(f"/api/handovers/{hid1}/complete")
    assert again.status_code == 409
    # 失败单不能再失败关闭一次
    again_fail = client.post(f"/api/handovers/{hid1}/fail", json={"reason": "x"})
    assert again_fail.status_code == 409

    # 解冻（失败关闭即解冻）后立即开新单：按发起时刻重快照
    reopened = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    assert reopened.status_code == 200, reopened.text
    hid2 = reopened.json()["id"]
    assert hid2 != hid1
    new_detail = client.get(f"/api/handovers/{hid2}").json()
    assert new_detail["status"] == "进行中"
    assert {b["biz_key"] for b in new_detail["items"]} == {
        "B20261115-01",
        "B20261115-02",
        "B20261115-03",
        "B20261114-09",
    }

    # 独立清单表：新单清单挂新交班单 id，旧单清单原样保留（不复用、不删档）
    with connect() as conn:
        old_rows = conn.execute(
            "SELECT batch_id FROM handover_items WHERE handover_id = ? ORDER BY batch_id",
            (hid1,),
        ).fetchall()
        new_rows = conn.execute(
            "SELECT batch_id FROM handover_items WHERE handover_id = ? ORDER BY batch_id",
            (hid2,),
        ).fetchall()
    assert [r["batch_id"] for r in old_rows] == [1, 2, 3, 4]
    assert [r["batch_id"] for r in new_rows] == [1, 2, 3, 4]

    # 记录保留失败单与新单
    logs = client.get("/api/handovers").json()
    status_by_id = {h["id"]: h["status"] for h in logs}
    assert status_by_id[hid1] == "失败"
    assert status_by_id[hid2] == "进行中"

    # 新单可正常完成并过户
    done = client.post(f"/api/handovers/{hid2}/complete")
    assert done.status_code == 200
    assert done.json()["status"] == "已完成"
    batches = _batch_map(client)
    assert all(batches[b]["owner_shift_id"] == 2 for b in (1, 2, 3, 4))


def test_snapshot_changes_after_settle_freeze_unfreeze(client):
    hid1 = client.post(
        "/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2}
    ).json()["id"]
    client.post(f"/api/handovers/{hid1}/fail", json={"reason": "失败重开"})

    # 期间结清批 1、冻结批 2
    assert client.post("/api/batches/1/settle").status_code == 200
    assert client.post("/api/batches/2/freeze").status_code == 200

    # 新单只含新快照：批 1 已结清、批 2 已冻结，都不在清单里
    hid2 = client.post(
        "/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2}
    ).json()["id"]
    items2 = client.get(f"/api/handovers/{hid2}").json()["items"]
    assert {b["biz_key"] for b in items2} == {"B20261115-03", "B20261114-09"}

    # 完成新单，仅过户快照内两批
    done = client.post(f"/api/handovers/{hid2}/complete").json()
    assert done["status"] == "已完成"
    batches = _batch_map(client)
    assert batches[3]["owner_shift_id"] == 2
    assert batches[4]["owner_shift_id"] == 2

    # 解冻批 2 后允许立即再开新单（交班方此时为已交班，仍可重新进入交班）；
    # 接班方改用仍空闲的夜班；已结清的批 1 永不复活、不再过户
    assert client.post("/api/batches/2/unfreeze").status_code == 200
    hid3 = client.post(
        "/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 3}
    ).json()["id"]
    items3 = client.get(f"/api/handovers/{hid3}").json()["items"]
    assert [b["biz_key"] for b in items3] == ["B20261115-02"]
    client.post(f"/api/handovers/{hid3}/complete")

    batches = _batch_map(client)
    assert batches[2]["owner_shift_id"] == 3
    assert batches[2]["status"] == "在途"
    assert batches[1]["status"] == "已结清"
    assert batches[1]["owner_shift_id"] == 1  # 已结清键没有被再次过户


def test_complete_defensively_fails_when_snapshot_escrow_diverge(client):
    hid = client.post(
        "/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2}
    ).json()["id"]

    # 人为破坏托管集合：删掉批 4 的托管行（清单仍含 4 批）
    with connect() as conn:
        conn.execute(
            "DELETE FROM handover_escrow WHERE handover_id = ? AND batch_id = 4",
            (hid,),
        )
        conn.commit()

    # 完成必须拒绝过户并同事务失败关闭，按清单整批归位
    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "失败"
    assert body["released_lock"] == 1
    assert body["escrow_returned_count"] == 4  # 以清单为准，孤儿批 4 也被归位

    batches = _batch_map(client)
    for bid in (1, 2, 3, 4):
        assert batches[bid]["owner_shift_id"] == 1
        assert batches[bid]["status"] == "在途"
    assert client.get(f"/api/handovers/{hid}").json()["escrow_items"] == []
    shifts = _shift_map(client)
    assert shifts[1]["status"] == "值班中"
    assert shifts[2]["status"] == "空闲"

    # 对账报告标记不一致
    reports = client.get("/api/reconcile").json()
    assert reports[0]["一致"] is False
    assert reports[0]["handover_id"] == hid


def test_dashboard_active_count_same_source_as_lock_and_escrow(client):
    dash0 = client.get("/api/dashboard").json()
    assert dash0["进行中交班"] == 0
    assert dash0["托管批次数"] == 0
    assert dash0["锁定班次数"] == 0
    assert dash0["锁托管同源"] is True

    hid = client.post(
        "/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2}
    ).json()["id"]
    dash1 = client.get("/api/dashboard").json()
    assert dash1["进行中交班"] == 1
    assert dash1["托管批次数"] == 4
    assert dash1["锁定班次数"] == 2
    assert dash1["锁托管同源"] is True

    client.post(f"/api/handovers/{hid}/fail", json={"reason": "关闭"})
    dash2 = client.get("/api/dashboard").json()
    assert dash2["进行中交班"] == 0
    assert dash2["托管批次数"] == 0
    assert dash2["锁定班次数"] == 0
    assert dash2["锁托管同源"] is True
