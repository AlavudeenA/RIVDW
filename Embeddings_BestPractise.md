# Embedding & Retrieval Best Practices — Applied to RIVDW

This file used to be a raw dump of general 2025–2026 RAG research notes. It's been rewritten below
into categories, each judged against **RIVDW's actual shape** — not a generic document-RAG system.

## Why RIVDW is a different case than most of this research assumes

Almost all of the source research (chunking strategy, parent-child retrieval, late chunking,
table/code/conversation-specific splitting) is written for **long-document RAG**: policy PDFs,
manuals, chat transcripts — things that must be split into pieces before they fit in a vector.

RIVDW is not that. Its corpus is:

- **Already atomic.** Each Qdrant entry is one LLM-written sentence-or-two description of one
  table or one column ([single_db_pipeline.py](rivdw_buildtime/pipeline/single_db_pipeline.py)).
  There is no 2,000-token document to chunk — chunking, as a problem, mostly doesn't exist here.
- **Small.** A handful of databases' worth of tables/columns — thousands of entries at most, not
  millions. Most of the "at scale" research (billion-vector ANN tuning, quantization, multi-vector
  ColBERT indexes) targets a scale RIVDW isn't at and shouldn't optimize for yet.
- **Naturally two-tier already.** A table-level entry (`column_name=""`) plus one entry per column
  — this *is* a parent/child structure, already built, without anyone designing it as one
  ([qdrant_store.py](rivdw_buildtime/vector_store/qdrant_store.py)).
- **Query-side, not built yet.** The mismatch that matters most for RIVDW isn't chunk size — it's
  that a business question ("show me compliance violations this quarter") is phrased nothing like
  a column description ("Stores unique 14-character identifiers for..."). That's an
  asymmetric-query problem, not a chunking problem.

So most of the categories below get a **Not Applicable** verdict for a reason specific to RIVDW —
not because the research is wrong, but because it's solving a problem RIVDW doesn't have.

---

## Category-by-category verdicts

### 1. Chunking strategies (semantic splitters, late chunking, structure-aware splitting, fixed 512-token recursive splitting)

**Verdict: Not applicable to build-time.** There's no document to split — each table/column
description is generated whole by the LLM as one unit. Don't add a chunking layer that doesn't
solve a real problem here.

**But the underlying idea survives in a different form**: the research's "parent-child" pattern
(retrieve a small precise unit, but return the larger context around it) maps directly onto
something RIVDW already has — table entries as parents, column entries as children. **Adopt this
at retrieval time**: when a column entry is the best match for a query, pull in its sibling table
entry (and maybe sibling columns) before handing context to the LLM, the same way "retrieve small,
return big" recommends. This is cheap — the data's already there, it just isn't used yet.

### 2. Contextual enrichment before embedding (Anthropic-style contextual retrieval)

**Verdict: Partially done — worth extending.** RIVDW already embeds `description + human_notes`
together ([qdrant_store.py:63-67](rivdw_buildtime/vector_store/qdrant_store.py#L63-L67)), which is
a basic form of this. But the embedded text currently **excludes the table name, column name, and
database context** — only the generated description goes into the vector. If a description happens
not to restate the column name in plain words, the embedding may miss keyword-level matches.

**Recommend**: prepend a short prefix to the embed text, e.g.
`"{db_display_name} > {table_name}.{column_name}: {description}"`, similar to the research's
"Document: X / Section: Y / Passage: Z" pattern. Cheap, no new infrastructure, directly addresses
a known gap in the current code.

### 3. Metadata filtering ("metadata compass")

**Verdict: Data already exists, just isn't used at query time yet.** Every entry already carries
`domain_tag`, `source_db`, `schema_name`, and `guardian_status` as Qdrant payload fields
([qdrant_store.py:69-87](rivdw_buildtime/vector_store/qdrant_store.py#L69-L87)), and `_build_filter()`
already supports filtering by them. The Query screen just doesn't call it yet — `query.py` shows
"Search logic coming soon." **Adopt**: once search is wired up, use domain tag / source db as a
pre-filter (or a UI dropdown) before the vector search runs, not just as display metadata.

### 4. Asymmetric query-document handling (HyDE, dual-encoder query/doc modes)

**Verdict: Worth adopting for runtime.** This is RIVDW's real mismatch problem — business
questions and column descriptions are written in completely different registers. **HyDE-style
approach fits well**: before embedding the user's question, have the LLM draft a short hypothetical
table/column description that would answer it, then embed *that* to search — description-to-description
matching instead of question-to-description matching. Cheap (one extra LLM call per query, and Groq
is fast) and directly targets RIVDW's actual retrieval gap.

### 5. Hybrid retrieval — dense vector + lexical (BM25) fused with Reciprocal Rank Fusion

**Verdict: Strongly recommend adopting.** This is the single highest-value idea in the source
material for RIVDW specifically. Business database metadata is full of exact-match terms —
column codes, table names, abbreviations (`CDSCode`, `IMEI`, `pip_violations`) — the kind of
content the research repeatedly flags as where pure dense search underperforms lexical search.
RIVDW's future Query screen doing embedding-only search is likely to miss exact identifier matches
that a simple keyword index would catch instantly. Pair Qdrant's dense search with a lightweight
BM25 index (even a simple in-process one, given the corpus size) and fuse with RRF.

### 6. Cross-encoder reranking

**Verdict: Recommend adopting, and it's cheap at RIVDW's scale.** The research consistently finds
reranking the single largest accuracy lever, larger than fusion or embedding-model choice. Because
RIVDW's candidate pool per query is small (pull top 30–50 from a corpus of thousands, not millions),
running a cross-encoder reranker (e.g., a small BGE reranker) on that shortlist is fast even without
GPU. This slots in cleanly as the last step before results reach the LLM.

### 7. Multi-vector / late-interaction retrieval (ColBERT-style)

**Verdict: Not needed.** This solves a precision problem that shows up in large, dense technical
corpora with many near-duplicate passages. RIVDW's corpus is small and each entry is already short
and distinct; the added storage and operational complexity isn't justified. Skip unless hybrid +
rerank (above) turns out to be insufficient after measurement.

### 8. Graph / multi-hop / entity-relationship retrieval (GraphRAG, RAPTOR, SiReRAG)

**Verdict: Not needed yet — a lighter substitute already exists.** RIVDW's actual "relationship"
need is join-path discovery between tables, which the research also flags (missing join keys are
a top failure cause in text-to-SQL). But RIVDW already has a `related_tables` field on every entry
— extending that field (per the join-key recommendation already in `PROGRESS_SUMMARY.md`) covers
the same need without standing up a graph index. Revisit only if genuine multi-hop questions
("which tables two joins away from X relate to Y") become common.

### 9. Structured-data-aware embedding formatting (tables, rows, code)

**Verdict: Already doing the right thing, worth confirming as policy.** The research is explicit
that embedding a raw table row as `Premium Account | 21 | India` performs worse than a labeled
plain-English version. RIVDW's entire pipeline is built around exactly this correction — the LLM
writes a plain-English sentence rather than embedding the raw column list. Good, no change needed;
just don't regress this by ever short-circuiting straight to raw metadata for speed.

### 10. Skip RAG entirely for small corpora (full-context + prompt caching)

**Verdict: Worth keeping in mind as a real option at RIVDW's current size.** The research's
threshold (~200K tokens, ~500 pages) before RAG pays for itself is a plausible fit for RIVDW's
*current* few-database catalog. For a **single-database query**, it may be simpler and more
accurate to just inject that database's full set of table/column descriptions directly into the
prompt rather than doing vector search at all — no recall risk, no retrieval tuning. This stops
being true once RIVDW onboards many databases; worth treating as a scale-dependent switch rather
than a permanent choice either way.

### 11. Domain fine-tuning of the embedding model, with hard negatives

**Verdict: Not now.** Matches the same conclusion already reached in `PROGRESS_SUMMARY.md`'s
research-adoption list — no training infrastructure or labeled query/description pairs exist yet,
and several papers explicitly warn against fine-tuning without them. Revisit only after there's a
real query log to mine hard negatives from.

### 12. Evaluate retrieval before evaluating the LLM (Recall@K, MRR, golden query set)

**Verdict: Do this first, before any of the above.** None of the improvements above (contextual
prefixes, hybrid search, reranking, HyDE) can be judged as "better" without a baseline. Build a
small golden set — a few dozen realistic business questions mapped to the table(s) that should
answer them — and measure Recall@K on it *before and after* each change. This is the cheapest item
on this whole list and should be step one, not an afterthought.

### 13. Long-context degradation ("context rot")

**Verdict: Keep as a standing caveat, not a specific action.** Applies regardless of which path
RIVDW takes — whether stuffing a whole database's metadata into the prompt (item 10) or expanding
parent context (item 1), more retrieved text is not automatically better. Cap how much gets sent
to the LLM even when it's cheap to fetch more.

### 14. Chunk overlap tuning

**Verdict: Not applicable.** No chunking is happening in RIVDW's pipeline, so this parameter has
no analog here.

---

## What would be great for RIVDW, in adoption order

1. **Build a small golden eval set** (business question → expected table/column) — item 12. Free,
   and makes every later change measurable.
2. **Enrich the embedded text** with table/column name and DB context, not just the description —
   item 2. One-line change to `qdrant_store.py`.
3. **Use existing payload fields as retrieval filters** (domain tag, source db) — item 3. Data
   already exists; just needs to be wired into the Query screen.
4. **Add hybrid (dense + BM25) retrieval with RRF fusion** — item 5. Directly fixes the
   exact-identifier weak spot pure vector search has for database metadata.
5. **Add a cross-encoder reranker** on top of hybrid results — item 6. Cheap at this corpus size,
   consistently the highest-leverage step in the research.
6. **Use HyDE-style query rewriting** for the runtime Query screen — item 4. Addresses RIVDW's
   real mismatch: business questions vs. column-description language.
7. **Use the table/column parent-child structure at retrieval time** — item 1. Already-built data
   relationship, just needs to be used when assembling LLM context.

## What's not good for RIVDW's case (skip for now)

- Any document **chunking strategy** (semantic, late, structure-aware, fixed-token) — there's no
  document being chunked in the first place.
- **Multi-vector / ColBERT-style late interaction** — solves a precision problem at a corpus scale
  RIVDW doesn't have.
- **Graph-based multi-hop retrieval (GraphRAG, RAPTOR)** — the lighter `related_tables` field
  already covers RIVDW's actual join-path need.
- **Fine-tuning the embedding model** — no training data or infrastructure exists yet, and the
  research itself warns against doing this without hard negatives.
- **Quantization / ANN parameter tuning for scale** — targets billion-vector regimes; Qdrant's
  defaults are already sufficient for RIVDW's corpus size (also flagged in
  `PROGRESS_SUMMARY.md`'s research-adoption notes).

---

*Original source notes (uncategorized 2025–2026 RAG research) have been superseded by the
categorization above. If you need the raw research text again, it's recoverable from git history.*
