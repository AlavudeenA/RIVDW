# RIVDW — What's Built So Far (Plain English)

This doc assumes no prior context on this project and goes light on jargon. Whenever a technical
term shows up, its plain-English explanation follows immediately in **[brackets]**, right where
the term is used — no need to jump to a separate glossary.

For a checklist of everything still pending across this whole doc, see
[`PENDING_ITEMS.md`](PENDING_ITEMS.md) in this same folder.

## The big idea

RIVDW stands for **Report Intelligence Virtual DW** (DW = Data Warehouse). The end goal: a business
user types a plain-English question like _"show me all compliance violations this quarter"_, and
the system figures out, on its own, which database tables and columns hold that answer — without
the user knowing any SQL or table names.

To do that, the system needs two halves:

1. **Build-time** — teach the system what your databases contain, _before_ anyone asks a question.
   This is the RAG **[Retrieval-Augmented Generation — search for relevant information first, then
   have an AI generate an answer using only that information, instead of answering from memory
   alone]** pattern's setup work: reading the database structure, writing plain-English
   descriptions of it, and storing those descriptions as embeddings **[lists of numbers that
   capture what a piece of text means, so similar meanings end up with similar numbers even when
   the wording is different]**.
2. **Runtime** — let a user ask a question and have the system use that stored knowledge to find
   the right tables/columns and generate an answer.

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

This starts a local web app (open the URL it prints, usually `http://localhost:8501`). Both the
build-time screens and the runtime **Query** screen live in this one app — there's no separate
server to start for runtime.

---

## Tech stack, in one line each

- **AI model**: Groq's `llama-3.3-70b-versatile`, called with `temperature=0.2` **[low
  randomness — favors consistent, predictable wording over creative variation]** and a system
  prompt constraining it to JSON-only replies, so both the metadata-description calls
  (`pipeline/nodes/enrich_node.py`) and the runtime SQL-drafting call
  (`rivdw_runtime/query_engine.py`) can parse the response as structured data rather than free text.
- **Vector database**: Qdrant **[a database built specifically to store embeddings and quickly
  find the ones most similar to a new one — a search engine that searches by meaning instead of
  exact keyword match]**. Runs locally as plain files on disk here, not as a separate server
  process.
- **Embeddings**: FastEmbed running the `bge-small-en-v1.5` model locally on your machine — turns
  text into a list of 384 numbers per piece of text, for similarity search **[asking "which stored
  items are closest in meaning to this one?" and getting back a ranked list]**. The model file
  itself is cached under `rivdw_buildtime/fastembed_cache/` and committed to git (same pattern as
  the ECWorkbench project) — clone the repo and it works fully offline, no HuggingFace download at
  startup.
- **Databases supported**: SQL Server and Oracle, connected read-only via SQLAlchemy **[a Python
  library for talking to databases]**. Only structure (table/column names and types) is ever
  read — never the actual business data inside those tables.
- **History/audit log**: a local SQLite file (`rivdw_app.db`) **[a lightweight, file-based
  database — no separate server needed]**, used here purely to keep a version history of every
  description.
- **UI**: Streamlit **[a Python framework for building web apps without writing separate
  frontend/backend code]**.

---

## `rivdw_buildtime/`

This is the working Streamlit web app you run locally. In plain terms, for each database it's
told about, it does this:

1. **Connects to your database** (SQL Server or Oracle) and reads the _structure_ only — the
   list of tables and columns, and their data types. Implemented in
   [`database/schema_crawler.py`](rivdw_buildtime/database/schema_crawler.py) as a direct
   read-only SQL query against each database's own system catalog **[the built-in tables a
   database keeps about itself — its own list of tables/columns — as opposed to the actual
   business tables]**: `sys.tables`/`sys.schemas`/`sys.columns`/`sys.types` for SQL Server,
   `ALL_TAB_COLUMNS` for Oracle. It never reads or stores actual business data/rows, only the
   shape of the data — the query never touches a real business table, only the catalog.
2. **Asks the LLM (Groq)** to write a plain-English description for every table and column: what
   it stores, what business process uses it, and what a business analyst would call it.
   Implemented in
   [`pipeline/single_db_pipeline.py`](rivdw_buildtime/pipeline/single_db_pipeline.py), which makes
   **one AI call per table** (covering all of that table's columns in a single request, using the
   prompt template in
   [`config/strings.py`](rivdw_buildtime/config/strings.py)`::LLMPrompts.BATCH_TABLE_TEMPLATE`) to
   keep this fast and cheap, rather than one call per column. The Groq call itself
   (`pipeline/nodes/enrich_node.py::_call_groq()`) is set to `temperature=0.2` **[a setting from 0
   to 1 controlling how random/creative the AI's wording is — low values like this favor
   consistent, predictable output over creative variation, which matters more for a data
   dictionary than for creative writing]** and asks for a system prompt of "respond with valid
   JSON only, no markdown" so the reply can be parsed directly; if the model still wraps its
   answer in markdown fences or adds stray text, a three-step fallback parser
   (`_parse_batch_response()`) tries a direct parse, then strips code fences, then extracts the
   first `{...}` block, before giving up.
3. **Checks the AI's work** with the "guardian" step **[a rules-based quality check — not another
   AI call — that verifies basic things like minimum length, banned filler phrases, and required
   tags]**, implemented in
   [`pipeline/nodes/guardian_node.py`](rivdw_buildtime/pipeline/nodes/guardian_node.py)`::_validate_entry()`.
   It **hard-rejects** an entry outright if the description is empty, the domain tag is missing,
   or the source database isn't tagged; it flags `needs_review` (a **soft** failure — stored, but
   marked for a human to check) if the description is under the configured minimum word count, the
   domain tag isn't in the known-domains list, or the text contains one of a configurable list of
   generic filler phrases (e.g. "this column contains data"); anything that clears all of these
   gets marked `approved`.
4. **Stores the approved descriptions** in the vector database (Qdrant), turning each description
   into an embedding so that later, a fuzzy or differently-worded question can still find the
   right table by meaning, not just exact words. Implemented in
   [`vector_store/qdrant_store.py`](rivdw_buildtime/vector_store/qdrant_store.py)`::save_entry()`
   — see Step 1 of "Our accurate-retrieval design" below for exactly what text gets turned into the
   embedding. Each entry's Qdrant point ID **[the unique key Qdrant uses to store/overwrite a
   vector]** is derived by hashing the entry's own ID (source DB + schema + table + column) with
   SHA-256, so regenerating the same table's metadata overwrites its old vector instead of creating
   a duplicate.
5. **Keeps a full history**: every version of every description — AI-generated, human-edited, or
   regenerated — is archived in the local SQLite file via
   [`database/sqlite_store.py`](rivdw_buildtime/database/sqlite_store.py) (SQLAlchemy ORM **[a
   Python library that lets code define database tables as plain classes instead of writing raw
   SQL]**, three tables: `run_history` for each pipeline run, an append-only `metadata_history` —
   every description version ever written, never deleted or overwritten — and a small
   `app_metadata` key-value table), so nothing is ever silently overwritten.

### The screens (Streamlit UI)

| Screen             | What you can do there                                                                                                                                                                                                                                                                          |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Build Metadata** | The main screen. Pick a database, click Generate, watch the AI describe each table live, edit any description inline, save, export everything to Excel, re-import an edited Excel, and browse the full change history.                                                                         |
| **Query**          | Type a plain-English question, get back the matched tables/columns, a short explanation, and one drafted SQL query (not executed). This is Phase 1 of the runtime build plan below — the simplest possible version of search, no hybrid search or reranking yet (both explained further down). |

### Human-in-the-loop workflow that's actually implemented

Human-in-the-loop **[a person reviews and can correct the AI's output before it's fully
trusted]** is built in throughout:

- Every AI-generated description can be **edited by a person** directly in the browser.
- You can **export** a database's metadata to an Excel file (with read-only ID columns
  greyed out), hand it to a business/domain expert to fill in descriptions and notes offline,
  then **import** it back — the system validates rows before applying them.
- Every save, edit, or regeneration is timestamped and archived, so you can see who changed
  what and when.

---

## Runtime build plan — what we're actually building, and in what order

**In one paragraph:** runtime gets built in four stages, each one a prerequisite for the next.
First, write down a set of test questions with known-correct answers (Phase 0), so there's a way
to check whether anything works. Then build the smallest possible version of the whole
question-to-SQL flow, as plainly as possible (Phase 1), and confirm it actually runs end to end.
Only after that exists do we start making the _search_ more accurate, one improvement at a time,
each one checked against the Phase 0 test questions to prove it actually helped (Phase 2). Only
once search is trustworthy do we let the system's SQL touch a real database at all, and even then
only through safety rails (Phase 3). Two of these four stages are done; two are still ahead.

| Phase       | Status     | What it does, in one line                               | Can't start until      |
| ----------- | ---------- | ------------------------------------------------------- | ---------------------- |
| **Phase 0** | ✅ Built   | Write the answer key: test questions + correct answers  | — (comes first)        |
| **Phase 1** | ✅ Built   | Build the smallest working version, start to finish     | Phase 0 exists         |
| **Phase 2** | ⬜ Planned | Make the search step more accurate, one layer at a time | Phase 1 works          |
| **Phase 3** | ⬜ Planned | Let the SQL actually run, with safety rails             | Phase 2 is trustworthy |

```
Phase 0                Phase 1                    Phase 2                     Phase 3
(the answer key)  →  (walking skeleton)   →   (accuracy layers)    →   (execution & safety)
     ✅ built              ✅ built                ⬜ planned                ⬜ planned
```

**Why split it into phases at all, instead of building the "ideal" version directly?** An earlier
pass at this project tried to research the ideal search pipeline first (HyDE, hybrid search,
reranking — all explained below where they first come up) and started building pieces of it
_before_ anything simpler existed to compare against — meaning there was no way to tell whether
any of those pieces were actually making things better. **The rule going forward: nothing new gets
added until the previous phase is working and has been measured against real questions.** So the
order got inverted: build the smallest possible end-to-end slice first (Phase 1), then only add
accuracy improvements where measurement (via Phase 0's test questions) shows they're actually
needed (Phase 2).

### Phase 0 — golden eval set (before any runtime code) — ✅ built

A golden eval set **[a hand-written answer key: test questions paired with the answer already
known to be correct, used to check whether the system got it right]** is the starting point. Ours
is 25 realistic business questions grounded in the real generated `compliance_db` metadata (trade
requests, approval workflows, compliance alerts, restricted securities, broker-dealers, accounts,
employees), each mapped to the table(s)/column(s) that should answer it —
[`rivdw_runtime/eval/golden_set.json`](rivdw_runtime/eval/golden_set.json). Run it with
`python eval/run_eval.py` from `rivdw_runtime/`. Current baseline (re-measured after switching to
`bge-small-en-v1.5`): **Table Recall@5: 96% (24/25), Column Recall@5: 96% (24/25)** **[Recall@5 =
out of all test questions, what fraction had the correct answer somewhere in their top-5 search
results; higher is better, 100% means it never missed]** — the one miss ("how long does it
typically take to review a trade request" → expected `turnarounddays`, got `reviewdate`) is the
same known gap from before the model switch, and is a legitimate target for Phase 2, not something
to chase now. (Before the switch, with the larger `bge-base-en-v1.5` model, table recall was
100% — a small, expected accuracy trade-off for the smaller model's speed/size benefits, worth
keeping an eye on but not a blocker.)

### Phase 1 — walking skeleton (get one real question answered, end to end) — ✅ built

A walking skeleton **[the smallest possible version of the whole system that still works start to
finish, with no extra features — it proves the pieces actually connect before any one piece gets
made fancier]** is what got built here. The flow: user types a question → embed it with the same
model already used at build-time → plain Qdrant top-5 similarity search **[no HyDE, no hybrid
search, no reranker yet — all defined below in "Our accurate-retrieval design"]** → hand those 5
descriptions to Groq and ask it to draft one SQL query → **show the generated SQL to the user,
don't execute it.** No database writes, no auto-run, just "here's what I think the answer looks
like." Lives in [`rivdw_runtime/query_engine.py`](rivdw_runtime/query_engine.py), wired into the
**Query** screen in `rivdw_buildtime` (`ui/pages/query.py`) — the "Search logic coming soon"
placeholder is gone. The SQL-drafting call reuses the same `_call_llm()` function build-time
already uses for descriptions (`pipeline/nodes/enrich_node.py`) rather than a second
Groq-calling implementation, and parses the response with the same three-step JSON fallback
pattern as build-time's batch parser (direct parse → strip markdown fences → extract the first
`{...}` block). Verified end-to-end in a real browser: a question returns matched schema
entries, a plain-English explanation, and a drafted SQL block, with no console errors.

Deliberately left out of Phase 1: HyDE, hybrid/BM25 search, reranking, parent/child expansion,
trust tie-breaking, execution, confidence scoring, trajectory reuse — all defined further down.
All of it comes later, one piece at a time, only if Phase 0's eval set shows it's needed.

One bug fixed along the way: `qdrant_store.py`'s `search()` called `QdrantClient.search()`, which
doesn't exist in the installed `qdrant-client` version — it was replaced with `.query_points()`
upstream. This had never been exercised end-to-end before (the Query screen was a placeholder, so
nothing ever called it), which is exactly the kind of gap "no automated tests anywhere" lets
through.

### Phase 2 — add accuracy layers one at a time (each re-measured against Phase 0)

In this order, per the priority list already worked out in "Our accurate-retrieval design" below —
add **one** improvement, re-run `eval/run_eval.py` from Phase 0, and only keep the change if the
Recall@K score measurably went up. If it doesn't help, it gets reverted, not kept "just in case."
Each one is explained in full further down (Steps 2–6 of "Our accurate-retrieval design") — this
list is just the short version, in build order:

1. **HyDE query rewriting** (Step 2 below) — before searching, have the AI imagine what the ideal
   answer would look like, and search using that instead of the raw question.
2. **Hybrid dense + BM25 search with RRF fusion** (Step 3 below) — add a plain keyword search
   alongside the meaning-based one, so exact codes/IDs aren't missed, then merge the two result
   lists.
3. **Cross-encoder reranking** (Step 4 below) — take the merged shortlist and re-score it more
   carefully before picking the final results.
4. **Parent/child expansion** (Step 5 below) — when a column matches, also pull in its table's
   overall description for context.
5. **Trust-based tie-breaking** (Step 6 below) — when two results are equally relevant, prefer the
   one a human has already verified.

### Phase 3 — execution and safety, only after Phase 2 is trustworthy

Once search results are measured and reliable, allow the generated SQL to actually run against a
real database — but only through a **read-only connection with a row limit and a timeout**, never
the same login credentials used for reading table structure at build-time. At this stage, also
add execution-guided self-correction (if the SQL errors or returns nothing, retry once with that
error fed back to the LLM) and confidence-based auto-answer vs. human review (see the research
list below) — there's no point running SQL that was generated from search results nobody has
verified yet.

### What's deliberately deferred, and why

- **Sampling real column values at build-time** (item 1 in the research list below) is _not_ part
  of this runtime plan. It's a genuinely useful accuracy idea, but it also means sending real
  production data — from compliance/HR/brokerage systems — to a third-party LLM API, which is a
  data-governance decision **[a policy/legal decision about how sensitive data may be used, not
  just an engineering choice]**. It needs an explicit yes/no from whoever owns that call before it
  gets built, not a default-on assumption because a research paper recommended it.
- **Trajectory reuse / approved query cache** (item 10) and **the domain glossary** (item 3) are
  real value-adds but depend on runtime already existing and being used — there's nothing to reuse
  or standardize against until Phase 1 is live and answering real questions.

---

## Research-backed ideas worth adopting

We went through all 16 PDFs in `docs/` (3 already had notes in `docs/Rearch_Papers_Summary.md`;
the other 13 were read fresh for this pass). Below is what's actually specific and cheap enough to
build — not generic "use a better prompt" advice. Where a paper or system has a name (e.g.
"DeepEye-SQL"), that's just how it's cited in the source research — you don't need to know it,
only the idea attributed to it.

### Near-term: cheap upgrades to the build-time pipeline (already running)

1. **Sample real column values, not just structure.** Nearly every paper lands on the same point:
   reading table/column _names_ only misses the real problem — mismatched formats ("USA" vs
   "United States"), junk placeholder values, ambiguous codes. RIVDW's crawler currently reads
   structure only. Pulling the top ~10 distinct values per column (plus null % and distinct count)
   and feeding that into the Groq prompt — the same "profiling" technique already noted in
   `docs/Rearch_Papers_Summary.md` from the metadata-extraction paper — would sharpen descriptions
   and catch data-quality issues the guardian can't see today.
2. **Flag join/foreign-key columns explicitly in metadata.** A join key being undocumented is
   one of the single biggest causes of text-to-SQL failure (11.6% in one paper). RIVDW's descriptions
   already have a `related_tables` field — worth making the enrichment prompt explicitly ask
   "is this a join key, and to what other table/column?" instead of leaving it implicit.
3. **Mine a synonym/glossary layer across DB types.** Same business concept, different physical
   column name in SQL Server vs Oracle. A cross-table glossary **[a lookup list mapping business
   terms to the actual column names that mean them]** fed into the enrichment prompt (Pinterest
   Engineering's approach) would standardize descriptions — this is exactly the glossary feature
   that was in the original design doc but never got built; worth resurrecting specifically for
   this reason.
4. **Use the existing guardian/approval status as a trust signal, not just a gate.** Right now
   `guardian_status` and `human_verified` only decide whether an entry gets stored at all. The
   "governance-aware ranking" idea (Pinterest) **[folding trust/freshness into how results are
   ranked, not just whether they're allowed in]** is nearly free to add once the Query screen does
   real search: rank human-verified entries above LLM-only ones when they're otherwise equally
   relevant.

### Blueprint for the runtime phase

Several of the papers hand over an almost-complete reference architecture for runtime:

5. **Two-stage retrieval: pick the database first, then the tables.** With multiple source
   databases, search should first rank _which database_ is relevant, then only drill into that
   database's tables — not one flat search across everything at once.
6. **Embed example questions per table, not just descriptions.** Query-to-query matching
   **[comparing a real question against sample questions]** tends to beat query-to-description
   matching **[comparing a real question against a formal description]**. At build-time — or once
   real usage exists — generate a handful of representative plain-English questions per table
   (double-checked by actually running them as SQL first) and embed those alongside the
   description.
7. **Hybrid schema linking with relational closure.** Combine three different ways of figuring out
   which tables/columns are relevant: asking the LLM directly, having the LLM draft SQL first and
   seeing which schema it used, and matching based on actual stored values. Then automatically
   pull in any table connected by a foreign key — relational closure **[making sure every table
   needed for a join is included, not just the ones that matched the search directly]** — so joins
   stay possible, instead of trusting one search pass alone.
8. **Execution-guided self-correction.** Actually run the generated SQL; if it errors or returns
   nothing, feed that error back to the LLM for one bounded retry before giving up. This is the
   single most commonly validated technique across the literature.
9. **Confidence-based auto-answer vs. human review.** Generate a few candidate SQL statements,
   execute all of them, and use how often their results agree with each other as a confidence
   score — high agreement auto-answers, low agreement routes to a human for review. This extends
   RIVDW's existing review culture (from the Build Metadata screen) into runtime, instead of
   always trusting whatever SQL was generated first.
10. **Reuse approved past queries.** Already scoped in `docs/Rearch_Papers_Summary.md` as the
    "Trajectory Builder" idea: once a human approves a question + its SQL + the reasoning behind
    it, store that pairing and use it to help answer similar future questions. A separate source
    (Pinterest's engineering blog) independently reaches the same conclusion from mining historical
    query logs, which reinforces that it's worth building.
11. **Deterministic checker chain before trusting generated SQL.** A cascade of cheap, non-LLM
    checks (valid syntax, sane JOINs, no accidental `SELECT *`, empty-result detection) — the same
    kind of rules-based gate as the build-time guardian, but applied to generated SQL instead of
    generated text.

### Looked at, not worth adopting yet

- **Quantized/optimized ANN search and pattern-constrained vector search** — ANN **[Approximate
  Nearest Neighbor search — the core technique vector databases use to find similar items
  quickly]** is what these two papers optimize further, for scales (millions to billions of stored
  vectors, huge text corpora) far beyond RIVDW's metadata store, which is a handful of databases'
  worth of table/column descriptions. Qdrant's default settings are already sufficient; revisit
  only if the store grows by orders of magnitude.
- **Fine-tuning an LLM on RIVDW's own schemas** — fine-tuning **[retraining part of an AI model on
  your own data, as opposed to just writing a good prompt for an off-the-shelf model]** is
  explicitly discouraged by several papers for teams without dedicated ML infrastructure or
  labeled training data. RIVDW's current approach (Groq + carefully written prompts, no
  fine-tuning) is the right call per this research.

---

## Our accurate-retrieval design (the target end-state, built in the phases above)

`docs/Embeddings_BestPractise.md` lays out 14 separate retrieval **[the "search" half of RAG —
finding the relevant information before an AI generates an answer from it]**/embedding techniques
and judges each one against RIVDW specifically. This section combines the ones that survived that
judgment into **a single retrieval pipeline** — described here as one flow rather than a list of
independent choices. It is _not_ what gets built on day one: the "Runtime build plan" section
above is the actual order of work, starting from Phase 1's plain top-5 search. Steps 2–6 below
correspond to Phase 2's accuracy layers, added and measured one at a time rather than all at once.

### Step 0 — measure before building anything

Before writing any retrieval code, put together a small golden set (see Phase 0 above): a few
dozen realistic business questions, each mapped to the table(s)/column(s) that should answer it.
Every step below gets measured against this set (via Recall@K — see Phase 0 above for what that
means) before and after it's added — otherwise "accurate" is just a guess. This is cheap and comes
first on purpose.

### Step 1 — change what gets embedded at build-time (storage side) — ✅ built

`save_entry()` used to embed only `description + human_notes` — the table name, column name, and
database it belonged to were stored alongside the vector but never actually turned into part of
the vector itself. Fixed in
[qdrant_store.py:61-70](rivdw_buildtime/vector_store/qdrant_store.py#L61-L70): it now embeds a
short prefix giving that context along with the description:

```
"{source_db} > {table_name}[.{column_name}]: {description} {human_notes}"
```

e.g. `"compliance_db > TradeRequest.status: The status column captures... "`. This was the one
build-time change in the whole plan — everything else below is retrieval-side and touches the
runtime Query screen, not the working Build Metadata pipeline. One caveat: this only affects
entries saved from the fix onward — anything embedded before it keeps the old, un-prefixed vector
until regenerated (Regenerate button per table, or Reset Vector Store + re-run for everything).

### Step 2 — when a question comes in, don't embed it raw

A business question ("show me compliance violations this quarter") and a column description
("stores the review status for flagged transactions") are written in completely different styles
— this mismatch, not embedding-model quality, is RIVDW's real accuracy risk. Fix: before
searching, use HyDE **[Hypothetical Document Embeddings — have the LLM imagine what the ideal
answer would look like, then search using that imagined answer's embedding instead of the raw
question]**. This turns the search into description-to-description matching, which tends to work
better than question-to-description matching.

### Step 3 — search two ways in parallel, not one

Run the HyDE vector against Qdrant (dense search **[search by meaning, via embeddings]**, top ~50)
**and** a simple keyword/BM25 **[a classic keyword-matching search algorithm — finds items sharing
the exact same words]** search over the same data (top ~50) at the same time, then combine both
lists — hybrid search **[running a meaning-based search and a keyword search together to get the
strengths of both]**. Database metadata is full of exact codes, table names, and abbreviations
that dense/embedding-based search is known to miss (`CDSCode`, `IMEI`, `pip_violations`) — keyword
search catches exactly what meaning-based search is weak at. If a `domain_tag` **[a label like
`compliance` or `brokerage` attached to each database and each piece of metadata]** or `source_db`
is already known (e.g. the user picked a database first), apply it as a filter on both searches
before they run, using payload fields that already exist on every stored entry.

### Step 4 — merge, then rerank

Combine the two candidate lists using RRF **[Reciprocal Rank Fusion — a simple, well-tested way to
merge two separately-ranked lists into one combined ranking, without needing to compare their raw
scores directly]** into one ranked shortlist (~30–50 entries), then run a cross-encoder reranker
**[after the broad search returns a shortlist of "good enough" candidates, this looks at each
candidate together with the original question, one pair at a time, for a more careful relevance
score — too slow to run over an entire database, which is why it only runs on the shortlist]**
over that shortlist to produce the final top 5–10 results. This single step is the
highest-leverage accuracy gain across all the source research — bigger than the choice of
embedding model or fusion method — and it's cheap to run because the shortlist going into it is
small.

### Step 5 — expand parent/child before handing off

RIVDW's data already has a "parent/child" shape for free: a table-level entry, plus one entry per
column that belongs to it. If the reranked top results include a column entry, pull in its sibling
table entry too (and vice versa) before building the final context — so the LLM answering the
question always sees the table's overall purpose alongside the specific column that matched,
rather than a column description in isolation.

### Step 6 — break ties using trust, not just similarity

When two entries are near-equally similar to the question, prefer the one with
`human_verified: true` or `guardian_status: approved` over an LLM-only `pending` entry. RIVDW
already produces this trust signal at build-time through the guardian check and the human review
screen — it just isn't used as a ranking factor yet.

### The result, as one sentence

**A user's question gets rewritten into a hypothetical answer, searched both by meaning and by
keyword in parallel with any known filters applied, merged into one shortlist, reranked for real
relevance, expanded to include parent-table context, and tie-broken in favor of human-approved
metadata — all measured against a golden query set so every step can be proven to help before it
ships.**
