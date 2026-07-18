"""Phase 0 eval runner (see IMPLEMENTATION_SUMMARY.md, "Runtime build plan").

Measures Recall@K of the current retrieval step against eval/golden_set.json — the
yardstick every later Phase 2 change (HyDE, hybrid search, reranking, ...) gets
measured against before being kept. Retrieval-only: does not call the LLM, so it's
fast and free to run after every change.

Usage:
    python eval/run_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from query_engine import RetrievedEntry, retrieve  # noqa: E402

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.json"
TOP_K = 5


def main() -> None:
    cases = json.loads(GOLDEN_SET_PATH.read_text())

    table_hits = 0
    column_hits = 0
    column_cases = 0

    for case in cases:
        question = case["question"]
        expected_tables = {t.lower() for t in case.get("expected_tables", [])}
        expected_columns = {c.lower() for c in case.get("expected_columns", [])}

        retrieved = retrieve(question, top_k=TOP_K)
        retrieved_tables = {r.table_name.lower() for r in retrieved}
        retrieved_columns = {r.column_name.lower() for r in retrieved if r.column_name}

        table_hit = bool(expected_tables & retrieved_tables)
        table_hits += table_hit

        if expected_columns:
            column_cases += 1
            column_hit = bool(expected_columns & retrieved_columns)
            column_hits += column_hit
        else:
            column_hit = None

        _print_case(question, table_hit, column_hit, retrieved)

    total = len(cases)
    print("\n" + "=" * 70)
    print(f"Table Recall@{TOP_K}:  {table_hits}/{total}  ({table_hits / total:.0%})")
    if column_cases:
        print(f"Column Recall@{TOP_K}: {column_hits}/{column_cases}  ({column_hits / column_cases:.0%})")
    print("=" * 70)


def _print_case(question: str, table_hit: bool, column_hit, retrieved: list) -> None:
    mark = "PASS" if table_hit else "FAIL"
    col_mark = "" if column_hit is None else (" | col:PASS" if column_hit else " | col:FAIL")
    print(f"\n[{mark}{col_mark}] {question}")
    for r in retrieved:
        loc = r.table_name if not r.column_name else f"{r.table_name}.{r.column_name}"
        print(f"    {r.score:.3f}  {loc}")


if __name__ == "__main__":
    main()
