## Research Paper - Automatic Metadata Extraction for Text-to-SQL:

The hardest part of writing SQL is not the SQL itself — it is understanding what is actually in the database.
They list the exact problems you face in WBRS too:

No documentation — column names like no_ltr_dt or cds_code with no explanation anywhere
Outdated documentation — someone documented the table three years ago, the schema changed, nobody updated the docs
Unclear data formats — is a name stored as "Smith John" or "John Smith" or "JOHN SMITH"?
Multiple formats in one column — half the rows use one format, half use another
Multiple date columns — four date fields in one table, which one do you use for "sign up date"?
Complex joins — two tables need to join on IMEI but one has 13 digits and the other has 14, with a "1" prefix. Took them a long time to discover that
Default values — telephone number field with 10% of rows containing "123-456-7890" as a placeholder. If you join on this you get billions of garbage rows

They then say: we built a system to automatically extract metadata to solve these problems, and it got us to #1 on the BIRD benchmark (the hardest text-to-SQL test in the world) — without using any hints or custom-tuned models, just GPT-4o.

Pages 2-3 — Database profiling (their first technique)
Profiling means: connect to a database, read a sample of the actual data, and compute statistics about each column. Not reading it for business use — reading it to understand what kind of data lives there.
Statistics they collect per column:

How many rows are NULL vs filled
How many distinct values exist
Min and max values
Most common values (top 10)
Character patterns (always 14 digits? Always uppercase?)
A minhash sketch — a mathematical fingerprint of the column's values

The minhash sketch is the clever bit. It lets you quickly find two columns that contain similar values — which tells you they might be joinable. If table_A.customer_id and table_B.cust_num both contain the same set of numbers, the minhash similarity will be high, which suggests these should be joined even if nobody documented it.
They then feed this profiling output to the LLM and ask it to write a plain-English description of what the column contains. Example:
Raw profile: CDSCode, 14 characters long, always numeric, sample values 01100170109835…
LLM produces: "The CDSCode column stores unique 14-character numeric identifiers for each school, where CDS stands for County-District-School."

Useful tips to generate query:
It is better to save Question-SQL pairs once result is verified with user and Text-to-SQL works much better when you give the LLM examples of similar question-SQL pairs.

---

## 2503.18596v4

Your Search Resolution Agent needs query rewriting. When the confidence score is below threshold, do not just do synonym expansion — use the LLM to analyse what was retrieved, infer what might be missing, rewrite the question to find it, and search again.

Your Search Resolution Agent's step 3 (surface top 3 tables) should use a two-agent debate pattern rather than just returning raw vector results. A Data Analyst agent and a Database Expert agent debating the answer produces far more reliable table selection than a single ranking.

Error 4 is particularly relevant for your join generation. Missing a join key column is responsible for 11.6% of all failures. Your enriched metadata needs to explicitly document join relationships between tables — not just what each column means in isolation.

---

## 2601.15709v1

# Trajectory Builder:

An admin types or selects a business question
The LLM generates the SQL and also explains its reasoning — which tables it chose, why, what join path it used
The admin reviews, corrects if needed, approves
The approved result (question + SQL + reasoning) gets stored
When a real user asks a question later, this stored knowledge is used to help generate better SQL.
This entire paper validates the value of reusing past successful queries rather than regenerating from scratch every time. Right now your cache stores: query text, SQL, tables used, timestamp. Adding a brief structured note about why those tables were chosen and what join path was used would help the SQL generation agent make better use of cached entries when handling similar-but-not-identical questions.

# What it should look like

Screen — Trajectory Builder (sits alongside your existing screens)

Input section: Admin types a business question in plain English
Generate button: LLM generates SQL + fills in the reasoning fields automatically
Review section: Admin sees and can edit:

The generated SQL (editable)
Tables chosen (editable list)
Why these tables (editable text)
Join path (editable)
Tricky parts or caveats (editable)

# One practical concern worth flagging

Trajectories can go stale. If a column gets renamed or a table gets restructured, the SQL in an approved trajectory might break. You need a simple staleness check:

When the schema crawler detects a change to a table that appears in a trajectory's tables_used list, automatically flag that trajectory as needs_review in Qdrant
The trajectory screen shows a "Stale trajectories" filter so the admin can review and update them

Test button: Actually runs the SQL against the source DB and shows a preview of results so admin can verify it is correct
Approve & Store button: Saves to the Qdrant collection with entry_type: approved_trajectory
Browse existing trajectories: A searchable table of all approved trajectories with edit and delete

# What the trajectory entry should look like

{
"entry_type": "approved_trajectory",
"id": "uuid",
"question": "Show me employees terminated in Q1 2025 not processed in PIP",
"domain": "compliance",
"approved_by": "admin@wf.com",
"approved_at": "2025-01-15T09:00:00",

"reasoning": {
"tables_chosen": ["pip_violations", "emp_master"],
"why_these_tables": "pip_violations holds the compliance status per employee. emp_master holds current employment status including termination date. Join is needed to correlate termination with PIP processing status.",
"join_path": "pip_violations.emp_id = emp_master.emp_id",
"filters_applied": "termination_date between Q1 dates, pip_processed = false",
"tricky_parts": "emp_master has multiple date fields — use termination_date not separation_date which is for voluntary leavers"
},

"sql": "SELECT e.emp_id, e.emp_name, e.termination_date FROM emp_master e LEFT JOIN pip_violations p ON p.emp_id = e.emp_id WHERE e.termination_date BETWEEN '2025-01-01' AND '2025-03-31' AND p.pip_processed = 0",

"source_dbs": ["DB_COMPLIANCE"],
"tables_used": ["pip_violations", "emp_master"],
"sensitivity_tier": "high",
"ttl_hours": 24
}

Step 1 — Schema metadata search

# Convert user question to a vector embedding

user_question = "Which terminated employees were not processed in PIP this quarter?"
question_vector = fastembed.embed(user_question)

# Search Qdrant — only look at schema metadata entries
# Note: query_points(), not search() — search() doesn't exist on the installed
# qdrant-client version (confirmed the hard way fixing vector_store/qdrant_store.py)

schema_results = qdrant_client.query_points(
collection_name="rivdw_metadata",
query=question_vector,
query_filter=Filter(
must=[FieldCondition(
key="entry_type",
match=MatchValue(value="schema_metadata")
)]
),
limit=10 # top 10 most relevant schema entries
).points

Step 2 — Trajectory search

# Same question vector, same collection

# But now filter to only approved_trajectory entries

trajectory_results = qdrant_client.query_points(
collection_name="rivdw_metadata",
query=question_vector,
query_filter=Filter(
must=[FieldCondition(
key="entry_type",
match=MatchValue(value="approved_trajectory")
)]
),
limit=3 # top 3 most similar past questions
).points

Step 3 — Combining into one context block
context = f"""

## Relevant database schema

{format_schema_results(schema_results)}

## Similar questions answered before

{format_trajectory_results(trajectory_results)}

## User question

{user_question}

Generate SQL to answer the user question using the schema and
prior examples above as context.
"""
