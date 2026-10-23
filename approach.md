# Approach Document

## Tri9T AI Engineering Internship Assignment — CT-200 QA Traceability API

## 1. Objective and assignment interpretation

The assignment is to build an API around the supplied CT-200 PDF manuals. The separate assignment update clarifies that parsing must operate on the PDF rather than a Markdown transcription. The implementation therefore reads PDF text directly with PyMuPDF when usable text is present and has a Tesseract OCR fallback for documents with very little extractable text. The goal is a useful, inspectable hierarchy for these manuals, not a universal parser for arbitrary PDFs.

The API also needs to preserve document versions, pin user selections to a specific version and exact source text, generate structured QA ideas from those selections, and expose staleness when the source changes.

## 2. Architecture and data model

- **FastAPI** provides document ingestion, section browsing, node retrieval, search, version changes, selections, generation, and generation retrieval.
- **SQLite and SQLAlchemy** store documents, immutable document versions, per-version tree nodes, selections, and selection items.
- **Node records** include heading, level, body text, parent, sibling order, node type, normalized logical key, and a SHA-256 content hash. Hash input normalizes whitespace and includes the heading and body.
- **Version identity** is deterministic: normalized heading path plus a sibling occurrence suffix. Node rows are created separately for each document version; re-ingestion does not mutate earlier rows.
- **Selection items** retain node/version references and snapshots of the heading, body text, and content hash. Selecting a section expands to its descendants in tree order so child requirements are not omitted.
- **Generated output** is stored in a local JSON file using atomic replacement and a process-local lock. This is a deliberate lightweight alternative to a separate MongoDB service for the assignment. It is not safe as a multi-process production database.
- **LLM output** is requested as JSON and validated with Pydantic. A bounded retry handles provider errors or malformed output. If validation still fails, the API returns an error and does not persist the invalid result.

## 3. PDF extraction and hierarchy reconstruction

The parser first extracts text from each page with PyMuPDF. If the total extracted text is very short, it attempts to rasterize the document and run Tesseract. The parser recognizes numbered headings, short all-caps headings, and labels such as Appendix, WARNING, CAUTION, NOTE, Table, and Figure. Text before the first numbered heading is preserved in a `Document preamble` node.

Numbered headings are attached using section-number prefixes. If an intermediate section is missing, the parser searches upward for the nearest available ancestor. Sibling order follows the extracted reading order, even when section numbers appear out of sequence. Logical keys are generated from normalized heading paths and occurrence counts, preserving repeated headings rather than globally deduplicating them.

The supplied manuals expose several specific cases that informed the rules:

| Observed case | Handling |
|---|---|
| Section `2.1.1.1 Battery Life Under Typical Use` appears without a `2.1.1` heading | Attach it to the nearest existing numbered ancestor, Section 2.1 |
| Section 3.4 appears before Section 3.3 | Preserve source order under the same Section 3 parent; do not reorder by section number |
| “Error Codes” is used in Sections 4.2 and 7.1 | Keep both nodes; their distinct parent paths give them distinct logical keys |
| General Specifications and Error Codes are visually tabular | Add targeted table, row, and cell nodes while retaining the original flattened text in the section body |
| PDF text contains line wraps and typographic ligatures such as `Cuﬀ` and `Deﬂates` | Normalize whitespace for matching; preserve source text for retrieval |
| Content crosses page boundaries | Preserve extracted page reading order and append the resulting text to its active node |

Table reconstruction is intentionally limited to the recognizable General Specifications and Error Codes regions in the supplied manuals. Specifications rows are paired from their extracted parameter/value sequence. Error-code rows are reconstructed using the code markers and conservative text rules. If the heuristic cannot confidently split a row, the original section text remains available; arbitrary tables are not claimed to be structurally understood.

Lists and numbered instructions remain in section body text rather than becoming dedicated list-item nodes. Figure labels can be recognized, but the parser does not extract image objects or reliably associate a caption with its parent section. It also does not retain page coordinates. These are explicit limits of the current representation.

## 4. Findings across manual versions

The supplied v1 and v2 PDFs share the main hierarchy and differ in several requirements relevant to traceability:

- Battery life changes from approximately 300 to 250 measurement cycles; the low-battery indicator threshold changes from below 15% to below 10%.
- Cuff inflation increments change from 40 mmHg to 30 mmHg.
- E3 auto-deflation changes from within 2 seconds to within 1.5 seconds.
- The v2 Error Codes table adds E6 for Bluetooth sync failure.
- The v2 manual adds Section 5.3, Data Export.

These differences are included in parser regression assertions so a future change that drops the content or fails to recognize the new section is detectable. The version and staleness APIs are designed to make the same changes visible after ingestion.

## 5. Implementation refinements and how failures were found

The project history recorded in the source documentation shows an initial limitation: PDF extraction retained the two tables as flattened text, so downstream consumers could not address rows and cells as separate nodes. Reviewing the extracted CT-200 content against the table headers and row patterns exposed that limitation. The parser was then extended with bounded rules for the supplied specifications and error-code tables, while keeping the original text as a fallback. Regression tests assert the v2 error-code table's header and E1–E6 rows, including an E6 cell.

The manual review also identified hierarchy cases that a naive sequential-heading stack could misclassify: a missing intermediate number, out-of-order section numbering, and repeated “Error Codes” headings in different sections. The current parser uses numeric parent prefixes for hierarchy, preserves source order, and scopes duplicate identity by parent path. The test suite contains synthetic parser fixtures and actual-manual checks for version differences and structured tables; focused cases for the numbering and repeated-heading irregularities are included in `tests/test_parser.py`.

The validation approach is a combination of manual PDF content inspection and regression assertions. Automated checks do not prove visual layout fidelity. Before submission, render and visually review the pages containing both tables and unusual headings, and keep honest screenshots or logs from the real API v1→v2 flow. This package-preparation step has not run the test suite or produced real-run screenshots, so it makes no claim that those checks have passed here.

## 6. Version matching and change reporting

Every ingestion creates immutable node rows for a document version. A node with the same normalized heading path and sibling occurrence maps to the same logical key in another version. Its body need not be identical; content hashes determine whether the matching node changed. The node changes endpoint returns version statuses and a lightweight unified diff of body text.

This strategy is easy to explain and test, but a renamed or moved heading can change the path and look like a removed node plus a new node. Inserting a duplicate before an existing sibling can also shift occurrence suffixes. The implementation does not use fuzzy matching because a false match could incorrectly preserve traceability. A future version could report ambiguous matches explicitly rather than silently choosing one.

## 7. Selection, generation, and staleness

A selection references a document and exact version. It captures requested nodes plus their descendants once, in tree order, and stores each node's heading, text, and hash snapshot. Re-ingestion therefore leaves the selection's source content intact.

The generation prompt asks for 3–5 concrete, executable QA ideas with preconditions, steps, expected results, exact requirement references, and a risk label. It prohibits invented numeric limits. Pydantic checks field types, required text, list bounds, risk values, and the 3–5 case count. Failed output is retried a bounded number of times and then returned as an error; unvalidated output is never stored. The provider is configurable through environment variables, and the API can start without a key.

Repeated requests reuse the most recent completed generation for a selection by default. `force_new=true` requests a new one. Each stored generation includes the exact source node/version/hash and text snapshots. At retrieval, those source hashes are compared with nodes having the same logical key in the latest version. A missing or changed source marks the generation stale; the API exposes the changed sources and the latest version checked. The system does not regenerate stale cases.

Hash-based staleness is intentionally conservative. It flags a punctuation change the same way it flags a changed pressure threshold. It cannot tell whether a change affects patient safety; a qualified human must review the diff.

## 8. Tests and demonstration

The repository includes:

- Synthetic parser fixtures for preamble retention, numbered parent/child relationships, repeated headings, and warning/caption-like labels.
- Checks against both supplied manuals for the battery, low-battery, inflation increment, E3 timing, E6, and Data Export changes.
- Assertions for structured General Specifications and Error Codes table nodes, including the v2 E1–E6 rows.
- API and LLM tests covering invalid input, selection/version references, malformed provider output, retries, and missing-key behavior.
- A deterministic synthetic end-to-end flow that ingests v1, creates a selection and stubbed generation, ingests v2, and confirms stale retrieval.
- A live `scripts/real_manual_flow.py` walkthrough that uses both supplied PDFs, the configured LLM, and changed/unchanged node checks against a running API.

The synthetic demo proves workflow mechanics only. The real-manual script is provided for the live provider and real API demonstration, but it has not been run in this package-preparation step. Neither script's existence is evidence of a successful run. Run `pytest -q`, `python scripts/demo_flow.py`, and `python scripts/real_manual_flow.py` from the repository root before submission, then record the actual results.

## 9. Required decision log

1. **Most likely silent wrong result:** text or a table row can be assigned to the wrong heading while still producing a valid tree. Catch it by comparing rendered PDF pages with the hierarchy, checking text coverage, and adding page-specific parser fixtures for every observed irregularity.
2. **Where simplicity was chosen over correctness:** the parser uses heading and section-number heuristics plus two targeted table rules rather than layout-aware document analysis. Arbitrary tables, multi-column pages, mixed layouts, and caption association would be the first extraction failures in broader use.
3. **Unsupported input:** a scanned page embedded in a mostly text-based PDF may not be OCRed. The current fallback activates only when the entire document yields very little text, so the page can be missed or mis-parented. A per-page text-quality check and OCR fallback would handle this more safely.

## 10. Further improvements

- Apply per-page OCR fallback when an individual page has little or no extractable text.
- Store page numbers and bounding boxes for each text block to support visual traceability.
- Add a review state for ambiguous headings, table rows, and parent assignments.
- Extend table/figure extraction only when supported by page-specific fixtures and visual comparisons.
- Add conservative node matching with explicit ambiguity reporting.
- Replace the process-local JSON generation store with a transactional store for multi-process deployments.
- Add database migrations and run the real v1/v2 workflow against a clean environment before each submission.

## 11. Submission notes

The provided ZIP is a source snapshot and does not contain the original Git repository metadata. The assignment asks for genuine incremental Git history, so initialize or clone the intended GitHub repository and commit real project changes in reviewable stages; do not present fabricated or backdated history as original development history. The README contains setup and run instructions, and this file is the approach document to upload with the repository URL.
