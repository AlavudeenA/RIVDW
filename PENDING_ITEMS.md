# RIVDW — Pending / To-Be-Implemented Items

Everything in this file is **planned but not built**. It's the backlog companion to
`IMPLEMENTATION_SUMMARY.md` (same folder) — that file explains what exists and why; this one
tracks what doesn't exist yet. Item numbers/step names cross-reference `IMPLEMENTATION_SUMMARY.md`
so you can go back for the full reasoning behind any line here.

---

## Acknowledge button → approved-query reuse loop (concrete design, new)

Extends research item #10 / the "Trajectory Builder" idea from `docs/Rearch_Papers_Summary.md`
with an actual UI trigger. Right now every question is answered from scratch — no memory of past
interactions, so the same recurring business question (e.g. "show me pending trade requests")
gets re-derived from zero every time, with no guarantee of the same correct answer twice. This
turns real usage into a feedback loop, on top of Phase 1's already-built result view.

**Placement note:** unlike Phase 2 (which improves accuracy for a *fresh* question), this improves
behavior on *repeat/similar* questions over time — a different, largely independent axis. It only
needs Phase 1 (already ✅ built), not Phase 2 — could reasonably be picked up in parallel with, or
even before, the Phase 2 accuracy layers.

- [ ] **UI: Acknowledge / Reject buttons on the Query screen** — after the drafted SQL renders
      (`ui/pages/query.py`), add "✅ This is correct" and "❌ Not quite right" buttons. The
      acknowledge button is the trigger; reject is nearly free to add alongside it and gives a
      symmetric signal (see below).
- [ ] **Store what gets acknowledged as an `approved_trajectory` entry** — the schema for this is
      already sketched in `docs/Rearch_Papers_Summary.md` (question, `tables_chosen`,
      `why_these_tables`, `join_path`, `sql`, `approved_by`, `approved_at`). On click, save the
      question text, the exact tables/columns that were retrieved and used, the drafted SQL, and a
      timestamp — as its own Qdrant entry type, separate from `schema_metadata`.
- [ ] **Tier A — few-shot context (cheapest, build first):** at query time, search the approved
      trajectory entries alongside the normal schema search. If similar past approved trajectories
      come back, include them as worked examples in the SQL-drafting prompt ("a similar question
      was answered correctly before: ..."), nudging the LLM toward validated patterns without any
      risky shortcuts.
- [ ] **Tier B — near-duplicate short-circuit:** if a new question's similarity to a stored
      trajectory is above a high threshold, skip LLM generation entirely and reuse (or lightly
      adapt) the previously-approved SQL directly — faster, and guaranteed to match a
      human-approved answer instead of a fresh unverified draft. Needs care on the threshold, since
      "this quarter" vs. "last quarter" can look similar but mean different SQL.
- [ ] **Tier C — staleness-aware reuse:** if the build-time schema crawler later detects that a
      table used in an approved trajectory has changed structure, flag that trajectory
      `needs_review` (same status vocabulary as the guardian) so stale approved answers don't get
      silently reused forever. Was already scoped conceptually in the Trajectory Builder notes —
      the acknowledge button is what actually populates the data this depends on.
- [ ] **Use the reject signal too** — doesn't need a correction flow immediately; even just
      recording "this SQL was rejected for this question" is useful to exclude bad examples from
      future few-shot context, and gives a live accuracy signal from real usage, on top of the
      static golden eval set.

---

## Runtime Phase 2 — accuracy layers (retrieval-side)

Build-time's one planned change (the context-prefixed embedding, Step 1) is already done. These
five are the retrieval-side layers from "Our accurate-retrieval design," to be added **one at a
time**, each re-measured against the Phase 0 golden eval set (`rivdw_runtime/eval/`), kept only if
it measurably raises Recall@K:

- [ ] **HyDE query rewriting** (Step 2) — before searching, have the LLM draft a hypothetical
      answer to the question and embed that instead of the raw question.
- [ ] **Hybrid dense + BM25 search with RRF fusion** (Step 3) — run a keyword search alongside the
      vector search and merge the two ranked lists, so exact codes/IDs/abbreviations aren't missed.
- [ ] **Cross-encoder reranking** (Step 4) — re-score the merged shortlist with a slower, more
      careful model before returning the final top results. Flagged as the single highest-leverage
      accuracy step in the source research.
- [ ] **Parent/child expansion** (Step 5) — when a column entry matches, pull in its sibling
      table-level entry (and vice versa) before handing context to the LLM.
- [ ] **Trust-based tie-breaking** (Step 6) — prefer `human_verified: true` / `guardian_status:
      approved` entries over LLM-only `pending` ones at equal similarity. Also covers research item
      #4 ("governance-aware ranking").

---

## Runtime Phase 3 — execution and safety

Only starts once Phase 2 is measured and trustworthy — there's no point running SQL generated from
unverified retrieval.

- [ ] **Read-only SQL execution** — a separate DB connection from the one used for schema crawling,
      with a row limit and a timeout.
- [ ] **Execution-guided self-correction** — if the generated SQL errors or returns nothing, feed
      that error back to the LLM for one bounded retry before giving up (research item #8).
- [ ] **Confidence-based auto-answer vs. human review** — generate a few candidate SQL statements,
      execute all, and use result agreement as a confidence score to decide auto-answer vs. routing
      to a human (research item #9).
- [ ] **Deterministic checker chain before trusting generated SQL** — cheap non-LLM checks (valid
      syntax, sane JOINs, no accidental `SELECT *`, empty-result detection), the same
      rules-based-gate philosophy as the build-time guardian, applied to SQL instead of
      descriptions (research item #11).

---

## Build-time pipeline upgrades (not yet scheduled into a phase)

Cheap, build-time-only improvements from the research pass that aren't part of the runtime phases
above:

- [ ] **Flag join/foreign-key columns explicitly in metadata** (research item #2) — make the
      enrichment prompt explicitly ask "is this a join key, and to what other table/column?"
      instead of leaving it implicit in `related_tables`.
- [ ] **Mine a synonym/glossary layer across DB types** (research item #3) — resurrect the
      glossary feature from the original design (dead/empty `glossary/` and `glossary_data/`
      folders currently sit unused in `rivdw_buildtime/`) and feed it into the enrichment prompt so
      the same business concept gets a consistent description across SQL Server and Oracle.

---

## Runtime architecture ideas not yet scheduled into a phase

From the research "Blueprint for the runtime phase" — directionally agreed as good ideas, but not
yet slotted into Phase 2/3 above:

- [ ] **Two-stage retrieval: pick the database first, then the tables** (research item #5) — with
      multiple source databases, rank which database is relevant before drilling into its tables.
- [ ] **Embed example questions per table** (research item #6) — generate a handful of
      representative plain-English questions per table (validated against real SQL) and embed
      those alongside the description, so query-to-query matching can beat query-to-description
      matching.
- [ ] **Hybrid schema linking with relational closure** (research item #7) — combine direct LLM
      linking, "draft SQL first and see what schema it used," and value-based matching, then
      auto-include any FK-connected table so joins stay possible.

---

## Explicitly deferred pending a decision (not just "not started")

- [ ] **Sampling real column values at build-time** (research item #1) — genuinely useful for
      description quality, but means sending real production data (compliance/HR/brokerage) to a
      third-party LLM API. **Blocked on an explicit data-governance yes/no from whoever owns that
      call** — not to be built on a default-on assumption just because the research recommends it.

---

## Looked at and explicitly rejected — not on this backlog

Listed for completeness, not as pending work. Revisit only if the stated condition changes:

- **Quantized/optimized ANN search and pattern-constrained vector search** — solves problems at a
  scale (millions–billions of vectors) far beyond RIVDW's metadata store. Revisit only if the
  vector store grows by orders of magnitude.
- **Fine-tuning an LLM on RIVDW's own schemas** — no dedicated ML infrastructure or labeled
  training data exists; the prompting-based approach (Groq + written prompts) is the right call
  per the research until that changes.
