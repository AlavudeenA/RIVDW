# RIVDW, Explained From Zero

This document assumes you just walked in with a computer science degree and nothing else — no
prior context on this project, no assumption you've used any of the tools mentioned. It covers two
things only: what's actually built and working right now, explained plainly, and what's planned
next. Every technical term is explained in **[square brackets]** the very first time it shows up,
so you never have to stop and look something up elsewhere.

Read it top to bottom once. After that, use it as a reference.

---

## 1. What problem is this solving?

Imagine a company has several databases — say a compliance database, maybe an HR database, maybe
a sales database. Each one has dozens of tables, each table has dozens of columns, and the names
are usually cryptic to a normal business person: `TRDREQ`, `CDSCode`, `apprvl_wf`. To get an answer
out of these databases today, you need someone who knows SQL **[Structured Query Language — the
language used to ask a database questions, e.g. "SELECT * FROM Employees WHERE ..."]** *and* who
happens to know what all those cryptic table/column names actually mean.

RIVDW stands for **Report Intelligence Virtual DW** (DW = **Data Warehouse**, a database built for
answering business questions rather than running an app). The goal: a business user types a plain
English question like _"show me all compliance violations this quarter"_, and the system figures
out, entirely on its own, which tables and columns hold that answer, and writes the SQL for
them — without that user ever needing to know a table name or a line of SQL.

This is a **RAG** system. RAG stands for **Retrieval-Augmented Generation** — instead of asking an
AI model to answer a question purely from memory, first _retrieve_ the relevant facts from a
trusted source, and only then ask the AI to _generate_ an answer using strictly those retrieved
facts. Here, the "facts" are descriptions of what each database table and column actually means.

To make RAG work here, the project is split into two halves that run at completely different
times:

1. **Build-time** — happens before any user ever asks a question. The system reads a database's
   structure, has an AI write plain-English descriptions of every table and column, has those
   descriptions checked, and stores them so they can be searched later. A person can also review
   and directly edit any of those AI-written descriptions before they're relied on — covered in
   full in section 3's "human-in-the-loop" part. Think of this as writing a glossary/dictionary of
   the company's data, once, in advance, with a human allowed to correct entries in that
   glossary/dictionary at any time.
2. **Runtime** — happens every time a user actually asks a question. It searches that stored
   dictionary for the pieces relevant to the question, then asks an AI to draft a SQL query using
   only what it found.

**Where things stand right now: build-time is completely built and working. Runtime has its first
working version built too (a deliberately simple one), with a longer list of accuracy and safety
improvements planned but not yet built.** The rest of this document walks through both halves in
detail, then the plan for what comes next.

---

## 2. The tools this project is built from

Before walking through the code, here's what each piece of technology is and why it was chosen.
You don't need deep expertise in any of these — just enough to follow the rest of the document.

- **Groq** — the AI service this project calls to run its language-model queries. An **LLM**
  **[Large Language Model — an AI model trained on huge amounts of text that can read, write, and
  reason about natural language]** call here always uses `temperature=0.2` — a setting from 0 to 1
  controlling how random/creative the wording is; a low value like this favors consistent,
  predictable output, which matters more for a data dictionary than it would for creative writing.

- **Qdrant** — a vector database **[a database purpose-built to store embeddings (explained next)
  and quickly find the ones most similar to a new one — effectively a search engine that searches
  by meaning instead of by exact keyword match]**. It runs locally here, as plain files on disk —
  there's no separate server process to start or manage.

- **Embeddings** — a way of converting a piece of text into a list of numbers (in this project,
  384 numbers) such that texts with _similar meaning_ end up with _similar numbers_, even if the
  actual words used are completely different. This is what makes "meaning-based search" possible:
  you convert everything you want to search over into these number-lists once, and then a new
  question, also converted into a number-list, can be compared against all of them mathematically
  to find the closest matches. This project generates embeddings locally on your own machine (no
  data leaves your computer for this step) using a small tool called **FastEmbed**, running a
  specific pre-trained model called `bge-small-en-v1.5`. The model file itself (about 65MB) is
  committed directly into this project's git repository **[the version-control history of the
  project's code]**, rather than being downloaded fresh from the internet every time — meaning if
  you clone this project onto a brand-new machine, it works completely offline, with no wait for a
  first-time model download.

- **SQLAlchemy** — a Python library **[library = a pre-written piece of code you can import and
  reuse instead of writing everything from scratch]** for talking to databases from Python code,
  used here to connect (read-only) to SQL Server and Oracle databases.

- **SQLite** — a lightweight, file-based database that needs no separate server to run, used here
  purely to keep a complete change history of every description ever written or edited.

- **Streamlit** — a Python framework for building interactive web apps without having to write
  separate front-end (browser-side) and back-end (server-side) code by hand. This is what the
  actual app you open in your browser is built with.

None of the databases RIVDW connects to ever have their actual business data read — only the
_structure_ (the list of tables, the list of columns, and their data types) is ever read from those
systems.

---

## 3. Build-time, in detail: teaching the system what the data means

This is the part that's fully built and working today. It lives in the `rivdw_buildtime/` folder,
which is a self-contained Streamlit web app you run locally on your own machine
(`python -m streamlit run ui/app.py`, opening a page usually at `http://localhost:8501`). For each
database you tell it about (configured in `config/databases.json`), it walks through five steps:

### Step 1 — Read the database's structure only

Implemented in `database/schema_crawler.py`. This connects to a real database (SQL Server or
Oracle) and reads its list of tables and columns and their data types by querying that database's
own **system catalog** — the built-in tables a database keeps _about itself_ (its own list of
tables/columns), as opposed to the actual business tables holding real data. For SQL Server this
means querying `sys.tables`, `sys.schemas`, `sys.columns`, and `sys.types`; for Oracle, it means
querying `ALL_TAB_COLUMNS`. The query never touches a real business table — only the database's own
internal "list of what I contain" tables. This is why no actual business data ever gets exposed to
this system or to the AI model that's called next.

### Step 2 — Ask the AI to describe every table and column in plain English

Implemented in `pipeline/single_db_pipeline.py`. For every table, the system makes **one AI call
covering all of that table's columns at once** (rather than one separate call per column — this
keeps things fast and cheap), using a prompt template stored in `config/strings.py` (a single file
that holds _all_ the UI text and AI prompt wording in the whole project — handy, because it means
anyone wanting to change what gets shown or asked doesn't need to go hunting through code). The
actual call to Groq happens in `pipeline/nodes/enrich_node.py`, at that low `temperature=0.2`
setting described above, and demands a JSON-only reply.

Real AI models don't always perfectly follow "respond with JSON only" — sometimes they wrap the
answer in markdown code fences **[blocks normally used to format code nicely when rendering
Markdown text, recognizable by the triple-backtick marks around them]**, or add a stray sentence
before or after the JSON. To handle this without crashing, there's a three-step fallback parser:
try to parse the reply as JSON directly; if that fails, strip out any markdown code fences and try
again; if _that_ fails, search the text for the first `{...}` block and try to parse just that.
Only if all three attempts fail does it actually give up.

### Step 3 — Check the AI's work with a "guardian"

Implemented in `pipeline/nodes/guardian_node.py`. The **guardian** is a rules-based quality check —
importantly, _not_ another AI call, just plain code checking plain rules — that looks at every
description the AI wrote and classifies it into one of three outcomes:

- **Hard-rejected** outright if the description is empty, if it's missing a required domain tag
  **[a label like `compliance` or `brokerage` identifying which business area a piece of data
  belongs to]**, or if it's missing a tag identifying which source database it came from.
- **Flagged `needs_review`** (a _soft_ failure — it still gets stored, but marked so a human knows
  to double check it) if the description is shorter than a configured minimum word count, if the
  domain tag isn't one of the recognized domains, or if the text contains one of a configurable
  list of generic filler phrases that don't actually say anything useful (e.g. "this column
  contains data" — technically true of every column, and useless to a reader).
- **Marked `approved`** if it clears every one of the above checks.

### Step 4 — Store the approved descriptions as embeddings

Implemented in `vector_store/qdrant_store.py`, function `save_entry()`. Every approved description
gets turned into one of those number-lists (embeddings) described earlier, and stored in Qdrant.
Critically, the text that actually gets converted into numbers isn't just the raw description — it's
a short context-prefixed string:

```
"{source_db} > {table_name}[.{column_name}]: {description} {human_notes}"
```

for example: `"compliance_db > TradeRequest.status: The status column captures..."`. This matters
because it bakes the table name, column name, and source database _into the meaning_ the embedding
captures — not just storing them as a side-note. That way, when someone later searches, the fact
that this text is about "TradeRequest" and "status" specifically is part of what similarity search
is comparing against, not just extra data sitting next to the vector unused.

Each stored entry also gets a unique ID, computed by hashing **[running the entry's own identity —
source database + schema + table + column — through a one-way mathematical function that always
produces the same fixed-length output for the same input, called SHA-256 here]** its own identity.
This means regenerating the same table's metadata later overwrites its existing entry instead of
creating a confusing duplicate.

### Step 5 — Keep a full history of every change, forever

Implemented in `database/sqlite_store.py`, using SQLAlchemy's **ORM** **[Object-Relational
Mapping — a technique that lets code define database tables as ordinary programming-language
classes, instead of writing raw SQL by hand for every operation]**. Three tables are kept in a
local SQLite file (`rivdw_app.db`):

- `run_history` — a record of every time the pipeline ran.
- `metadata_history` — an **append-only** log (nothing is ever deleted or overwritten here) of
  every single version of every description that has ever existed — AI-generated, human-edited, or
  regenerated.
- `app_metadata` — a small key-value table for miscellaneous app state.

The point of all this is that nothing is ever silently lost or overwritten — you can always trace
back who changed a description, when, and what it used to say.

### The actual screens you interact with

The Streamlit app has two navigation entries:

| Screen | What you can do there |
|---|---|
| **Build Metadata** | The main screen. Pick a database, click Generate, watch the AI describe each table live, edit any description by hand right in the browser, save your edits, export the whole thing to an Excel file, re-import an edited Excel file back in, and browse the complete change history. |
| **Query** | Type a plain-English question, get back the tables/columns the system thinks are relevant, a short explanation, and one drafted SQL query — this is the runtime half, covered in the next section. |

### Human-in-the-loop: people stay in control

**Human-in-the-loop** means a person reviews and can correct the AI's output before it's fully
trusted, rather than the AI's word being taken as final automatically. This is built in throughout
build-time in a few concrete ways:

- Every AI-written description can be edited by a person directly in the browser.
- A database's whole metadata set can be exported to an Excel spreadsheet (with the ID columns —
  the ones that identify _which_ table/column a row is about — greyed out and non-editable, so
  nobody accidentally breaks the linkage), handed to a business or domain expert to fill in
  descriptions and notes offline, at their own pace, in a tool they're already comfortable with,
  and then imported back in. The system validates every row before applying the import, so a
  malformed spreadsheet can't corrupt the stored data.
- Every save, edit, or regeneration gets timestamped and archived in that append-only history
  table, so there's always an audit trail of who changed what and when.

Two screens sometimes mentioned in project notes — "Review Metadata" and "Manage Glossary" — don't
exist in the app today, and there's no glossary storage or UI either; the `glossary/` and
`glossary_data/` folders in the code sit empty and unused (see section 6 for the plan to eventually
put them to use).

---

## 4. Runtime, in detail: answering a real question

This is the newer half of the project. It's built in four deliberate stages, and as of today, **two
of the four are done**. The operating principle behind the whole plan: nothing new gets added until
the previous stage is built and has been measured against real test questions — so search only gets
more advanced where actual testing shows it's needed, not on a guess.

| Stage | Status | What it does, in one line | Can't start until |
|---|---|---|---|
| **Phase 0** | ✅ Built | Write the answer key: test questions with known-correct answers | — (comes first) |
| **Phase 1** | ✅ Built | Build the smallest possible working version, start to finish | Phase 0 exists |
| **Phase 2** | ⬜ Planned | Make the search step more accurate, one improvement at a time | Phase 1 works |
| **Phase 3** | ⬜ Planned | Let the generated SQL actually run, with safety rails | Phase 2 is trustworthy |

```
Phase 0                Phase 1                    Phase 2                     Phase 3
(the answer key)  →  (walking skeleton)   →   (accuracy layers)    →   (execution & safety)
     ✅ built              ✅ built                ⬜ planned                ⬜ planned
```

### Phase 0 — the answer key (✅ built)

Before writing any search code, the project needed a way to check whether search results were
actually _correct_, not just plausible-looking. So the first thing built was a **golden eval set**
**[a hand-written answer key: a list of test questions paired with the answer that's already known
to be correct, used specifically to check whether a system got it right]** — 25 realistic business
questions grounded in the real generated `compliance_db` test data (covering trade requests,
approval workflows, compliance alerts, restricted securities, broker-dealers, accounts, and
employees), each one mapped by hand to the exact table(s) and column(s) that should answer it. This
lives in `rivdw_runtime/eval/golden_set.json`, and is run with `python eval/run_eval.py` from
inside `rivdw_runtime/`.

The score it reports is called **Recall@5**: out of all 25 test questions, what fraction had the
_correct_ answer show up somewhere in the search system's top-5 results. Higher is better; 100%
means the correct answer was in the top 5 every single time.

The current baseline: **Table Recall@5: 96% (24 out of 25), Column Recall@5: 96% (24 out of 25).**
The one question it still misses is "how long does it typically take to review a trade request" —
the system expects the `turnarounddays` column but the search currently surfaces `reviewdate`
instead. This is a known, accepted gap, explicitly earmarked as something Phase 2's accuracy
improvements (below) should fix, not something being chased right now with a special-case hack.

### Phase 1 — the walking skeleton (✅ built)

A **walking skeleton** is the smallest possible version of an entire system that still works start
to finish, deliberately without any extra features — the goal is purely to prove that all the
pieces actually connect correctly, before any single piece gets made fancier. This is what got
built for Phase 1, and it's what powers the **Query** screen in the app today.

The full flow, step by step:

1. A user types a plain-English question into the Query screen.
2. That question gets converted into an embedding, using the exact same embedding model used at
   build-time (`bge-small-en-v1.5`) — this consistency matters, since embeddings from different
   models aren't comparable to each other.
3. That embedding is used to do a plain Qdrant top-5 similarity search **[find the 5 stored entries
   whose embeddings are mathematically closest to this one]** — deliberately the simplest possible
   search: no advanced techniques yet (all explained in section 5 below).
4. Those 5 matched descriptions get handed to Groq, along with a prompt asking it to draft **one**
   SQL query that would answer the question, using only those 5 descriptions as its source of
   truth.
5. The drafted SQL is shown to the user on screen. **It is never executed against a real
   database.** No writes happen anywhere; nothing runs. It's purely "here's what I think the answer
   looks like" — a suggestion for a human to review.

This lives in `rivdw_runtime/query_engine.py`, and is wired into the Query screen
(`rivdw_buildtime/ui/pages/query.py`). Two implementation details worth noting: the SQL-drafting
call reuses the exact same `_call_llm()` function that build-time already uses for writing
descriptions (`pipeline/nodes/enrich_node.py`), rather than duplicating a second Groq-calling
implementation, and it parses Groq's reply using that same three-step JSON fallback pattern
described in section 3 (direct parse → strip markdown fences → extract the first `{...}` block).
This flow has been verified end-to-end in a real browser: typing a question returns matched schema
entries, a plain-English explanation, and a drafted SQL block.

**Deliberately left out of Phase 1, on purpose:** HyDE, hybrid/keyword search, reranking,
parent/child expansion, trust-based tie-breaking, actual SQL execution, confidence scoring, and
reusing previously-approved answers. All of these are explained in the sections below — none of
them are built yet, and per the "measure before adding" rule above, they only get added if the
golden eval set shows they're actually needed.

### Phase 2 — accuracy layers (⬜ planned, not yet built)

This is where the more advanced retrieval **[the "search" half of RAG — finding the relevant
information before an AI generates an answer from it]** techniques come in — a full separate
research pass went into designing this (see section 5 below for full detail on _why_ each one
helps). The plan is to add them **one at a time**, in this order, re-running the Phase 0 golden
eval after each one, and keeping the change _only if_ the Recall@K score measurably goes up. If a
change doesn't help, it gets reverted — not kept "just in case it helps something we didn't
measure."

The order planned:

1. **Embed representative example questions per table** — generate a handful of plain-English
   example questions per table and embed them alongside the description, so a real question can be
   compared against _other questions_ instead of only a formal description. Since SQL execution
   doesn't exist in this project yet (that's Phase 3, further down), each generated example
   question is validated by a **person** instead — in the same Build Metadata screen already used
   to review descriptions — rather than by running it against a database.
2. **HyDE query rewriting** — before searching, have the AI imagine what an ideal answer would
   look like, and search using _that_ instead of the raw question.
3. **Hybrid dense + keyword + AI-native schema-linking search, merged with RRF fusion** — run a
   classic keyword search alongside the existing meaning-based search, _and_ a third search: ask the
   AI directly, before it sees any search results at all, what it thinks the answer needs (either by
   naming tables/columns outright, or by drafting a rough hypothetical SQL query and reading off
   whatever schema it references) — then merge all three ranked result lists together. This third
   channel is the only one of the three that can catch a case where the question's wording doesn't
   match the stored text either by meaning _or_ by exact words — for example, "how long does review
   take" vs. the stored `turnarounddays` column, which is the one known miss in the current golden
   eval (section 4 above).
4. **Cross-encoder reranking** — take the merged shortlist of candidates and re-score it more
   carefully before picking the true final results.
5. **Parent/child + relational-closure expansion** — when a column-level match is found, also pull
   in its table's overall description for context, and also automatically pull in any _other_ table
   connected to it by a foreign key **[a column in one table that points to a matching row in
   another table — the mechanism that makes multi-table SQL queries possible]**, using join keys
   explicitly flagged in the metadata.
6. **Trust-based tie-breaking** — when two results are equally relevant, prefer the one a human has
   already verified over one that's only AI-generated and unreviewed.

### Phase 3 — execution and safety (⬜ planned, not yet built)

Only starts once Phase 2 has been built and measured as trustworthy — there would be no point
letting AI-generated SQL actually run against a real database if nobody has verified that the
_search_ feeding it is reliable yet. When this stage is eventually built, it will include:

- **Read-only SQL execution** through a completely separate database connection from the one used
  for reading schema structure at build-time, with a row limit and a timeout, so nothing runs away
  or modifies data.
- **Execution-guided self-correction** — if the generated SQL produces an error or comes back
  empty, feed that error back to the AI for exactly one bounded retry attempt before giving up
  entirely (rather than retrying forever).
- **Confidence-based auto-answer vs. human review** — generate a few different candidate SQL
  statements for the same question, run all of them, and use how much their results _agree_ with
  each other as a confidence score: high agreement can auto-answer, low agreement routes the
  question to a human for review instead.
- **A deterministic checker chain before trusting generated SQL** — a cascade of cheap, non-AI
  checks (is the syntax valid, do the JOINs make sense, is it accidentally doing a `SELECT *` that
  returns everything, does it come back with zero rows unexpectedly) — the same "rules-based gate"
  philosophy as build-time's guardian step, just applied to generated SQL instead of generated text
  descriptions.

---

## 5. The full target design for search accuracy (why each Phase 2 step exists)

A separate, detailed research document (`docs/Embeddings_BestPractise.md`) surveyed 14 different
retrieval/embedding techniques and judged each one specifically against this project's needs. The
ones that survived that judgment combine into a single target pipeline, described here end to end.
**This is not what's built today** — Phase 1 (section 4 above) is the actual current state. This
section explains the reasoning behind _where the project is headed_.

- **Step 0 — measure first.** Covered above as Phase 0: build a golden eval set before writing any
  search code, so every later change can be proven to help (or not) with a number, not a guess.

- **Step 1 — change what gets embedded at build-time (✅ already done).** Covered in section 3,
  step 4: instead of embedding just the raw description, embed a short prefix giving the source
  database, table name, and column name as well, so that identity information is part of the
  _meaning_ being searched, not just side-note data sitting unused next to the vector. This is the
  one build-time change already made in this design — Step 1b just below is a second, planned
  build-time change; every step from Step 2 onward is about runtime search, not the already-working
  Build Metadata pipeline. One caveat: this only affects entries saved _after_ the fix — anything
  embedded before it keeps the old, un-prefixed vector until it's regenerated.

- **Step 1b — also embed example questions per table (⬜ planned).** Beyond embedding descriptions
  (Step 1 above), generate a small handful (3-5) of representative plain-English example questions
  per table — the kind a real business user might actually type — and embed those alongside the
  description, as their own distinct entry type, separate from the regular schema descriptions. The
  reasoning: a business question and a formal column description are written in noticeably
  different styles, and comparing a real question against _other questions_ (**query-to-query
  matching**) tends to beat comparing it against a formal description (**query-to-description
  matching**). As noted above, validation here is done by a human reviewer, not by executing the
  generated questions as SQL, since SQL execution doesn't exist yet in this project.

- **Step 2 — don't embed the raw question (⬜ planned).** A business question ("show me compliance
  violations this quarter") and a column description ("stores the review status for flagged
  transactions") are written in completely different styles from each other — and this style
  mismatch, not the quality of the embedding model, is this project's real accuracy risk. The fix
  is **HyDE** — **Hypothetical Document Embeddings** — where, before searching, the AI is asked to
  imagine what the ideal answer to the question would look like, written in a style closer to the
  stored descriptions, and _that_ imagined answer gets embedded and searched instead of the raw
  question. This turns the comparison into description-to-description matching, which tends to
  work noticeably better than question-to-description matching.

- **Step 3 — search three ways in parallel, not one (⬜ planned).** Run the HyDE-based vector search
  (**dense search** — searching by meaning, via embeddings) and, at the same time, a classic keyword
  search called **BM25** **[a well-established keyword-matching search algorithm that finds items
  sharing the exact same words, rather than similar meaning]** over the same stored data — plus a
  third search, **AI-native schema linking**: ask the AI directly, given only the question and a
  lightweight list of table/column names (before it's shown any search results at all), what it
  would look for to answer this — either by naming tables/columns outright, or by drafting a rough,
  hypothetical SQL query and reading off whatever schema names it references — and treat whatever it
  names as a third ranked candidate list. This combination of all three is called **hybrid search**.
  The reason for the keyword channel: database metadata is full of exact codes, table names, and
  abbreviations (things like `CDSCode`, `IMEI`, `pip_violations`) that meaning-based embedding search
  is known to sometimes miss, precisely because it's optimized for _meaning_ rather than _exact
  matching_ — keyword search catches exactly what embedding search tends to be weak at. The reason
  for the AI-native channel: it's the only one of the three that doesn't depend on the stored text
  matching the question at all, by meaning or by exact words — it catches cases where the question's
  business phrasing (e.g. "how long does review take") shares neither meaning nor spelling with the
  stored column name (`turnarounddays`), but a general-purpose AI's own knowledge of business
  language can still bridge that gap. This directly targets the one known miss in the current golden
  eval (section 4 above). If a `domain_tag` or source database is already known ahead of time (say,
  the user already picked which database they're asking about), that gets applied as a filter to the
  dense and keyword searches before they even run.

- **Step 4 — merge, then rerank (⬜ planned).** The three separately-ranked candidate lists (dense,
  keyword, and AI-native, maybe 50 results each) get combined into one list using **RRF** —
  **Reciprocal Rank Fusion**, a simple, well-tested mathematical way to merge multiple
  differently-scored ranked lists into a single fair ranking, without needing to directly compare
  their raw, differently-scaled scores. This produces a shortlist of maybe 30-50 candidates. That
  shortlist then gets passed through a **cross-encoder reranker** **[a slower but more careful
  comparison model: after a broad search returns a shortlist of "good enough" candidates, this
  looks at each individual candidate together with the original question, one pair at a time, and
  produces a more careful relevance score — too slow to run over an entire database up front, which
  is exactly why it only runs on the already-narrowed-down shortlist]**, producing the true final
  top 5-10 results. Across all the research surveyed, this single step is flagged as the single
  highest-leverage accuracy improvement available — bigger than the choice of embedding model or how
  the two lists get fused together — and it stays cheap specifically because it only ever runs on a
  small shortlist, never the whole database.

- **Step 5 — expand parent/child, and FK-connected tables, before handing off (⬜ planned).** The
  stored data already naturally has a "parent/child" shape: one entry describing a table overall,
  plus separate entries for each of its columns. If the final reranked results include a
  column-level entry, its sibling table-level entry gets pulled in too (and vice versa), so the AI
  drafting the final SQL always sees a column's specific meaning _together with_ its table's
  overall purpose, rather than a column description sitting in isolation with no context about what
  table it even belongs to. The same idea gets extended to **relational closure** **[making sure
  every table needed to complete a join is included in the final context, not just the ones that
  matched the search directly]**: if a table connected by a foreign key isn't already in the result
  set, pull it in too, so a question spanning two related tables still ends up with both in
  context. This depends on join keys being explicitly flagged in the metadata first — a cheap,
  build-time-only addition to the AI's description-writing prompt (asking "is this a join key, and
  to what other table/column does it connect?"), which can be made independently of the rest of
  this search pipeline since it only touches build-time, not search.

- **Step 6 — break ties using trust, not just similarity (⬜ planned).** When two candidate entries
  come back roughly equally relevant, prefer the one already marked `human_verified: true` or
  `guardian_status: approved` (from the build-time human review process described in section 3)
  over one that's still only `pending` and AI-only. Build-time already produces this trust signal
  today — it just isn't being used to influence ranking yet.

**As one sentence, the full target design:** a user's question gets rewritten into a hypothetical
answer, searched by meaning, by keyword, and by asking the AI directly, all in parallel with any
known filters applied, merged into one shortlist, reranked for real relevance, expanded to include
parent-table and FK-connected context, and tie-broken in favor of human-approved metadata — all
continuously measured against the golden query set so every single step can be proven to actually
help before it ships.

---

## 6. Other ideas that came out of the research, not yet scheduled into any phase

Beyond the Phase 2/3 plan above, the same research pass surfaced several other ideas judged worth
adopting eventually, but not yet slotted into a specific build phase. None of these are built.

**Cheap upgrades to the build-time pipeline (the part that's already running):**

- **Sample real column _values_, not just structure — currently blocked on a policy decision, not
  an engineering one.** Nearly every research paper surveyed lands on the same point: reading only
  table/column _names_ misses real problems — mismatched formats (e.g. "USA" vs "United States" for
  the same thing), junk placeholder values, ambiguous codes that only make sense once you see a
  real example. Pulling the top ~10 distinct values per column (plus what fraction are blank/null
  and how many distinct values exist) and feeding that into the AI's description-writing prompt
  would sharpen descriptions meaningfully. **This is explicitly not being built yet**, because doing
  it means sending real production data — from compliance, HR, or brokerage systems — to a
  third-party AI API, which is a **data-governance decision** **[a policy/legal decision about how
  sensitive data may be used, distinct from a purely technical/engineering choice]**, not just an
  engineering one. It needs an explicit yes/no from whoever owns that decision before any code gets
  written for it — it is not being built on a default-on assumption just because research recommends
  it. A closely related idea, **value-based schema matching** — matching a question against actual
  stored column values, rather than just structure — is blocked on this exact same decision, for the
  exact same reason.

- **Mine a synonym/glossary layer across different database types.** The same business concept
  often has a different physical column name depending on which database system stores it (SQL
  Server vs Oracle, for instance). A cross-table **glossary** **[a lookup list mapping business
  terms to the actual column names that represent them across different systems]**, fed into the
  description-writing prompt, would make descriptions consistent regardless of which underlying
  database they came from. This would reuse the `glossary/` and `glossary_data/` folders already
  sitting empty in the code.

**A near-complete reference architecture for later runtime work:**

- **Two-stage retrieval: pick the database first, then the tables.** With multiple source
  databases connected, search should first figure out _which database_ is relevant to a question,
  and only then drill into that specific database's tables — rather than running one flat search
  across every database at once.

**Ideas looked at and explicitly rejected — not on the backlog at all, listed here only for
completeness:**

- **Quantized/optimized approximate nearest-neighbor search and pattern-constrained vector
  search** — these techniques solve search-speed problems at a scale (millions to billions of
  stored vectors) far beyond this project's actual size, which is a handful of databases' worth of
  table/column descriptions. Qdrant's default settings are already more than sufficient. Worth
  revisiting only if the vector store somehow grows by orders of magnitude.
- **Fine-tuning an AI model on this project's own schemas** — **fine-tuning** means retraining part
  of an AI model on your own specific data, as opposed to just writing a good prompt for an
  off-the-shelf model. Several papers explicitly discourage this for teams without dedicated
  machine-learning infrastructure or labeled training data, which describes this project. The
  current approach (Groq's existing model + carefully written prompts, no fine-tuning) is
  considered the right call.

---

## 7. A concrete new feature planned: reusing previously-approved answers

Beyond the Phase 2/3 accuracy and safety plan, there's one more concrete feature design that's been
worked out in detail but not yet built. It sits _alongside_ Phase 2/3 rather than depending on them
— it only actually needs Phase 1 (already built) to exist, so it could reasonably be built in
parallel with, or even before, the Phase 2 accuracy work.

**The problem it solves:** right now, every question the Query screen answers gets derived from
scratch, every single time, with no memory of past interactions. If ten different people ask the
same recurring business question ("show me pending trade requests") on ten different days, the
system re-derives an answer from zero all ten times — with no guarantee it even lands on the exact
same (correct) SQL twice in a row.

**The planned fix, as a loop:**

1. **Add "Acknowledge" and "Reject" buttons to the Query screen**, shown right after a drafted SQL
   query renders. "Acknowledge" (something like a ✅ "This is correct" button) is the main trigger
   for everything below; "Reject" (❌ "Not quite right") is nearly free to add alongside it and
   gives a useful, symmetric signal in the opposite direction.
2. **When a result gets acknowledged, store it as an `approved_trajectory` entry** — its own
   distinct entry type in Qdrant, separate from the regular `schema_metadata` entries. It would
   record: the original question text, exactly which tables/columns were retrieved and actually
   used, the drafted SQL, who approved it, and when.
3. From there, three increasingly aggressive tiers of reuse, meant to be built roughly in this
   order:
   - **Tier A — few-shot context (cheapest, built first):** at query time, search the stored
     approved trajectories alongside the normal schema search. If a similar past approved question
     comes back, include it as a worked example inside the SQL-drafting prompt ("a similar question
     was answered correctly before: ...") — nudging the AI toward previously-validated patterns,
     without skipping any actual generation step.
   - **Tier B — near-duplicate short-circuit:** if a brand-new question is similar enough (above a
     high similarity threshold) to a stored approved trajectory, skip AI generation entirely and
     reuse (or lightly adapt) the previously-approved SQL directly — faster, and guaranteed to
     match something a human already approved, instead of a fresh, unverified draft. This needs
     careful threshold-tuning, since something like "this quarter" vs. "last quarter" can look very
     similar in meaning but require genuinely different SQL.
   - **Tier C — staleness-aware reuse:** if the build-time schema crawler later detects that a
     table used in some approved trajectory has since changed structure, flag that trajectory
     `needs_review` (reusing the exact same status vocabulary the guardian step already uses at
     build-time), so a stale approved answer doesn't keep getting silently reused forever after the
     underlying table it depended on has changed.
4. **Use the "Reject" signal too**, even without building a full correction flow right away — just
   recording "this SQL was rejected for this question" is useful on its own, both to exclude bad
   examples from future Tier A few-shot context, and to give a live, real-usage accuracy signal on
   top of the static Phase 0 golden eval set.

---

## 8. Where all of this actually lives (a map of the code)

If you go looking through the actual repository, here's what maps to what described above:

- `rivdw_buildtime/` — the whole build-time app described in section 3, plus the Query screen's
  UI.
  - `database/schema_crawler.py` — reads database structure only (section 3, step 1).
  - `pipeline/single_db_pipeline.py` — the real entry point the UI actually runs; calls the AI once
    per table (section 3, step 2).
  - `pipeline/nodes/enrich_node.py` — the actual Groq-calling code, plus the three-step JSON
    fallback parser.
  - `pipeline/nodes/guardian_node.py` — the rules-based quality gate (section 3, step 3).
  - `pipeline/nodes/normalise_node.py` — data cleanup/normalization between the other pipeline
    steps. (The pipeline always re-crawls fully each run — there's no incremental diff step.)
  - `vector_store/qdrant_store.py` — stores/searches embeddings in Qdrant (section 3, step 4, and
    section 5, step 1).
  - `database/sqlite_store.py` — the full version-history/audit log (section 3, step 5).
  - `config/strings.py` — every piece of UI text and every AI prompt template, in one file.
  - `config/databases.json` — which real databases this app knows about, and their connection
    details/schemas.
  - `ui/app.py` — the Streamlit app itself, with its two navigation entries (Build Metadata,
    Query).
  - `fastembed_cache/` — the committed, offline-ready embedding model files.
  - `qdrant_data/` — the actual on-disk vector database files.
  - `rivdw_app.db` — the SQLite history/audit database file.
  - `glossary/`, `glossary_data/` — empty, unused folders (see section 6's glossary idea for the
    plan to eventually use them).

- `rivdw_runtime/` — the runtime half described in section 4.
  - `query_engine.py` — the actual Phase 1 walking-skeleton search-and-draft flow.
  - `prompts.py` — the SQL-drafting prompt template used by the above.
  - `eval/golden_set.json` — the 25 hand-written test questions (Phase 0).
  - `eval/run_eval.py` — runs those test questions and reports Recall@K scores.

- `docs/` — background research. `Rearch_Papers_Summary.md` and `Embeddings_BestPractise.md` hold
  the detailed notes that sections 5 and 6 above summarize; the rest are the original research PDFs
  those notes were drawn from.

- `IMPLEMENTATION_SUMMARY.md` and `PENDING_ITEMS.md` (same folder as this file) — the two documents
  this explanation was written from; they track, respectively, what currently exists and why, and
  what's still on the backlog. Worth checking directly if you want the most up-to-date state,
  since this file is a snapshot and those two get updated as work continues.

---

## 9. One-paragraph summary, if you only remember one thing

RIVDW teaches a computer, in advance, what a company's databases actually mean in plain English (by
having an AI describe every table and column, with a human able to review and correct every
description), stores those descriptions as searchable "meaning fingerprints" (embeddings), and then
lets a business user ask a plain-English question and get back the matching tables/columns plus a
drafted (but not auto-run) SQL query. The teaching half (build-time) is fully built. The
question-answering half (runtime) has its simplest possible working version built and verified, with
a clearly ordered, currently-unbuilt roadmap ahead of it to make search more accurate (Phase 2), let
generated SQL actually run safely (Phase 3), and eventually let the system learn from real usage by
remembering and reusing previously human-approved answers.
