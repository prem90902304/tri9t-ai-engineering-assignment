#!/usr/bin/env bash
set -euo pipefail
BASE=http://127.0.0.1:8000
curl -s "$BASE/health"
# Replace paths with the actual CT-200 manual files from the assignment package.
curl -s -X POST "$BASE/documents/ingest?document_name=CT-200" -F "file=@data/ct200_manual_v1.pdf"
curl -s -X POST "$BASE/documents/ingest?document_name=CT-200" -F "file=@data/ct200_manual_v2.pdf"
curl -s "$BASE/documents"
