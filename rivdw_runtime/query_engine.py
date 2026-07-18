"""Phase 1 runtime walking skeleton (see IMPLEMENTATION_SUMMARY.md, "Runtime build plan").

Takes a plain-English question, runs a plain vector search against the build-time
Qdrant metadata store, and asks the LLM to draft one SQL query. The SQL is returned
for display only — it is never executed. Deliberately excludes HyDE, hybrid/BM25
search, reranking, and parent/child expansion; those are Phase 2, added later and
only if they measurably improve results on eval/golden_set.json.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

# This walking skeleton reuses rivdw_buildtime's Qdrant store, embedding model, and
# LLM-calling code rather than duplicating them. That code assumes rivdw_buildtime is
# both on sys.path (its own imports are flat, e.g. `from vector_store...`) and the
# process's working directory (its .env paths like QDRANT_PATH=./qdrant_data are
# relative) — so both are set up here before anything from it is imported.
_BUILDTIME_ROOT = Path(__file__).resolve().parent.parent / "rivdw_buildtime"
if str(_BUILDTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUILDTIME_ROOT))
os.chdir(_BUILDTIME_ROOT)

from config.settings import get_settings  # noqa: E402
from pipeline.nodes.enrich_node import _call_llm  # noqa: E402
from vector_store.qdrant_store import get_store  # noqa: E402

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompts import QueryPrompts  # noqa: E402

TOP_K = 5


class RetrievedEntry(NamedTuple):
    source_db: str
    table_name: str
    column_name: str
    description: str
    score: float


class QueryResult(NamedTuple):
    question: str
    retrieved: List[RetrievedEntry]
    sql: str
    explanation: str
    raw_llm_response: str


def retrieve(question: str, top_k: int = TOP_K) -> List[RetrievedEntry]:
    """Plain dense-vector search over the build-time metadata store — no HyDE, no
    hybrid search, no reranking. Phase 1 is deliberately the simplest retrieval."""
    store = get_store()
    hits = store.search(question, limit=top_k)
    return [
        RetrievedEntry(
            source_db=h.get("source_db", ""),
            table_name=h.get("table_name", ""),
            column_name=h.get("column_name", ""),
            description=h.get("description", ""),
            score=h.get("score", 0.0),
        )
        for h in hits
    ]


def answer_question(question: str, top_k: int = TOP_K) -> QueryResult:
    """Run the Phase 1 pipeline end to end: retrieve, then draft SQL. Never executes it."""
    retrieved = retrieve(question, top_k=top_k)

    if not retrieved:
        return QueryResult(
            question=question,
            retrieved=[],
            sql="",
            explanation="No relevant tables or columns were found in the metadata store for this question.",
            raw_llm_response="",
        )

    settings = get_settings()
    prompt = QueryPrompts.SQL_DRAFT_TEMPLATE.format(
        question=question,
        schema_context=_format_schema_context(retrieved),
    )
    raw_response, _tokens_used = _call_llm(prompt, settings)
    sql, explanation = _parse_sql_response(raw_response)

    return QueryResult(
        question=question,
        retrieved=retrieved,
        sql=sql,
        explanation=explanation,
        raw_llm_response=raw_response,
    )


def _format_schema_context(entries: List[RetrievedEntry]) -> str:
    lines = []
    for e in entries:
        location = e.table_name if not e.column_name else f"{e.table_name}.{e.column_name}"
        lines.append(f"- [{e.source_db}] {location}: {e.description}")
    return "\n".join(lines)


def _parse_sql_response(raw: str) -> Tuple[str, str]:
    """Parse the LLM's JSON response, tolerating markdown fences or stray surrounding
    text — same three-strategy fallback pattern already used in
    pipeline/single_db_pipeline.py's _parse_batch_response."""
    if not raw:
        return "", "The model returned an empty response."

    cleaned = raw.strip()
    candidates = [cleaned, _strip_code_fences(cleaned), _extract_braces(cleaned)]

    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            return str(data.get("sql", "")), str(data.get("explanation", ""))
        except json.JSONDecodeError:
            continue

    logger.warning("Could not parse SQL-draft response as JSON. Raw (first 300 chars): %s", cleaned[:300])
    return "", "Could not parse the model's response as JSON — see raw output for details."


def _strip_code_fences(text: str) -> Optional[str]:
    if "```" not in text:
        return None
    lines = [line for line in text.split("\n") if not line.strip().startswith("```")]
    return "\n".join(lines).strip()


def _extract_braces(text: str) -> Optional[str]:
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None
