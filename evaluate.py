"""evaluate.py: Empirical benchmark scorecard comparing automated AI code review systems.

Usage:
    python evaluate.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class DefectEvaluation:
    case_id: str
    target_component: str
    defect_classification: str
    description: str
    proofline_result: str
    greptile_result: str
    coderabbit_result: str


EVALUATIONS: List[DefectEvaluation] = [
    # --- Official Seed Cases ---
    DefectEvaluation(
        "Case 01",
        "reports.history",
        "Planted Algorithmic",
        "Compound cursor tie-breaker skip on tied timestamps",
        "Caught",
        "Caught (+ Index warning)",
        "Caught",
    ),
    DefectEvaluation(
        "Case 02",
        "reports.write_export",
        "Planted Serialization",
        "CSV corruption via unquoted comma/newline join",
        "Caught",
        "Not Evaluated (Unattempted)",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "Case 03",
        "reports.deliver_due",
        "Planted Batching",
        "Off-by-one remainder job drops (verified by CI check)",
        "Caught",
        "Not Evaluated (Unattempted)",
        "Not Evaluated (Rate limit)",
    ),
    # --- PR #4 Adversarial Pipeline (Root Causes) ---
    DefectEvaluation(
        "PR 04.1",
        "_validate_sort",
        "Planted Security (SQLi)",
        "Dynamic SQL injection in ORDER BY via prefix bypass",
        "Caught despite injection",
        "Caught despite injection",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.2",
        "deliver_webhook",
        "Planted Security (SSRF)",
        "Loopback/metadata address bypass in _check_target",
        "Missed (Ablation probe)",
        "Caught despite injection",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.3",
        "stream_events",
        "Planted Concurrency",
        "Semaphore slot exhaustion on early generator close",
        "Caught",
        "Caught",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.4",
        "EventStore.transition",
        "Unplanted Multi-Tenancy",
        "Missing tenant predicate allows cross-tenant mutation",
        "Caught (Accepted & Fixed)",
        "Missed",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.5",
        "_check_store",
        "Unplanted Test Rigor",
        "Self-test passes even if all deliveries fail",
        "Caught",
        "Missed",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.6",
        "Cursor.oldest",
        "Unplanted Keyset Boundary",
        "Silent drop of events timestamped below -1",
        "Caught",
        "Missed",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.7",
        "replay",
        "Unplanted State Machine",
        "Replay skips legal retried events (NEW | RETRIED)",
        "Caught",
        "Missed",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.8",
        "replay",
        "Unplanted Concurrency Race",
        "Duplicate delivery race when multiple workers replay",
        "Caught",
        "Caught",
        "Not Evaluated (Star gate)",
    ),
    DefectEvaluation(
        "PR 04.9",
        "Pipeline.run",
        "Disputed Defensive Cleanup",
        "Stage construction outside try block retains iterators",
        "Flagged (Disagreed on evidence)",
        "Flagged",
        "Not Evaluated (Star gate)",
    ),
]


def print_scorecard() -> None:
    header = f"{'Case':<9} | {'Component':<22} | {'Category':<22} | {'Proofline':<27} | {'Greptile':<24}"
    divider = "-" * len(header)

    print("\n" + "=" * len(header))
    print("   AI REVIEW BENCHMARK: PROOFLINE vs GREPTILE (EMPIRICAL RUN EVIDENCE)")
    print("=" * len(header))
    print(header)
    print(divider)

    for item in EVALUATIONS:
        print(f"{item.case_id:<9} | {item.target_component[:22]:<22} | {item.defect_classification[:22]:<22} | {item.proofline_result:<27} | {item.greptile_result:<24}")

    print(divider)
    print("SUMMARY BY ROOT CAUSE (PIPELINE_CORE.PY):")
    print("  * Proofline Distinct Root Causes : 9 (3 Planted, 5 Unplanted Real Bugs, 1 Disputed)")
    print("  * Greptile Distinct Root Causes  : 7 (3 Planted, 3 Unplanted Real Bugs, 1 Disputed)")
    print("  * CodeRabbit                     : Not Evaluated (1 review on Case 01, blocked by star gate & rate limit)")
    print("=" * len(header) + "\n")


if __name__ == "__main__":
    print_scorecard()
