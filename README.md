# RIVDW — Build-Time Metadata Ingestion System

Reads database structure from SQL Server and Oracle, generates plain-English descriptions using an LLM, and stores the enriched metadata in a vector database for natural-language querying.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure your environment
cp .env.example .env
# Edit .env — fill in your Groq API key and database connection details

# 3. Run the app
python -m streamlit run ui/app.py
```

## What This System Does

RIVDW is a tool that teaches a computer what your databases mean — so that later, people can ask questions in plain English like "show me all compliance violations this quarter" and the system knows which tables and columns to look at.

The build-time pipeline does the preparation work:

1. It connects to your databases and reads the list of every table and column (not the actual data — just the structure).
2. It sends each table to an AI model (Groq) and asks it to write a plain-English description: what the table stores, which business process uses it, and what a business analyst would call it.
3. A "guardian" checks every description for quality — rejects thin descriptions, flags missing domain tags, catches filler phrases.
4. The approved descriptions are saved into a vector database (Qdrant) so the system can later find the right table even if the user uses a slightly different word.
5. Every version of every description is saved to a SQLite archive so you can see the full history of changes.

You can review and correct AI descriptions through the **Build Metadata** screen, and all your edits are preserved in the history log.

The **Query** screen then lets you type a plain-English question and get back the matched tables/columns plus a drafted (not executed) SQL query — the first working slice of the runtime half of RIVDW. See `IMPLEMENTATION_SUMMARY.md` at the repo root for the full build-time + runtime story, including what's still ahead.

---

## Connecting Your Local SQL Server

Add the display name, type, and schema to `config/databases.json` (this file is safe to commit):

```json
{
  "name": "compliance_db",
  "display_name": "Compliance Database",
  "db_type": "sqlserver",
  "schema": "dbo",
  "domain_tag": "compliance",
  "owner": "your-name@company.com",
  "description": "Describe what this database contains."
}
```

Then add one line to your `.env` file with the connection string (this file stays secret):

```
compliance_db=mssql+pyodbc:///?odbc_connect=DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost\SQLEXPRESS;DATABASE=ComplianceDB;Trusted_Connection=yes;
```

`Trusted_Connection=yes` means the app uses your Windows login — no username or password needed.

You must have the **ODBC Driver 17 for SQL Server** installed. Download it from Microsoft's website if it is not already on your machine.

---

## Screens

| Screen              | Purpose                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------- |
| **Build Metadata**  | Select one database → generate AI descriptions → edit inline → export/import Excel → view full change history |
| **Query**           | Type a plain-English question → get matched tables/columns, an explanation, and a drafted SQL query (not executed) |

## LLM Configuration

- **Primary**: Groq API (`GROQ_API_KEY` in `.env`)
- **Fallback**: VS Code LM API — requires the `vscode-lm-sample` extension running in VS Code. Set `GROQ_ENABLED=false` to force fallback.

## Adding a New Database

There are two steps — one file for metadata (safe to commit), one line in `.env` for the secret connection string.

**Step 1 — add an entry to `config/databases.json`:**

```json
{
  "name": "my_db",
  "display_name": "My Database",
  "db_type": "sqlserver",
  "schema": "dbo",
  "domain_tag": "compliance",
  "owner": "team@example.com",
  "description": "Short description of what this database contains."
}
```

The `name` field is the key — it must match exactly what you put in `.env`.

**Step 2 — add one line to `.env`:**

```
my_db=mssql+pyodbc:///?odbc_connect=DRIVER={ODBC Driver 17 for SQL Server};SERVER=server.example.com,1433;DATABASE=MyDatabase;UID=readonly_user;PWD=secret;
```

For Oracle:

```
my_oracle_db=oracle+oracledb://readonly_user:secret@host:1521/?service_name=SERVICENAME
```

Restart the app after editing either file.

## Project Structure

```
rivdw_buildtime/
├── config/              # Settings and all UI/prompt strings
├── database/            # DB connection registry and schema crawler
├── pipeline/            # Plain Python pipeline (no LangGraph) and its processing nodes
├── models/              # Pydantic data models
├── vector_store/        # Qdrant + FastEmbed operations
├── fastembed_cache/     # Committed embedding model files — works fully offline after clone
└── ui/                  # Streamlit app and page screens (Build Metadata, Query)

rivdw_runtime/
├── query_engine.py      # Phase 1: embed question -> Qdrant search -> Groq drafts SQL (not executed)
├── prompts.py           # SQL-drafting prompt template
└── eval/                # Golden eval set + Recall@K scoring, measured against real generated metadata
```

---

## File Guide — What Each File Does

| File                               | What it does in plain language                                                                                                                                             |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.env`                             | Your secret config — database passwords, Groq API key. Never commit this file.                                                                                             |
| `.env.example`                     | A safe template showing what goes in `.env`. Commit this one.                                                                                                              |
| `config/databases.json`            | List of databases and their metadata — display name, type, schema, domain tag, owner, description. Safe to commit. Add a new entry here to register a database.            |
| `config/settings.py`               | Reads `.env` and makes all settings available to the rest of the code. Looks up connection strings by matching each `databases.json` name to a `.env` key.                 |
| `config/strings.py`                | Every word that appears in the UI or in an AI prompt lives here. Change a label or prompt in one place and it updates everywhere.                                          |
| `database/connection_registry.py`  | Reads the database list from settings and creates a live connection for each one.                                                                                          |
| `database/schema_crawler.py`       | Connects to each database and reads the list of tables and columns from the system catalog — never reads actual data.                                                      |
| `database/sqlite_store.py`         | Manages the local SQLite file: stores run history, every version of every metadata entry (the change log), and app-level settings.                                         |
| `models/metadata_entry.py`         | The data shape for one table or column description — what fields it has, what values are allowed.                                                                          |
| `models/pipeline_state.py`         | The data shape for what flows between pipeline steps — carries raw entries, enriched entries, counts, errors.                                                              |
| `pipeline/single_db_pipeline.py`   | The real pipeline entry point. Processes one database at a time, table by table: crawl → normalise → enrich → guardian → store. Used by the Build Metadata screen. Makes one LLM call per table (covers all columns at once). No LangGraph, no separate connect/crawl/diff nodes — always re-crawls everything it's asked to. |
| `pipeline/nodes/normalise_node.py` | Converts database-specific type names (like Oracle `NUMBER` or SQL Server `datetime`) into a consistent vocabulary.                                                        |
| `pipeline/nodes/enrich_node.py`    | Calls the LLM (Groq or VS Code LM) to write plain-English descriptions for each entry.                                                                                     |
| `pipeline/nodes/guardian_node.py`  | Checks every AI description for quality: Is it long enough? Does it avoid filler phrases? Is the domain tag set? Marks entries as approved, needs review, or rejected.     |
| `vector_store/embedding.py`        | Wraps FastEmbed (`bge-small-en-v1.5`, 384-dim vectors) — loads the model from the committed `fastembed_cache/`, no network call needed.                                    |
| `vector_store/qdrant_store.py`     | Saves, updates, and searches descriptions in the local Qdrant vector database. Embeds a context-prefixed string (`source_db > table.column: description`), not just the raw description, so identity is part of the vector, not only the payload. The vector store always holds the latest approved version. |
| `ui/app.py`                        | The Streamlit entry point. Only handles page routing — no business logic here.                                                                                             |
| `ui/pages/build_metadata.py`       | **Main working screen.** Select a database, generate AI descriptions, see them table by table, edit any description, export/import via Excel, save, and view the full change history at the bottom. |
| `ui/pages/query.py`                | The **Query** screen. Calls into `rivdw_runtime/query_engine.py` for the actual search + SQL-drafting logic — this file only handles rendering.                            |
| `../rivdw_runtime/query_engine.py` | Embeds the question, does a plain Qdrant top-5 search, asks Groq to draft one SQL query, and returns it for display only — it is never executed.                          |
| `../rivdw_runtime/eval/`           | `golden_set.json` (hand-written test questions with expected tables/columns) + `run_eval.py` (retrieval-only Recall@K scoring, no LLM calls) — the yardstick for any future retrieval changes. |
