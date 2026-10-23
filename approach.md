# Approach Document — Tri9T AI CT-200 Assignment

## 1. Architecture
FastAPI exposes ingestion, browsing, search, version-diff, selection, generation, and retrieval endpoints. SQLite stores immutable document versions and tree nodes. Each node stores a heading, level, body, parent ID, sibling order, logical key, and normalized SHA-256 content hash. Selections store node/version pairs and snapshots of the exact text/hash. Generated output is persisted separately in an atomic JSON store.

## 2. OCR and parsing
The pipeline attempts text extraction with PyMuPDF. If extracted text is effectively empty, it falls back to rasterizing pages and using Tesseract OCR. The hierarchy builder uses numbered-heading depth, all-caps heading heuristics, and known labels. Parent stacks are popped when the next heading is at the same or shallower depth. Duplicate headings are not globally deduplicated; identity includes a normalized path and occurrence number among siblings. Unclassified text is retained under a preamble node rather than discarded.

**Validation caveat:** the assignment brief was provided, but the separate CT-200 v1/v2 manual PDFs were not present in the uploaded file. Therefore, the exact structural irregularities in those manuals have not yet been verified. This document describes the implementation's rules and known risks; update it with page-specific findings after inspecting the actual manuals.

## 3. Version matching
Each version has immutable node rows. The matcher uses normalized hierarchy path + duplicate occurrence suffix. Exact body equality is not required for identity; `content_hash` determines whether content changed. This is simple and explainable, but heading renames/moves or duplicate insertions may change the path and cause a false removed/new classification. A future improvement would use conservative fuzzy matching on parent identity, heading similarity, and body similarity, with an explicit ambiguous-match status rather than forcing a match.

## 4. LLM prompt and validation
The prompt requests 3–5 QA ideas, JSON-only output, concrete steps, expected results, source heading references, risk labels, and no invented thresholds. Pydantic validates the shape and field constraints. A bounded retry strategy handles transient failures and malformed outputs; after retries, the API returns an error and stores nothing. The configured provider uses an OpenAI-compatible chat-completions API. Secrets belong in `.env`, never in Git.

## 5. Selection and staleness
A selection is tied to one document version and captures each requested node plus its descendants once, in tree order. Each captured node stores text/hash snapshots. This makes selecting a parent section useful even when its body is empty and ensures descendant changes are visible to staleness checks. Re-ingestion does not mutate that selection. A generation records the selection ID, source node/version IDs, content hashes, and source text snapshots. Retrieval compares each source hash with the same logical key in the latest version. Any content change marks the generation stale. This errs on the side of warning rather than overlooking changes, but treats a punctuation edit like a changed safety threshold; it is not a clinical risk classifier and requires human review.

## 6. Idempotency
A repeated request returns the latest successful generation for a selection by default. `force_new=true` requests a new generation. This avoids duplicate cost and preserves reproducibility for the default path.

## 7. Tests and failure discovery
Included tests cover duplicate-heading identity, whitespace-normalized hashes, parent path retention, body-change hash sensitivity, health endpoint, and rejection of invalid selection references. These are starter regression tests. After receiving the actual CT-200 manuals, add fixtures for every irregularity observed (tables, duplicate headings, numbering gaps, unnumbered headings, captions, multi-column pages, split headings, and OCR artifacts where present). Compare extracted hierarchy to page images and record missed/mis-parented content.

## 8. Required decision log
1. **Most likely silent error:** incorrect parent assignment or lost table cell ordering. Catch with page-by-page visual comparison, text-coverage checks, and targeted fixtures.
2. **Simplicity chosen:** heading-depth heuristics rather than layout-aware document understanding. Complex tables and multi-column reading order are likely first production failures.
3. **Unsupported input:** complex multi-column tables and line-wrapped headings may not be structurally reconstructed correctly. Preserve text when possible, flag known limitations, and add targeted rules rather than silently claiming correctness.

## 9. What I would improve with more time
- Run a manual-specific inspection pass and add page-indexed parser rules.
- Persist page number and bounding boxes for every text block.
- Model tables, figures, and captions as typed nodes with source coordinates.
- Add a review queue for ambiguous heading classification.
- Use conservative fuzzy node matching with ambiguity reporting.
- Add schema migration tooling, transactional generation metadata, and a multi-process-safe generated-output store.
- Add integration tests that ingest the actual v1/v2 files and assert expected changed/unchanged logical nodes.


## Validation against supplied CT-200 PDFs

The supplied update explicitly requires parsing PDF directly rather than Markdown. The two source files are included in `data/manuals/`. Regression checks against the real files confirm: battery life changes from about 300 cycles to about 250; low-battery indication changes from below 15% to below 10%; cuff inflation increments change from 40 mmHg to 30 mmHg; E3 auto-deflation changes from within 2 seconds to within 1.5 seconds; the v2 error-code table adds E6 Bluetooth sync failure; and v2 adds Section 5.3 Data Export. The parser currently retains table content as text, so exact row/cell extraction remains a known limitation rather than a claimed feature.


## Table reconstruction update

The supplied CT-200 PDFs include a two-column specifications table and an error-code table. The parser now adds explicit `table`, `table_row`, and `table_cell` nodes for these recognizable regions while retaining the original flattened text in the parent section as a fallback. This is intentionally a bounded, testable rule for the supplied manuals; arbitrary visual tables and multi-column layouts still require further layout analysis and human review. Regression tests verify the v2 error-code table contains a header plus E1–E6 rows and that E6 is represented as a cell.
