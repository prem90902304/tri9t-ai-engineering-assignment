# Tri9T AI Engineering Internship Assignment — CT-200 QA Traceability API

A FastAPI backend that ingests a CT-200 PDF, reconstructs a hierarchical section tree, stores immutable document versions, pins selections to exact node/version content, generates structured QA test ideas through an OpenAI-compatible LLM endpoint, and reports stale generations after re-ingestion.

> **Source PDFs included:** `data/manuals/ct200_manual_v1.pdf` and `data/manuals/ct200_manual_v2.pdf` are the actual supplied manuals. The parser has been exercised against both and a regression test checks known version differences. The parser preserves the original table text and adds structured table/row/cell nodes for the recognizable General Specifications and Error Codes tables. This is a targeted reconstruction for the supplied manuals, not a claim of universal table extraction.

## Stack and design decisions

- **FastAPI + Pydantic:** REST API and strict validation of LLM JSON.
- **SQLAlchemy + SQLite:** documents, immutable versions, nodes, selections, and pinned snapshots.
- **JSON store for generations:** a justified lightweight local store, with atomic file replacement and a lock. It avoids requiring a MongoDB service for a small internship demo. For multi-process/production use, replace with MongoDB or a transactional database.
- **PyMuPDF first, OCR fallback:** use PDF text when usable; otherwise try `pdf2image` + Tesseract. OCR dependencies must be installed on the host.
- **Version matching:** normalized hierarchy path plus sibling occurrence (`heading~1`). Repeated headings under different parents remain distinct. This is deterministic and explainable but can fail if a heading is renamed, moved, or a preceding duplicate is inserted. Such cases may appear removed/new; fuzzy matching is a future enhancement.
- **Staleness:** compares selected source hashes with the corresponding logical node in the latest version. It deliberately flags any content change, including wording-only edits. It does not determine clinical significance; safety-critical thresholds should receive human review.
- **Repeated generation:** idempotent by default for a completed selection; `force_new=true` explicitly requests a fresh generation.
- **Malformed LLM output:** validated with Pydantic, retried with bounded exponential delay, then returns HTTP 502. No unvalidated output is persisted.

## Quick start

Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows; on macOS/Linux use: cp .env.example .env
```

Add an OpenAI-compatible API key to `.env` (Groq is the default endpoint) to enable generation. The API can start without a key, but `/generations` will return 502 until configured.

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`.

## Ingest v1 then v2

Place the supplied CT-200 manuals somewhere on your machine. In Swagger (`/docs`) call `POST /documents/ingest` twice:
1. Upload v1 with query `document_name=CT-200`.
2. Upload v2 with the same `document_name=CT-200`.

The same source file hash is not ingested twice. A changed PDF creates version 2 and keeps version 1 rows intact.

Example with curl:

```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_name=CT-200" \\
  -F "file=@data/ct200_manual_v1.pdf"
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_name=CT-200" \\
  -F "file=@data/ct200_manual_v2.pdf"
curl "http://127.0.0.1:8000/documents/1/sections?version=1"
curl "http://127.0.0.1:8000/documents/1/sections?version=2"
```

The exact document ID depends on your local database. Do not assume it is 1 if you have ingested other documents.

## Main API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/documents/ingest?document_name=CT-200` | Ingest a PDF as a new version |
| `GET` | `/documents` | List documents and latest versions |
| `GET` | `/documents/{id}/sections?version=1` | List top-level sections; defaults to latest |
| `GET` | `/nodes/{node_id}` | Get node, recursively nested children, aggregate subtree text, and content hash |
| `GET` | `/search?q=pressure&document_id=1&version=2` | Search headings and body text |
| `GET` | `/nodes/{node_id}/changes` | Compare matching logical nodes across versions |
| `POST` | `/selections` | Save named selection pinned to a version |
| `POST` | `/generations` | Generate 3–5 QA ideas for a selection |
| `GET` | `/generations?selection_id=1` | Retrieve generated output and staleness |
| `GET` | `/generations?node_id=1` | Retrieve generations involving a node |
| `GET` | `/generations/{generation_id}` | Retrieve one generation and staleness |
| `GET` | `/health` | Health check |

### Selection request

Use node IDs returned for the same version:

```json
{
  "name": "Pressure safety requirements",
  "document_id": 1,
  "version_id": 1,
  "node_ids": [1, 2]
}
```

The API rejects nodes that do not belong to the selected version. Selecting a section captures that node and all descendants, once each, in tree order; this prevents an empty parent body from omitting the actual section requirements and ensures a changed child can make the generation stale. The selection stores each node's content hash, heading snapshot, and body-text snapshot so its exact source survives later re-ingestion. The response distinguishes `requested_node_ids` from `captured_node_ids`.

### Generation request

```json
{"selection_id": 1, "force_new": false}
```

The response stores the generation alongside exact source node/version/hash snapshots. By default, repeated requests reuse the last successful generation for that selection. Set `force_new` to `true` to request a fresh output.

### Staleness demo flow

1. Ingest v1 and create a selection from a section.
2. Generate QA ideas; keep the returned `generation_id`.
3. Ingest v2 with the same `document_name`.
4. Call `GET /generations/{generation_id}`.
5. Inspect `staleness.stale` and `staleness.changed_sources`.
6. Call `GET /documents/{id}/sections?version=1` to confirm v1 still exists.
7. Submit the same selection again; it remains pinned to v1 and returns its prior generation unless `force_new=true`.

The current implementation intentionally checks against the **latest** version while retaining the generation's original source snapshots. It does not automatically regenerate stale test cases.

## Parser and hierarchy notes

The parser uses conservative patterns for numbered headings, short all-caps headings, and common labels such as Appendix, WARNING, CAUTION, NOTE, Table, and Figure. It retains repeated headings as separate nodes and preserves leading text in a `Document preamble` node instead of silently dropping it. Parent-child nesting is inferred from heading depth.

This is not a generic PDF parser. Table reconstruction currently uses conservative rules for the two recognizable tables in the supplied manuals (specifications and error codes); it retains original flattened text as a fallback. Other layout-sensitive tables, multi-column reading order, captions, unnumbered mixed-case headings, headers/footers, OCR mistakes, and headings split across lines can still be misclassified. The tests include synthetic PDF fixtures and regression checks against both actual CT-200 PDFs, including structured table-node assertions.

## Verify the complete versioning/staleness flow

Run the deterministic end-to-end demo from the repository root:

```bash
python scripts/demo_flow.py
```

The script creates synthetic v1/v2 PDFs, uses a stubbed LLM response, ingests v1, selects a parent section (including its descendants), creates a generation, ingests v2 with a changed pressure-release requirement, and asserts that retrieval reports the generation as stale. Its output explicitly identifies this as a synthetic workflow demo; real-manual parsing is covered separately by regression tests, and the demo does not test a live LLM provider.

## Run tests

```bash
pytest -q
```

The suite covers synthetic PDF fixtures and regression checks against the actual v1/v2 manuals, hierarchy, duplicate heading identity, normalized hashes, version pinning, repeated-upload idempotency, subtree selection, generation deduplication, changed-node diffs, staleness detection, malformed LLM output retries, missing API key behavior, and invalid PDF rejection.

## Decision log

1. **Most likely silent wrong result:** A paragraph or table row can be assigned to the wrong heading while producing a valid tree. Catch it with a page-by-page visual review, section counts, text coverage checks, and fixture tests for every irregular CT-200 page.
2. **Simplicity over correctness:** Heading-depth heuristics and targeted table rules are used instead of full layout analysis. Complex tables and multi-column pages may fail in production and must be surfaced for manual review.
3. **Unsupported input:** A heading split across lines or a complex multi-column table may not be reconstructed correctly. The parser preserves extracted text where possible but does not claim structural certainty; record such pages as known limitations and add targeted rules/tests before submission.

## Suggested incremental Git history

Do not create one giant commit. Example sequence:
1. `chore: scaffold FastAPI project and configuration`
2. `feat: add SQLAlchemy document and version models`
3. `feat: parse PDF text into a section hierarchy`
4. `test: cover duplicate headings and content hashes`
5. `feat: ingest immutable document versions`
6. `feat: add browse search and change APIs`
7. `feat: pin selections to exact version snapshots`
8. `feat: validate and persist LLM QA generation`
9. `feat: expose generation staleness at retrieval`
10. `docs: add setup and end-to-end demo`

## Before submission

- Run against the actual v1/v2 PDFs from `AI Internship Assignment Files – CT-200`.
- Add tests for each irregularity explicitly listed in the manual/package.
- Confirm table/figure extraction and parent-child relationships visually.
- Demonstrate changed and unchanged nodes across real v1/v2 documents.
- Test malformed LLM responses and missing API key behavior.
- Include a Postman collection or saved curl script and screenshots/logs from the real run.
- Push real incremental commits to GitHub; do not claim test results that were not run.
