"""all_cases.py: Consolidated 8-case AI review benchmark suite.

Covers all eight review cases from the benchmark catalog:
- Case 01: Compound keyset pagination tie-breaker skip
- Case 02: Streaming CSV export corruption without escaping
- Case 03: Queued delivery batching with remainder job drops
- Case 04: Online column retirement breaking active worker polling
- Case 05: Export batch size increase exceeding receiver limits
- Case 06: Queue routing cutover starving unsubscribed workers
- Case 07: Partial index creation for pending delivery queue query
- Case 08: Activity summary total calculation across empty/populated states
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
from dataclasses import dataclass
from itertools import islice
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_SQL = """
CREATE TABLE deliveries (
    id INTEGER PRIMARY KEY,
    report TEXT NOT NULL,
    scheduled_at INTEGER NOT NULL,
    queue TEXT NOT NULL,
    legacy_format TEXT NOT NULL DEFAULT 'csv',
    delivered_at INTEGER
);

CREATE TABLE receipts (
    delivery_id INTEGER PRIMARY KEY REFERENCES deliveries(id),
    recorded_at INTEGER NOT NULL
);
"""


def init_database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA_SQL)
    return db


# ============================================================================
# Case 01: Page Report History by Scheduled Time
# ============================================================================

@dataclass(frozen=True)
class HistoryPage:
    rows: List[Tuple[Any, ...]]
    next_cursor: Optional[Any]


def case_01_history_buggy(db: sqlite3.Connection, limit: int, cursor: Optional[int] = None) -> HistoryPage:
    query = "SELECT id, report, scheduled_at FROM deliveries"
    params: List[Any] = []
    if cursor is not None:
        query += " WHERE scheduled_at < ?"
        params.append(cursor)
    query += " ORDER BY scheduled_at DESC, id DESC LIMIT ?"
    rows = db.execute(query, (*params, limit + 1)).fetchall()
    next_cursor = rows[limit - 1][2] if len(rows) > limit else None
    return HistoryPage([tuple(r) for r in rows[:limit]], next_cursor)


def case_01_history_fixed(
    db: sqlite3.Connection, limit: int, cursor: Optional[Tuple[int, int]] = None
) -> HistoryPage:
    query = "SELECT id, report, scheduled_at FROM deliveries"
    params: List[Any] = []
    if cursor is not None:
        query += " WHERE (scheduled_at < ?) OR (scheduled_at = ? AND id < ?)"
        params.extend([cursor[0], cursor[0], cursor[1]])
    query += " ORDER BY scheduled_at DESC, id DESC LIMIT ?"
    rows = db.execute(query, (*params, limit + 1)).fetchall()
    next_cursor = (rows[limit - 1][2], rows[limit - 1][0]) if len(rows) > limit else None
    return HistoryPage([tuple(r) for r in rows[:limit]], next_cursor)


# ============================================================================
# Case 02: Stream Report Export
# ============================================================================

def case_02_write_export_buggy(db: sqlite3.Connection, output: io.StringIO) -> None:
    output.write("id,report,scheduled_at\n")
    rows = db.execute("SELECT id, report, scheduled_at FROM deliveries ORDER BY id")
    for row in rows:
        output.write(",".join(map(str, row)) + "\n")


def case_02_write_export_fixed(db: sqlite3.Connection, output: io.StringIO) -> None:
    writer = csv.writer(output)
    writer.writerow(["id", "report", "scheduled_at"])
    rows = db.execute("SELECT id, report, scheduled_at FROM deliveries ORDER BY id")
    for row in rows:
        writer.writerow(list(row))


# ============================================================================
# Case 03: Batch Queued Deliveries
# ============================================================================

def case_03_deliver_due_buggy(db: sqlite3.Connection, queue: str, now: int) -> int:
    with db:
        jobs = db.execute(
            "SELECT id FROM deliveries WHERE queue = ? AND delivered_at IS NULL "
            "AND scheduled_at <= ? ORDER BY scheduled_at, id",
            (queue, now),
        ).fetchall()
        processed = 0
        batch_size = 20
        for start in range(0, len(jobs) - batch_size + 1, batch_size):
            batch = jobs[start : start + batch_size]
            db.executemany(
                "INSERT INTO receipts VALUES (?, ?)",
                ((job[0], now) for job in batch),
            )
            db.executemany(
                "UPDATE deliveries SET delivered_at = ? WHERE id = ?",
                ((now, job[0]) for job in batch),
            )
            processed += len(batch)
    return processed


def case_03_deliver_due_fixed(db: sqlite3.Connection, queue: str, now: int) -> int:
    with db:
        jobs = db.execute(
            "SELECT id FROM deliveries WHERE queue = ? AND delivered_at IS NULL "
            "AND scheduled_at <= ? ORDER BY scheduled_at, id",
            (queue, now),
        ).fetchall()
        processed = 0
        batch_size = 20
        for start in range(0, len(jobs), batch_size):
            batch = jobs[start : start + batch_size]
            db.executemany(
                "INSERT INTO receipts VALUES (?, ?)",
                ((job[0], now) for job in batch),
            )
            db.executemany(
                "UPDATE deliveries SET delivered_at = ? WHERE id = ?",
                ((now, job[0]) for job in batch),
            )
            processed += len(batch)
    return processed


# ============================================================================
# Case 04: Retire Legacy Delivery Column
# ============================================================================

CASE_04_INVENTORY = {
    "observation": "inventory-204",
    "observed_at": "2026-10-21T09:00:00Z",
    "sites": {
        "elm": [
            {
                "revision": "workers-17",
                "replicas": 2,
                "poll_sql": "SELECT id, legacy_format FROM deliveries WHERE delivered_at IS NULL",
            },
            {
                "revision": "workers-18",
                "replicas": 3,
                "poll_sql": "SELECT id, report FROM deliveries WHERE delivered_at IS NULL",
            },
        ],
        "cedar": [
            {
                "revision": "workers-18",
                "replicas": 4,
                "poll_sql": "SELECT id, report FROM deliveries WHERE delivered_at IS NULL",
            }
        ],
    },
}


def case_04_simulate_migration(db: sqlite3.Connection, inventory: Dict[str, Any]) -> List[str]:
    db.execute("ALTER TABLE deliveries DROP COLUMN legacy_format")
    failures = []
    for site, workers in inventory.get("sites", {}).items():
        for worker in workers:
            poll_sql = worker["poll_sql"]
            try:
                db.execute(poll_sql).fetchall()
            except sqlite3.OperationalError as e:
                failures.append(
                    f"Site {site} revision {worker['revision']} failed with sql error: {e}"
                )
    return failures


# ============================================================================
# Case 05: Increase Export Batch Size
# ============================================================================

CASE_05_CONFIG = {
    "observation": "archive-318",
    "observed_at": "2026-10-21T09:00:00Z",
    "sites": {
        "elm": {"receiver_revision": "archive-12", "max_rows_per_request": 100, "ready_rows": 70},
        "cedar": {"receiver_revision": "archive-11", "max_rows_per_request": 40, "ready_rows": 70},
    },
}


def case_05_validate_batch_size(batch_size: int, config: Dict[str, Any]) -> List[str]:
    violations = []
    for site, site_cfg in config.get("sites", {}).items():
        limit = site_cfg["max_rows_per_request"]
        if batch_size > limit:
            violations.append(
                f"Site {site} receiver ({site_cfg['receiver_revision']}) rejects batch size {batch_size} (exceeds limit {limit})"
            )
    return violations


# ============================================================================
# Case 06: Use New Delivery Queue
# ============================================================================

CASE_06_SUBSCRIPTIONS = {
    "observation": "subscriptions-416",
    "observed_at": "2026-10-21T09:00:00Z",
    "window_end": "2026-10-21T09:30:00Z",
    "sites": {
        "elm": {"subscribed_queues": ["reports"], "new_jobs_due_in_window": 8},
        "cedar": {"subscribed_queues": ["reports", "report-dispatch"], "new_jobs_due_in_window": 8},
    },
}


def case_06_validate_queue_routing(target_queue: str, subs: Dict[str, Any]) -> List[str]:
    unserved_sites = []
    for site, site_data in subs.get("sites", {}).items():
        if target_queue not in site_data["subscribed_queues"]:
            unserved_sites.append(
                f"Site {site} missing subscription for '{target_queue}'; {site_data['new_jobs_due_in_window']} jobs will stall"
            )
    return unserved_sites


# ============================================================================
# Case 07: Index Pending Deliveries
# ============================================================================

CASE_07_INDEX_SQL = """
CREATE INDEX deliveries_due_by_queue ON deliveries (queue, scheduled_at, id)
WHERE delivered_at IS NULL;
"""


def case_07_apply_and_verify_index(db: sqlite3.Connection) -> str:
    db.executescript(CASE_07_INDEX_SQL)
    plan = db.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT id FROM deliveries WHERE queue = ? AND delivered_at IS NULL "
        "AND scheduled_at <= ? ORDER BY scheduled_at, id",
        ("reports", 100),
    ).fetchall()
    return " | ".join(row[3] for row in plan)


# ============================================================================
# Case 08: Include Total in Activity Summary
# ============================================================================

def case_08_summary(db: sqlite3.Connection) -> Dict[str, int]:
    sent, waiting = db.execute(
        "SELECT count(delivered_at), count(*) - count(delivered_at) FROM deliveries"
    ).fetchone()
    return {"delivered": sent, "pending": waiting, "total": sent + waiting}


# ============================================================================
# Self-Test and Empirical Verification Runner
# ============================================================================

def verify_all_cases() -> bool:
    print("======================================================================")
    print("   RUNNING ALL 8 CASES VERIFICATION SUITE")
    print("======================================================================\n")
    all_passed = True

    # Case 01 Check
    print("[Case 01] Keyset pagination tie-breaker check...")
    db = init_database()
    for i in range(1, 6):
        db.execute(
            "INSERT INTO deliveries (id, report, scheduled_at, queue) VALUES (?, ?, ?, ?)",
            (i, f"rep_{i}", 100, "reports"),
        )
    page1_buggy = case_01_history_buggy(db, limit=2)
    page2_buggy = case_01_history_buggy(db, limit=2, cursor=page1_buggy.next_cursor)
    total_retrieved_buggy = len(page1_buggy.rows) + len(page2_buggy.rows)
    assert total_retrieved_buggy == 2, f"Expected buggy to skip tied rows, got {total_retrieved_buggy}"

    page1_fixed = case_01_history_fixed(db, limit=2)
    page2_fixed = case_01_history_fixed(db, limit=2, cursor=page1_fixed.next_cursor)
    page3_fixed = case_01_history_fixed(db, limit=2, cursor=page2_fixed.next_cursor)
    total_retrieved_fixed = len(page1_fixed.rows) + len(page2_fixed.rows) + len(page3_fixed.rows)
    assert total_retrieved_fixed == 5, f"Expected fixed to retrieve all 5 rows, got {total_retrieved_fixed}"
    print("  -> PASSED: Demonstrated tie-breaker row skipping and compound cursor fix.")

    # Case 02 Check
    print("[Case 02] CSV streaming export formatting check...")
    db = init_database()
    db.execute(
        "INSERT INTO deliveries (id, report, scheduled_at, queue) VALUES (?, ?, ?, ?)",
        (1, 'Sales, "North", Q3', 100, "reports"),
    )
    buf_buggy = io.StringIO()
    case_02_write_export_buggy(db, buf_buggy)
    buf_buggy.seek(0)
    parsed_buggy = list(csv.reader(buf_buggy))
    # Unquoted comma splits the single report column into multiple unexpected columns
    assert len(parsed_buggy[1]) > 3, "Expected buggy CSV export to corrupt columns"

    buf_fixed = io.StringIO()
    case_02_write_export_fixed(db, buf_fixed)
    buf_fixed.seek(0)
    parsed_fixed = list(csv.reader(buf_fixed))
    assert len(parsed_fixed[1]) == 3 and parsed_fixed[1][1] == 'Sales, "North", Q3'
    print("  -> PASSED: Demonstrated CSV column corruption and quoted writer fix.")

    # Case 03 Check
    print("[Case 03] Queued delivery batch remainder drop check...")
    db = init_database()
    for i in range(1, 26):
        db.execute(
            "INSERT INTO deliveries (id, report, scheduled_at, queue) VALUES (?, ?, ?, ?)",
            (i, f"job_{i}", 50, "reports"),
        )
    processed_buggy = case_03_deliver_due_buggy(db, "reports", 100)
    assert processed_buggy == 20, f"Expected buggy loop to drop 5 remainder jobs, got {processed_buggy}"

    db = init_database()
    for i in range(1, 26):
        db.execute(
            "INSERT INTO deliveries (id, report, scheduled_at, queue) VALUES (?, ?, ?, ?)",
            (i, f"job_{i}", 50, "reports"),
        )
    processed_fixed = case_03_deliver_due_fixed(db, "reports", 100)
    assert processed_fixed == 25, f"Expected fixed loop to deliver all 25 jobs, got {processed_fixed}"
    print("  -> PASSED: Demonstrated remainder job dropping (20/25) and full batch delivery (25/25).")

    # Case 04 Check
    print("[Case 04] Schema migration online worker compatibility check...")
    db = init_database()
    failures = case_04_simulate_migration(db, CASE_04_INVENTORY)
    assert len(failures) == 1 and "workers-17" in failures[0]
    print(f"  -> PASSED: Caught breaking migration on active workers: {failures[0]}")

    # Case 05 Check
    print("[Case 05] Export batch size receiver limit check...")
    batch_50_violations = case_05_validate_batch_size(50, CASE_05_CONFIG)
    assert len(batch_50_violations) == 1 and "cedar" in batch_50_violations[0]
    print(f"  -> PASSED: Caught receiver limit violation: {batch_50_violations[0]}")

    # Case 06 Check
    print("[Case 06] Queue routing cutover subscriber check...")
    unserved = case_06_validate_queue_routing("report-dispatch", CASE_06_SUBSCRIPTIONS)
    assert len(unserved) == 1 and "elm" in unserved[0]
    print(f"  -> PASSED: Caught unserved queue stall: {unserved[0]}")

    # Case 07 Check
    print("[Case 07] Partial index creation and query planner check...")
    db = init_database()
    plan_desc = case_07_apply_and_verify_index(db)
    assert "deliveries_due_by_queue" in plan_desc
    print(f"  -> PASSED: Verified optimizer plan: {plan_desc}")

    # Case 08 Check
    print("[Case 08] Summary total calculation check...")
    db = init_database()
    empty_summary = case_08_summary(db)
    assert empty_summary == {"delivered": 0, "pending": 0, "total": 0}
    db.execute("INSERT INTO deliveries (id, report, scheduled_at, queue, delivered_at) VALUES (1, 'r', 10, 'q', 12)")
    db.execute("INSERT INTO deliveries (id, report, scheduled_at, queue, delivered_at) VALUES (2, 'r', 10, 'q', NULL)")
    pop_summary = case_08_summary(db)
    assert pop_summary == {"delivered": 1, "pending": 1, "total": 2}
    print(f"  -> PASSED: Verified summary invariant on empty and populated states.")

    print("\n======================================================================")
    print("   ALL 8 CASES VERIFIED SUCCESSFULLY (8/8 PASSED)")
    print("======================================================================\n")
    return all_passed


if __name__ == "__main__":
    success = verify_all_cases()
    sys.exit(0 if success else 1)
