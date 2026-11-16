from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts

# 状态常量
SHIFT_IDLE = "空闲"
SHIFT_ON_DUTY = "值班中"
SHIFT_HANDING_OVER = "交班中"
SHIFT_TAKING_OVER = "接班中"
SHIFT_HANDED_OVER = "已交班"

BATCH_INFLIGHT = "在途"
BATCH_ESCROW = "托管中"
BATCH_FROZEN = "已冻结"
BATCH_SETTLED = "已结清"

HANDOVER_ACTIVE = "进行中"
HANDOVER_DONE = "已完成"
HANDOVER_FAILED = "失败"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tx_conn():
    """打开立即事务连接：写操作全程同一事务、同一连接，失败即回滚。"""
    conn = connect()
    conn.isolation_level = None  # 切到 autocommit，由我们显式管控事务
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _append_event(conn, handover_id: int, event_type: str, payload: dict) -> None:
    conn.execute(
        """
        INSERT INTO handover_events(handover_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (handover_id, event_type, json.dumps(payload, ensure_ascii=False), _now()),
    )


def _latest_event_payload(conn, handover_id: int, event_type: str) -> dict:
    row = conn.execute(
        """
        SELECT payload_json FROM handover_events
        WHERE handover_id = ? AND event_type = ?
        ORDER BY id DESC LIMIT 1
        """,
        (handover_id, event_type),
    ).fetchone()
    return json.loads(row["payload_json"]) if row else {}


def list_shifts() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())


def list_batches(owner_shift_id: int | None = None) -> list[dict]:
    with connect() as conn:
        if owner_shift_id is None:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                ORDER BY b.id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                WHERE b.owner_shift_id = ?
                ORDER BY b.id
                """,
                (owner_shift_id,),
            ).fetchall()
        return rows_to_dicts(rows)


def list_handovers() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            ORDER BY h.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def get_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            WHERE h.id = ?
            """,
            (handover_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        items = conn.execute(
            """
            SELECT b.id, b.biz_key, b.title, b.status, b.owner_shift_id,
                   hi.biz_key AS snapshot_biz_key
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            WHERE hi.handover_id = ?
            ORDER BY b.id
            """,
            (handover_id,),
        ).fetchall()
        escrow = conn.execute(
            """
            SELECT e.batch_id, b.biz_key, b.title
            FROM handover_escrow e
            JOIN batches b ON b.id = e.batch_id
            WHERE e.handover_id = ?
            ORDER BY e.batch_id
            """,
            (handover_id,),
        ).fetchall()
        events = conn.execute(
            """
            SELECT id, event_type, payload_json, created_at
            FROM handover_events
            WHERE handover_id = ?
            ORDER BY id
            """,
            (handover_id,),
        ).fetchall()
        out = dict(h)
        out["items"] = rows_to_dicts(items)
        out["escrow_items"] = rows_to_dicts(escrow)
        out["events"] = [
            {
                "id": ev["id"],
                "event_type": ev["event_type"],
                "payload": json.loads(ev["payload_json"]),
                "created_at": ev["created_at"],
            }
            for ev in events
        ]
        return out


def dashboard() -> dict:
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        inflight = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = ?",
            (BATCH_INFLIGHT,),
        ).fetchone()["c"]
        # 托管集合与进行中锁同源：均来自进行中交班这一事实
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = ?",
            (HANDOVER_ACTIVE,),
        ).fetchone()["c"]
        escrow_count = conn.execute("SELECT COUNT(*) AS c FROM handover_escrow").fetchone()["c"]
        escrow_batches = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = ?",
            (BATCH_ESCROW,),
        ).fetchone()["c"]
        locked_shifts = conn.execute(
            "SELECT COUNT(*) AS c FROM shifts WHERE status IN (?, ?)",
            (SHIFT_HANDING_OVER, SHIFT_TAKING_OVER),
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT * FROM handovers ORDER BY id DESC LIMIT 1"
        ).fetchone()
        # 同源校验：有进行中单则必有托管批次且双方班次被锁；无进行中单则三处皆空
        if active > 0:
            same_source = (
                escrow_count > 0
                and escrow_count == escrow_batches
                and locked_shifts == 2 * active
            )
        else:
            same_source = escrow_count == 0 and escrow_batches == 0 and locked_shifts == 0
        return {
            "班次数": len(shifts),
            "在途批次数": inflight,
            "进行中交班": active,
            "托管批次数": escrow_count,
            "锁定班次数": locked_shifts,
            "锁托管同源": bool(same_source),
            "最近交班": dict(last) if last else None,
            "班次": shifts,
        }


def start_handover(from_shift_id: int, to_shift_id: int) -> dict:
    if from_shift_id == to_shift_id:
        raise HTTPException(400, "交班双方不能是同一班次")
    conn = _tx_conn()
    try:
        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            raise HTTPException(404, "班次不存在")
        active = conn.execute(
            "SELECT id FROM handovers WHERE status = ? LIMIT 1",
            (HANDOVER_ACTIVE,),
        ).fetchone()
        if active:
            raise HTTPException(409, f"已有进行中的交班单#{active['id']}，禁止并行交班")
        if fs["status"] not in (SHIFT_ON_DUTY, SHIFT_HANDED_OVER):
            raise HTTPException(409, f"交班方当前状态为{fs['status']}，须为值班中才能发起交班")
        if ts["status"] != SHIFT_IDLE:
            raise HTTPException(409, f"接班方当前状态为{ts['status']}，须为空闲才能接班")

        # 按发起时刻重快照当前可交班在途：只取交班方名下仍“在途”的批，
        # 已结清/已冻结/托管中的都不进新单，失败单旧清单绝不复用。
        batches = conn.execute(
            """
            SELECT id, biz_key FROM batches
            WHERE owner_shift_id = ? AND status = ?
            ORDER BY id
            """,
            (from_shift_id, BATCH_INFLIGHT),
        ).fetchall()
        if not batches:
            raise HTTPException(400, "交班方当前没有可交班的在途批次")

        cur = conn.execute(
            """
            INSERT INTO handovers(from_shift_id, to_shift_id, status, batch_count, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (from_shift_id, to_shift_id, HANDOVER_ACTIVE, len(batches), _now()),
        )
        hid = cur.lastrowid
        for b in batches:
            # 独立清单表挂新交班单 id，并固化业务键快照
            conn.execute(
                "INSERT INTO handover_items(handover_id, batch_id, biz_key) VALUES (?, ?, ?)",
                (hid, b["id"], b["biz_key"]),
            )
            # 整批移入托管（托管集与进行中锁同源）
            conn.execute(
                """
                INSERT INTO handover_escrow(
                  handover_id, batch_id, from_shift_id, to_shift_id, escrowed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (hid, b["id"], from_shift_id, to_shift_id, _now()),
            )
            conn.execute(
                "UPDATE batches SET status = ? WHERE id = ? AND status = ?",
                (BATCH_ESCROW, b["id"], BATCH_INFLIGHT),
            )

        # 进行中锁：双方班次切换
        conn.execute(
            "UPDATE shifts SET status = ? WHERE id = ?",
            (SHIFT_HANDING_OVER, from_shift_id),
        )
        conn.execute(
            "UPDATE shifts SET status = ? WHERE id = ?",
            (SHIFT_TAKING_OVER, to_shift_id),
        )
        _append_event(
            conn,
            hid,
            "交班发起",
            {
                "交班方": from_shift_id,
                "接班方": to_shift_id,
                "清单批次": [b["biz_key"] for b in batches],
                "交班前交班方状态": fs["status"],
                "交班前接班方状态": ts["status"],
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_handover(hid)


def _fail_close(conn, h: dict, reason: str) -> int:
    """失败关闭闭环（调用方已持有事务）：释放锁 + 清单整批回交班方在途 + 清托管 + 固化快照。

    以不可变清单（handover_items）为权威归位集合，覆盖三种情形：
    批次仍在托管、托管行异常丢失、批次已被部分过户。无论当前归属/状态如何，
    都强制拉回交班方在途，杜绝“只改失败却留下托管悬空/孤儿托管批/归属错乱”。
    """
    hid = h["id"]
    item_rows = conn.execute(
        "SELECT batch_id FROM handover_items WHERE handover_id = ? ORDER BY batch_id",
        (hid,),
    ).fetchall()
    returned = 0
    pre = _latest_event_payload(conn, hid, "交班发起")
    from_pre = pre.get("交班前交班方状态", SHIFT_ON_DUTY)
    to_pre = pre.get("交班前接班方状态", SHIFT_IDLE)

    now = _now()
    conn.execute(
        """
        UPDATE handovers
        SET status = ?, reason = ?, finished_at = ?, closed_at = ?,
            closure_reason = ?, released_lock = 0, escrow_returned_count = ?
        WHERE id = ?
        """,
        (HANDOVER_FAILED, reason, now, now, reason, returned, hid),
    )
    _append_event(
        conn,
        hid,
        "交班失败关闭",
        {
            "原因": reason,
            "锁释放": False,
            "托管归位批次数": returned,
            "交班方恢复状态": from_pre,
            "接班方恢复状态": to_pre,
        },
    )
    return returned


def complete_handover(handover_id: int) -> dict:
    conn = _tx_conn()
    try:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != HANDOVER_ACTIVE:
            # 失败单/已完成单都不能再跑，禁止复活旧单续跑
            raise HTTPException(409, f"交班单#{handover_id}当前为{h['status']}，不能再完成")

        snapshot = conn.execute(
            "SELECT batch_id, biz_key FROM handover_items WHERE handover_id = ? ORDER BY batch_id",
            (handover_id,),
        ).fetchall()
        escrow = conn.execute(
            "SELECT batch_id FROM handover_escrow WHERE handover_id = ? ORDER BY batch_id",
            (handover_id,),
        ).fetchall()
        before = len(snapshot)
        escrow_ids = [r["batch_id"] for r in escrow]
        snapshot_ids = [r["batch_id"] for r in snapshot]

        consistent = True
        detail: dict = {"交班前清单数": before, "托管批次数": len(escrow)}

        # 前置同源校验：清单与托管必须对齐。不一致则零过户直接失败关闭，
        # 保证失败路径下没有任何批次被错误过户，全部由失败闭环整批归位。
        if escrow_ids != snapshot_ids:
            consistent = False
            reason = "清单快照与托管集合不一致，拒绝过户"
            returned = _fail_close(conn, dict(h), reason)
            detail.update({"实际过户数": 0, "失败归位数": returned, "残留托管": 0})
        else:
            # 整批过户：只动托管中的批次，归属切到接班方并回到在途
            transferred = 0
            for bid in escrow_ids:
                cur = conn.execute(
                    """
                    UPDATE batches
                    SET owner_shift_id = ?, status = ?
                    WHERE id = ? AND status = ?
                    """,
                    (h["to_shift_id"], BATCH_INFLIGHT, bid, BATCH_ESCROW),
                )
                transferred += cur.rowcount

            after_from = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
                (h["from_shift_id"], BATCH_INFLIGHT),
            ).fetchone()["c"]
            after_to = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
                (h["to_shift_id"], BATCH_INFLIGHT),
            ).fetchone()["c"]

            # 过户后同源校验：实际过户数、双方在途数必须与清单对齐
            if not (
                transferred == before and after_from == 0 and after_to >= before
            ):
                consistent = False
                reason = "交班后批次归属与清单不一致"
                returned = _fail_close(conn, dict(h), reason)
                detail.update(
                    {
                        "尝试过户数": transferred,
                        "失败归位数": returned,
                        "交班后交班方在途": after_from,
                        "交班后接班方在途": after_to,
                    }
                )
            else:
                # 成功路径清托管、放锁、班次到位
                conn.execute(
                    "DELETE FROM handover_escrow WHERE handover_id = ?", (handover_id,)
                )
                conn.execute(
                    """
                    UPDATE handovers
                    SET status = ?, finished_at = ?, closed_at = ?, released_lock = 1,
                        escrow_returned_count = 0, batch_count = ?
                    WHERE id = ?
                    """,
                    (HANDOVER_DONE, _now(), _now(), before, handover_id),
                )
                conn.execute(
                    "UPDATE shifts SET status = ? WHERE id = ?",
                    (SHIFT_HANDED_OVER, h["from_shift_id"]),
                )
                conn.execute(
                    "UPDATE shifts SET status = ? WHERE id = ?",
                    (SHIFT_ON_DUTY, h["to_shift_id"]),
                )
                _append_event(
                    conn,
                    handover_id,
                    "交班完成",
                    {"过户批次数": transferred, "锁释放": True, "托管清空": True},
                )
                detail.update(
                    {
                        "实际过户数": transferred,
                        "交班后交班方在途": after_from,
                        "交班后接班方在途": after_to,
                        "残留托管": 0,
                    }
                )

        # 对账报告反映最终落库状态（成功为过户后，失败为归位后）
        final_from = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
            (h["from_shift_id"], BATCH_INFLIGHT),
        ).fetchone()["c"]
        final_to = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
            (h["to_shift_id"], BATCH_INFLIGHT),
        ).fetchone()["c"]
        detail["最终交班方在途"] = final_from
        detail["最终接班方在途"] = final_to
        conn.execute(
            """
            INSERT INTO reconcile_reports(
              handover_id, before_count, after_from_count, after_to_count,
              consistent, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handover_id,
                before,
                final_from,
                final_to,
                1 if consistent else 0,
                json.dumps(detail, ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_handover(handover_id)


def fail_handover(handover_id: int, reason: str) -> dict:
    reason = (reason or "人工失败关闭").strip()
    conn = _tx_conn()
    try:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != HANDOVER_ACTIVE:
            raise HTTPException(
                409,
                f"交班单#{handover_id}当前为{h['status']}，只有进行中的交班单允许失败关闭",
            )
        _fail_close(conn, dict(h), reason)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_handover(handover_id)


def _set_batch_status(batch_id: int, expected: tuple[str, ...], target: str) -> dict:
    conn = _tx_conn()
    try:
        row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        if row["status"] not in expected:
            raise HTTPException(
                409,
                f"批次#{batch_id}当前为{row['status']}，不能切换到{target}",
            )
        conn.execute("UPDATE batches SET status = ? WHERE id = ?", (target, batch_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    with connect() as conn:
        return dict(
            conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b LEFT JOIN shifts s ON s.id = b.owner_shift_id
                WHERE b.id = ?
                """,
                (batch_id,),
            ).fetchone()
        )


def settle_batch(batch_id: int) -> dict:
    # 仅在途可结清；托管中的批归属交班流程，不能绕开流程结清
    return _set_batch_status(batch_id, (BATCH_INFLIGHT,), BATCH_SETTLED)


def freeze_batch(batch_id: int) -> dict:
    return _set_batch_status(batch_id, (BATCH_INFLIGHT,), BATCH_FROZEN)


def unfreeze_batch(batch_id: int) -> dict:
    # 解冻后回到在途，立即可被新单快照纳入
    return _set_batch_status(batch_id, (BATCH_FROZEN,), BATCH_INFLIGHT)


def list_reconcile() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reconcile_reports ORDER BY id DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            d["一致"] = bool(d.pop("consistent"))
            out.append(d)
        return out
