# Tri9T AI Engineering Internship Assignment

## CT-200 QA Traceability API

A FastAPI service that ingests CT-200 PDF manuals, builds a hierarchical section tree, stores immutable document versions, saves selections against exact source versions, generates structured QA test ideas through an OpenAI-compatible LLM endpoint, and reports when a generation's source has changed in a later manual version.

The supplied manuals are included at `data/manuals/ct200_manual_v1.pdf` and `data/manuals/ct200_manual_v2.pdf`. PDF parsing and table reconstruction are tailored to these manuals; this project is not a general-purpose PDF understanding system.

## Repository structure

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI ingestion, browsing, search, comparison, selection, generation, and retrieval endpoints |
| `app/parser.py` | PDF text/OCR extraction, hierarchy construction, logical node keys, and targeted table reconstruction |
| `app/models.py`, `app/db.py` | SQLAlchemy models and SQLite setup |
| `app/llm.py`, `app/schemas.py` | LLM requests, bounded retries, and Pydantic response validation |
| `app/store.py` | Local JSON store for generated output |
| `data/manuals/` | Supplied CT-200 v1 and v2 PDF manuals |
| `tests/` | Parser, API, LLM, and versioning/staleness test definitions |
| `scripts/demo_flow.py` | Deterministic synthetic versioning and staleness walkthrough using a stub LLM |
| `scripts/real_manual_flow.py` | Live API walkthrough using the supplied manuals and configured LLM provider |
| `approach.md` | Design rationale, parser observations, validation notes, and limitations |

## Design overview

- **API and validation:** FastAPI with Pydantic request and LLM-output validation.
- **Documents and versions:** SQLAlchemy with SQLite stores documents, immutable versions, parsed nodes, and version-pinned selections.
- **Generated output:** A local JSON store uses atomic replacement and a lock. This keeps the demo simple; a multi-process or production deployment should use a transactional shared store.
- **PDF parsing:** PyMuPDF text extraction is attempted first, with OCR fallback when extracted text is insufficient. OCR requires the relevant host tools and Python dependencies.
- **Logical node identity:** A normalized hierarchy path and sibling occurrence distinguish repeated headings. Renamed or moved headings can appear as removed/new nodes.
- **Staleness:** Source hashes are compared with the corresponding logical nodes in the latest version. Any content change can mark a generation stale; this does not assess clinical significance or automatically regenerate test cases.
- **Generation reuse:** A completed generation is reused for the same selection by default. `force_new: true` requests a fresh generation.
- **Invalid LLM output:** Output is schema-validated, retried a bounded number of times, and not persisted unless validation succeeds.

## Requirements

- Python 3.11 or newer
- An OpenAI-compatible API key for live generation (the default endpoint is Groq)
- Tesseract OCR and Poppler if OCR fallback is needed

## Setup

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `LLM_API_KEY` in `.env` to enable live generation. The API can start without a key, but generation requests will fail until a provider is configured. Keep `.env` private and do not commit it.

Start the API from the repository root:

```bash
uvicorn app.main:app --reload
```

Interactive API documentation is available at <http://127.0.0.1:8000/docs>.

## Ingest the supplied manuals

In Swagger at `/docs`, call `POST /documents/ingest` twice using the same `document_name` value, such as `CT-200`: first upload `data/manuals/ct200_manual_v1.pdf`, then `data/manuals/ct200_manual_v2.pdf`. Re-uploading identical PDF content is idempotent; changed content creates another immutable version.

Example using curl from the repository root:

```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_name=CT-200" \
  -F "file=@data/manuals/ct200_manual_v1.pdf"

curl -X POST "http://127.0.0.1:8000/documents/ingest?document_name=CT-200" \
  -F "file=@data/manuals/ct200_manual_v2.pdf"

curl "http://127.0.0.1:8000/documents/1/sections?version=1"
curl "http://127.0.0.1:8000/documents/1/sections?version=2"
```

Use the document ID returned by the API in place of `1`; IDs depend on the local database contents.

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/documents/ingest?document_name=CT-200` | Ingest a PDF version |
| `GET` | `/documents` | List documents and latest versions |
| `GET` | `/documents/{id}/sections?version=1` | List top-level sections for a version |
| `GET` | `/nodes/{node_id}` | Return a node, descendants, subtree text, and hash |
| `GET` | `/search?q=pressure&document_id=1&version=2` | Search headings and body text |
| `GET` | `/nodes/{node_id}/changes` | Compare a logical node across versions |
| `POST` | `/selections` | Save a selection pinned to a version |
| `POST` | `/generations` | Generate validated QA ideas for a selection |
| `GET` | `/generations?selection_id=1` | Retrieve generations for a selection, including staleness |
| `GET` | `/generations?node_id=1` | Retrieve generations involving a node |
| `GET` | `/generations/{generation_id}` | Retrieve one generation and its staleness |

## Selections and generations

Create a selection using IDs returned for the same document version:

```json
{
  "name": "Pressure safety requirements",
  "document_id": 1,
  "version_id": 1,
  "node_ids": [1, 2]
}
```

Every requested node must belong to the selected version. Selecting a node captures it and its descendants in tree order. The selection stores source version, content hash, heading, and body-text snapshots, so later ingestion does not alter the selection. The response distinguishes `requested_node_ids` from `captured_node_ids`.

Request generation with:

```json
{"selection_id": 1, "force_new": false}
```

The prompt requests 3–5 structured QA ideas with executable steps, expected results, source references, and risk, without inventing thresholds. Completed output is reused by default for the same selection; set `force_new` to `true` for a new request. IDs in examples are illustrative and should be replaced with API responses.

## Versioning and staleness flow

1. Ingest v1 and create a selection from one or more returned node IDs.
2. Generate QA ideas and retain the returned `generation_id`.
3. Ingest v2 using the same `document_name`.
4. Retrieve the saved generation with `GET /generations/{generation_id}` and inspect `staleness` and `changed_sources`.
5. Retrieve v1 sections to confirm the original version remains available.

The selection remains pinned to its original version. Staleness is checked against the latest version; changed generations are not automatically regenerated.

## Parser behavior and limitations

The parser recognizes numbered headings and selected labels such as warnings, notes, appendices, tables, and figures. It preserves repeated headings under distinct parent paths and keeps text before the first numbered section in a preamble node. Targeted rules create table, row, and cell nodes for recognizable General Specifications and Error Codes tables, while retaining the original table text in the owning section as a fallback.

Parsing remains heuristic. Complex or arbitrary tables, multi-column reading order, figure/caption association, split headings, OCR mistakes, headers and footers, and some unnumbered headings may need manual review. OCR fallback currently depends on the document's total extracted text, so a scanned page inside an otherwise text-based PDF may not trigger OCR. A renamed or moved heading may be treated as removed/new, and any text change can mark a source stale.

## Demonstration and tests

Run the deterministic synthetic workflow:

```bash
python scripts/demo_flow.py
```

This walkthrough uses synthetic PDFs and a stub LLM. It demonstrates versioning and staleness behavior, but does not verify live provider access or visually validate every page of the supplied manuals.

For the live-manual walkthrough, configure `.env`, start the API, and run this from a second terminal at the repository root:

```bash
python scripts/real_manual_flow.py
```

This script uses the configured provider and creates demo records in the running API database. Run it only when you intend to use that provider and database.

Run the test suite from the repository root:

```bash
pytest -q
```

The suite contains synthetic fixtures and regression tests for parser behavior, the supplied manuals, API behavior, LLM validation, and versioning/staleness. These are test definitions; report a passing result only after running them successfully in your environment.

## Git history note

The supplied ZIP is a source snapshot and does not contain the original Git history. Any commits created while preparing a repository should describe changes that were actually made; do not present an illustrative commit sequence as pre-existing history.
