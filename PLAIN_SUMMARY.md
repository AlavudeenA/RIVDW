# RIVDW — What's Built So Far (Plain English)

## The big idea

RIVDW stands for **Report Intelligence Virtual DW**. The end goal is a system where a business
user can type a plain-English question like _"show me all compliance violations this quarter"_
and the system figures out, on its own, which database tables and columns hold that answer —
without the user knowing any SQL or table names.

To do that, the system needs two halves:

1. **Build-time** — teach the system what your databases contain, _before_ anyone asks a question.
2. **Runtime** — let a user ask a question and have the system use that knowledge to find and
   return an answer.

**Build-time is fully built.** Runtime now has a working first slice too — see "Runtime build
plan" below for exactly what exists (Phases 0–1) versus what's still ahead (Phases 2–3). Note:
"Phase" below refers to the runtime build plan's own numbering, not these two halves.

---

## How to run it

```bash
cd rivdw_buildtime
pip install -r requirements.txt
cp .env.example .env   # add your Groq API key and DB connection strings
python -m streamlit run ui/app.py
```

---

## Tech stack, in one line each

- **AI model**: Groq's `llama-3.3-70b-versatile`, with a fallback to a local VS Code language
  model extension if Groq is unavailable.
- **Vector database**: Qdrant, running locally as files on disk (not a separate server).
- **Embeddings**: FastEmbed's `bge-base-en-v1.5` model — turns text into 768-number vectors
  for similarity search.
- **Databases supported**: SQL Server and Oracle, read-only, structure-only (via SQLAlchemy).
- **History/audit log**: a local SQLite file (`rivdw_app.db`).
- **UI**: Streamlit (Python web app framework) — no separate frontend/backend split.

---

## `rivdw_buildtime/`

This is a working Streamlit web app you can run locally. In plain terms, it does this:

1. **Connects to your databases** (SQL Server and Oracle) and reads the _structure_ only —
   the list of tables and columns. It never reads or stores actual business data/rows.
2. **Asks an AI model (Groq)** to write a plain-English description for every table and
   column: what it stores, what business process uses it, and what a business analyst
   would call it. It makes **one AI call per table** (covering all its columns at once) to
   keep this fast and cheap.
3. **Checks the AI's work** with a rules-based "guardian" step: is the description long
   enough, free of vague filler phrases, and tagged with a known business domain? Each
   entry ends up marked `approved`, `needs_review`, `rejected`, or `pending`.
4. **Stores the approved descriptions** in a local vector database (Qdrant), turning each
   description into a numeric "embedding" so that later, a fuzzy or differently-worded
   question can still find the right table.
5. **Keeps a full history**: every version of every description — AI-generated, human-edited,
   or regenerated — is archived in a local SQLite file, so nothing is ever silently overwritten.

### The screens (Streamlit UI)

| Screen             | What you can do there                                                                                                                                                                                                  |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Build Metadata** | The main screen. Pick a database, click Generate, watch the AI describe each table live, edit any description inline, save, export everything to Excel, re-import an edited Excel, and browse the full change history. |
| **Query**          | Type a plain-English question, get back the matched tables/columns, a short explanation, and one drafted SQL query (not executed). This is Phase 1 of the runtime build plan below — the simplest version of retrieval, no hybrid search or reranking yet. |

### Human-in-the-loop workflow that's actually implemented

- Every AI-generated description can be **edited by a person** directly in the browser.
- You can **export** a database's metadata to an Excel file (with read-only ID columns
  greyed out), hand it to a business/domain expert to fill in descriptions and notes offline,
  then **import** it back — the system validates rows before applying them.
- Every save, edit, or regeneration is timestamped and archived, so you can see who changed
  what and when.

---

## Runtime build plan — what we're actually building, and in what order

Everything below turns the research and the retrieval design (further down this file) into a
build sequence, not a wishlist. The rule going forward: **nothing new gets added until the
previous phase is working and has been measured against real questions.** This plan exists
specifically because the earlier approach — read research, design the full accurate pipeline,
then start adding pieces of it (HyDE, hybrid search, reranking) — was getting ahead of having any
working retrieval to measure at all. So we're inverting the order: build the smallest possible
end-to-end slice first, then layer in accuracy improvements only where measurement shows they're
needed.

### Phase 0 — golden eval set (before any runtime code) — ✅ built

25 realistic business questions grounded in the real generated `compliance_db` metadata (trade
requests, approval workflows, compliance alerts, restricted securities, broker-dealers, accounts,
employees), each mapped to the table(s)/column(s) that should answer it —
[`rivdw_runtime/eval/golden_set.json`](rivdw_runtime/eval/golden_set.json). Run it with
`python eval/run_eval.py` from `rivdw_runtime/`. Current baseline: **Table Recall@5: 100%
(25/25), Column Recall@5: 96% (24/25)** — the one miss ("how long does it typically take to
review a trade request" → expected `turnarounddays`, got `reviewdate`) is a legitimate target for
Phase 2, not something to chase now.

### Phase 1 — walking skeleton (get one real question answered, end to end) — ✅ built

The smallest thing that can be called "runtime": user types a question → embed it with the same
model already used at build-time → plain Qdrant top-5 similarity search (no HyDE, no hybrid
search, no reranker yet) → hand those 5 descriptions to Groq and ask it to draft one SQL query →
**show the generated SQL to the user, don't execute it.** No database writes, no auto-run, just
"here's what I think the answer looks like." Lives in
[`rivdw_runtime/query_engine.py`](rivdw_runtime/query_engine.py), wired into the **Query** screen
in `rivdw_buildtime` (`ui/pages/query.py`) — the "Search logic coming soon" placeholder is gone.
Verified end-to-end in a real browser: a question returns matched schema entries, a plain-English
explanation, and a drafted SQL block, with no console errors.

Deliberately left out of Phase 1: HyDE, hybrid/BM25 search, reranking, parent/child expansion,
trust tie-breaking, execution, confidence scoring, trajectory reuse. All of it comes later, one
piece at a time, only if Phase 0's eval set shows it's needed.

One bug fixed along the way: `qdrant_store.py`'s `search()` called `QdrantClient.search()`, which
doesn't exist in the installed `qdrant-client` version — it was replaced with `.query_points()`
upstream. This had never been exercised end-to-end before (the Query screen was a placeholder, so
nothing ever called it), which is exactly the kind of gap "no tests anywhere" lets through.

### Phase 2 — add accuracy layers one at a time (each re-measured against Phase 0)

In this order, per the priority list already worked out below — add one, re-run the eval set,
keep it only if it measurably helps:

1. HyDE query rewriting (Step 2 below)
2. Hybrid dense + BM25 search with RRF fusion (Step 3 below)
3. Cross-encoder reranking (Step 4 below)
4. Parent/child expansion (Step 5 below)
5. Trust-based tie-breaking (Step 6 below)

### Phase 3 — execution and safety, only after Phase 2 is trustworthy

Once retrieval is measured and reliable, allow the generated SQL to actually run — but only
against a **read-only connection with a row limit and a timeout**, never the same credentials used
for schema crawling. Add execution-guided self-correction (retry once on error/empty result) and
confidence-based auto-answer vs. human review (from the research list below) at this stage, not
before — there's no point auto-executing SQL that was generated from unmeasured retrieval.

### What's deliberately deferred, and why

- **Sampling real column values at build-time** (item 1 in the research list below) is _not_ part
  of this runtime plan. It's a genuinely useful accuracy idea, but it also means sending real
  production data — from compliance/HR/brokerage systems — to a third-party LLM API, which is a
  data-governance decision, not just an engineering one. It needs an explicit yes/no from whoever
  owns that call before it gets built, not a default-on assumption because a research paper
  recommended it.
- **Trajectory reuse / approved query cache** (item 10) and **the domain glossary** (item 3) are
  real value-adds but depend on runtime already existing and being used — there's nothing to reuse
  or standardize against until Phase 1 is live and answering real questions.

---

## Research-backed ideas worth adopting

We went through all 16 PDFs in `docs/` (3 already had notes in `docs/Rearch_Papers_Summary.md`;
the other 13 were read fresh for this pass). Below is what's actually specific and cheap enough to
build — not generic "use a better prompt" advice.

### Near-term: cheap upgrades to the build-time pipeline (already running)

1. **Sample real column values, not just structure.** Nearly every paper lands on the same point:
   reading table/column _names_ only misses the real problem — mismatched formats ("USA" vs
   "United States"), junk placeholder values, ambiguous codes. RIVDW's crawler currently reads
   structure only. Pulling the top ~10 distinct values per column (plus null % and distinct count)
   and feeding that into the Groq prompt — the same "profiling" technique already noted in
   `docs/Rearch_Papers_Summary.md` from the metadata-extraction paper — would sharpen descriptions
   and catch data-quality issues the guardian can't see today.
2. **Flag join/foreign-key columns explicitly in metadata.** Multiple papers separately land on
   this: a missing or undocumented join key is one of the single biggest causes of text-to-SQL
   failure (11.6% in one paper). RIVDW's descriptions already have a `related_tables` field — worth
   making the enrichment prompt explicitly ask "is this a join key, and to what other table/column?"
   instead of leaving it implicit.
3. **Mine a synonym/glossary layer across DB types.** Same business concept, different physical
   column name in SQL Server vs Oracle. A cross-table glossary fed into the enrichment prompt
   (Pinterest Engineering's approach) would standardize descriptions — this is exactly the glossary
   feature that was in the original design doc but never got built; worth resurrecting specifically
   for this reason.
4. **Use the existing guardian/approval status as a trust signal, not just a gate.** Right now
   `guardian_status` and `human_verified` only decide whether an entry gets stored. The
   "governance-aware ranking" idea (Pinterest) — folding trust/freshness into retrieval ranking, not
   just filtering — is nearly free to add once the Query screen does real search: rank
   human-verified entries above LLM-only ones at equal similarity.

### Blueprint for the (currently empty) runtime phase

`rivdw_runtime/` hasn't been started, so this is where the papers are most useful — several hand
over an almost-complete reference architecture:

5. **Two-stage retrieval: pick the database first, then the tables.** (LinkAlign, EMNLP 2025) With
   multiple source databases, search should rank _which database_ is relevant before drilling into
   its tables — not one flat search across everything at once.
6. **Embed example questions per table, not just descriptions.** (US Patent 12,450,272 "anchor
   queries"; Pinterest Engineering) Query-to-query matching beats query-to-description matching. At
   build-time — or once real usage exists — generate a handful of representative NL questions per
   table (validated by round-tripping them to real SQL) and embed those alongside the description.
7. **Hybrid schema linking with relational closure.** (DeepEye-SQL) Combine direct LLM linking,
   "draft SQL first and see what schema it used" (reversed linking), and value-based matching — then
   automatically pull in any FK-connected table so joins are always possible, instead of trusting a
   single vector-search pass.
8. **Execution-guided self-correction.** (Survey paper; DeepEye-SQL; Pinterest) Actually run the
   generated SQL; if it errors or returns nothing, feed that back to the LLM for one bounded retry
   before giving up. This is the single most commonly validated technique across the literature.
9. **Confidence-based auto-answer vs. human review.** (DeepEye-SQL) Generate a few candidate SQL
   statements, execute all of them, and use how often their results agree as a confidence score —
   high agreement auto-answers, low agreement routes to a human. This extends RIVDW's existing
   review culture (from the Build Metadata screen) into runtime instead of always trusting the first
   generated query.
10. **Reuse approved past queries.** Already scoped in `docs/Rearch_Papers_Summary.md` as the "Trajectory
    Builder" idea: store question + SQL + reasoning once a human approves it, and use it to help
    answer similar future questions. Pinterest's blog independently reaches the same conclusion from
    mining historical query logs, which reinforces that it's worth building.
11. **Deterministic checker chain before trusting generated SQL.** (DeepEye-SQL) A cascade of cheap,
    non-LLM checks (valid syntax, sane JOINs, no accidental `SELECT *`, empty-result detection)
    mirrors the guardian pattern already used for descriptions — the same rules-engine-plus-LLM-repair
    philosophy, applied to generated SQL instead of generated text.

### Looked at, not worth adopting yet

- **Quantized/optimized ANN search (AQR-HNSW) and pattern-constrained vector search (VectorMaton)**
  — both solve problems at a scale (millions to billions of vectors, huge sequence corpora) far
  beyond RIVDW's metadata store, which is a handful of databases' worth of table/column
  descriptions. Qdrant's defaults are already sufficient; revisit only if the store grows by orders
  of magnitude.
- **Fine-tuning an LLM on RIVDW's own schemas** — several papers explicitly recommend against this
  for teams without dedicated ML infrastructure or labeled training data. RIVDW's prompting-based
  approach (Groq + plain-English descriptions, no fine-tuning) is the right call per this research.

---

## Our accurate-retrieval design (the target end-state, built in the phases above)

`docs/Embeddings_BestPractise.md` lays out 14 separate retrieval/embedding techniques and judges each
one against RIVDW individually. This section combines the ones that survived that judgment into
**a single retrieval pipeline** — described here as one flow rather than a list of independent
choices. It is _not_ what gets built on day one: the "Runtime build plan" section above is the
actual order of work, starting from Phase 1's plain top-5 search. Steps 2–6 below correspond to
Phase 2's accuracy layers, added and measured one at a time rather than all at once.

### Step 0 — measure before building anything

Before writing any retrieval code, put together a small golden set: a few dozen realistic business
questions, each mapped to the table(s)/column(s) that should answer it. Every step below gets
measured against this set (Recall@K) before and after it's added — otherwise "accurate" is just a
guess. This is cheap and comes first on purpose.

### Step 1 — change what gets embedded at build-time (storage side)

Right now `save_entry()` embeds only `description + human_notes`
([qdrant_store.py:63-67](rivdw_buildtime/vector_store/qdrant_store.py#L63-L67)) — the table name,
column name, and database it belongs to are stored as payload but never make it into the vector
itself. Fix: embed a short contextual prefix along with the description, e.g.

```
"{db_display_name} > {table_name}.{column_name}: {description} {human_notes}"
```

This is the one build-time change in the whole plan — everything else below is retrieval-side and
touches the not-yet-built Query/runtime screen, not the working Build Metadata pipeline.

### Step 2 — when a question comes in, don't embed it raw

A business question ("show me compliance violations this quarter") and a column description
("stores the review status for flagged transactions") are written in completely different
registers — this mismatch, not embedding-model quality, is RIVDW's real accuracy risk. Fix: before
searching, have the LLM draft one hypothetical short "table/column description" that would answer
the question (HyDE), and embed _that_ to search with — description-to-description matching instead
of question-to-description matching.

### Step 3 — search two ways in parallel, not one

Run the HyDE vector against Qdrant (dense search, top ~50) **and** a simple keyword/BM25 search
over the same corpus (top ~50) at the same time. Database metadata is full of exact codes, table
names, and abbreviations that dense embeddings are known to miss (`CDSCode`, `IMEI`,
`pip_violations`) — lexical search catches exactly what semantic search is weak at. If a
`domain_tag` or `source_db` is already known (e.g. the user picked a database first), apply it as
a filter on both searches before they run, using payload fields that already exist on every entry.

### Step 4 — merge, then rerank

Combine the two candidate lists with Reciprocal Rank Fusion into one ranked shortlist
(~30–50 entries), then run a cross-encoder reranker over that shortlist to produce the final
top 5–10. This single step is the highest-leverage accuracy gain across all the source research —
bigger than the embedding model choice or the fusion method — and it's cheap to run because the
shortlist is small.

### Step 5 — expand parent/child before handing off

RIVDW's data already has a parent/child shape for free: a table-level entry plus one entry per
column. If the reranked top results include a column entry, pull in its sibling table entry (and
vice versa) before building the final context — so the LLM answering the question always sees the
table's purpose alongside the specific column that matched, not a column in isolation.

### Step 6 — break ties using trust, not just similarity

When two entries are near-equally similar, prefer the one with `human_verified: true` or
`guardian_status: approved` over an LLM-only `pending` entry. RIVDW already produces this signal
at build-time through the guardian and the human review screen — it just isn't used as a ranking
factor yet.

### The result, as one sentence

**A user's question gets rewritten into a hypothetical answer, searched both semantically and by
keyword in parallel with any known filters applied, fused into one shortlist, reranked for real
relevance, expanded to include parent-table context, and tie-broken in favor of human-approved
metadata — all measured against a golden query set so every step can be proven to help before it
ships.**

