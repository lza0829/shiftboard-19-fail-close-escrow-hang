from __future__ import annotations

import json
from pathlib import Path

from app.db import FIXTURES_DIR, connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  keeper TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲'
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  biz_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_shift_id INTEGER REFERENCES shifts(id),
  status TEXT NOT NULL DEFAULT '在途',
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS handovers (
  id INTEGER PRIMARY KEY,
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  status TEXT NOT NULL,
  batch_count INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  -- 关闭快照：失败关闭时把锁释放与托管归位结果固化在交班单上（形态二）
  closed_at TEXT,
  closure_reason TEXT NOT NULL DEFAULT '',
  released_lock INTEGER NOT NULL DEFAULT 0,
  escrow_returned_count INTEGER NOT NULL DEFAULT 0,
  -- 失败闭环证明：双方班次落库态、托管清空结果、是否允许再完成（关闭即置 0）
  from_shift_status_after TEXT NOT NULL DEFAULT '',
  to_shift_status_after TEXT NOT NULL DEFAULT '',
  escrow_cleared INTEGER NOT NULL DEFAULT 0,
  recomplete_allowed INTEGER NOT NULL DEFAULT 1
);
-- 独立清单表：每单（含失败后重开的新单）挂各自交班单 id 的不可变快照，
-- 新发起按发起时刻重快照，禁止复用失败单旧清单。
CREATE TABLE IF NOT EXISTS handover_items (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  biz_key TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (handover_id, batch_id)
);
-- 托管集合：进行中交班把在途批整批移入托管，与进行中锁同源；失败关闭整批回交班方在途并清托管。
CREATE TABLE IF NOT EXISTS handover_escrow (
  handover_id INTEGER NOT NULL,
  batch_id INTEGER NOT NULL,
  from_shift_id INTEGER NOT NULL,
  to_shift_id INTEGER NOT NULL,
  escrowed_at TEXT NOT NULL,
  PRIMARY KEY (handover_id, batch_id)
);
-- 独立关闭事件表：按交班单 id 追加不可变状态机事件（形态一）。
CREATE TABLE IF NOT EXISTS handover_events (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_handover_events_no_update
BEFORE UPDATE ON handover_events
BEGIN
  SELECT RAISE(ABORT, '交班事件不可变，禁止更新');
END;
CREATE TRIGGER IF NOT EXISTS trg_handover_events_no_delete
BEFORE DELETE ON handover_events
BEGIN
  SELECT RAISE(ABORT, '交班事件不可变，禁止删除');
END;
CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER REFERENCES handovers(id),
  before_count INTEGER NOT NULL,
  after_from_count INTEGER NOT NULL,
  after_to_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        n = conn.execute("SELECT COUNT(*) AS c FROM shifts").fetchone()["c"]
        if n:
            return
        seed_path = FIXTURES_DIR / "seed.json"
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        for s in data["shifts"]:
            conn.execute(
                "INSERT INTO shifts(id, name, keeper, status) VALUES (?, ?, ?, ?)",
                (s["id"], s["name"], s["keeper"], s["status"]),
            )
        for b in data["batches"]:
            conn.execute(
                "INSERT INTO batches(id, biz_key, title, owner_shift_id, status, note) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    b["id"],
                    b["biz_key"],
                    b["title"],
                    b["owner_shift_id"],
                    b["status"],
                    b.get("note", ""),
                ),
            )
        conn.commit()


def _ensure_columns(conn) -> None:
    """对老库幂等补列，避免重建数据。"""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(handovers)").fetchall()}
    extra = {
        "closed_at": "TEXT",
        "closure_reason": "TEXT NOT NULL DEFAULT ''",
        "released_lock": "INTEGER NOT NULL DEFAULT 0",
        "escrow_returned_count": "INTEGER NOT NULL DEFAULT 0",
        "from_shift_status_after": "TEXT NOT NULL DEFAULT ''",
        "to_shift_status_after": "TEXT NOT NULL DEFAULT ''",
        "escrow_cleared": "INTEGER NOT NULL DEFAULT 0",
        "recomplete_allowed": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, decl in extra.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE handovers ADD COLUMN {name} {decl}")
    # 老库回填：已关闭（失败/已完成）的单一律禁止再完成；已完成单托管必然已清
    conn.execute("UPDATE handovers SET recomplete_allowed = 0 WHERE status != '进行中'")
    conn.execute("UPDATE handovers SET escrow_cleared = 1 WHERE status = '已完成'")
    item_cols = {r["name"] for r in conn.execute("PRAGMA table_info(handover_items)").fetchall()}
    if "biz_key" not in item_cols:
        conn.execute("ALTER TABLE handover_items ADD COLUMN biz_key TEXT NOT NULL DEFAULT ''")
