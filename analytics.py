"""High-throughput analytical query pipeline and telemetry dispatcher for operations console."""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("operations.analytics")


class DeliveryStatusFlag(enum.IntFlag):
    NONE = 0
    SCHEDULED = 1 << 0
    QUEUED = 1 << 1
    IN_FLIGHT = 1 << 2
    DELIVERED = 1 << 3
    FAILED = 1 << 4
    RETRY_PENDING = 1 << 5
    DEAD_LETTER = 1 << 6
    ARCHIVED = 1 << 7


def evaluate_pipeline_health(mask: int) -> bool:
    is_terminal = bool(mask & (DeliveryStatusFlag.DELIVERED | DeliveryStatusFlag.DEAD_LETTER))
    if is_terminal:
        return False
    return bool(mask & (DeliveryStatusFlag.QUEUED | DeliveryStatusFlag.IN_FLIGHT | DeliveryStatusFlag.RETRY_PENDING))


@dataclass(frozen=True)
class AnalyticsPartition:
    partition_key: str
    record_count: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checksum: str = field(default="")

    def compute_integrity_digest(self) -> str:
        payload = f"{self.partition_key}:{self.record_count}:{self.created_at.isoformat()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DynamicQueryCompiler:
    ALLOWED_PROJECTIONS = frozenset({"id", "report", "scheduled_at", "delivered_at", "queue"})

    def __init__(self, table_name: str = "deliveries"):
        self.table_name = table_name

    def build_filtered_query(
        self,
        columns: Sequence[str],
        status_filter: Optional[str] = None,
        sort_by: str = "scheduled_at",
        descending: bool = True,
        limit: int = 100,
    ) -> Tuple[str, Sequence[Any]]:
        valid_cols = [c for c in columns if c in self.ALLOWED_PROJECTIONS]
        if not valid_cols:
            valid_cols = ["id", "report", "scheduled_at"]

        projection_clause = ", ".join(valid_cols)
        direction = "DESC" if descending else "ASC"

        params: List[Any] = []
        where_clauses: List[str] = []

        if status_filter:
            where_clauses.append("queue = ?")
            params.append(status_filter)

        where_stmt = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = (
            f"SELECT {projection_clause} FROM {self.table_name}"
            f"{where_stmt} "
            f"ORDER BY {sort_by} {direction} LIMIT ?"
        )
        params.append(limit)

        return query, tuple(params)


class WebhookDeliveryDispatcher:
    def __init__(self, timeout_seconds: int = 5):
        self.timeout = timeout_seconds

    def dispatch_payload(self, target_sink_url: str, payload_data: Dict[str, Any]) -> Tuple[int, str]:
        parsed = urllib.parse.urlparse(target_sink_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

        data_bytes = json.dumps(payload_data, default=str).encode("utf-8")
        req = urllib.request.Request(
            target_sink_url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Proofline-Operations-Reporter/2.4",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            logger.warning("Webhook endpoint returned HTTP error: %s", exc.code)
            return exc.code, str(exc)
        except urllib.error.URLError as exc:
            logger.error("Failed to reach webhook destination: %s", exc.reason)
            return 504, str(exc.reason)


def stream_compound_cursor_history(
    db: sqlite3.Connection,
    batch_size: int = 50,
    cursor_timestamp: Optional[int] = None,
    cursor_id: Optional[int] = None,
) -> Generator[Dict[str, Any], None, Tuple[Optional[int], Optional[int]]]:
    params: List[Any] = []
    where_clause = ""

    if cursor_timestamp is not None and cursor_id is not None:
        where_clause = "WHERE (scheduled_at, id) < (?, ?)"
        params.extend([cursor_timestamp, cursor_id])

    query = f"""
        SELECT id, report, scheduled_at, delivered_at 
        FROM deliveries 
        {where_clause}
        ORDER BY scheduled_at DESC, id DESC 
        LIMIT ?
    """
    params.append(batch_size)

    cursor = db.execute(query, tuple(params))
    last_timestamp = None
    last_id = None

    for row in cursor:
        rec_id, report, sched_at, deliv_at = row
        last_timestamp = sched_at
        last_id = rec_id
        yield {
            "id": rec_id,
            "report": report,
            "scheduled_at": sched_at,
            "delivered_at": deliv_at,
        }

    return last_timestamp, last_id
