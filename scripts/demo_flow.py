"""Executable deterministic v1 -> v2 -> stale-generation demonstration.

This uses synthetic PDFs and a local stub for the LLM so it can run without an API key.
It demonstrates API/versioning mechanics only; it is not evidence that extraction was
validated against the actual CT-200 manuals.
Run from the repository root with: python scripts/demo_flow.py
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
import sys

# Allow invocation as `python scripts/demo_flow.py` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings


def make_pdf(seconds: int) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        (60, 60, 540, 740),
        "1 Safety\nGeneral safety information for the home blood pressure monitor.\n"
        "1.1 Pressure release\n"
        f"The device must release pressure within {seconds} seconds.\n"
        "1.2 Display\nDisplay systolic and diastolic values clearly.\n",
        fontsize=11,
    )
    result = doc.tobytes()
    doc.close()
    return result


def stub_generation(context: str) -> dict:
    return {
        "test_cases": [
            {
                "title": f"Verify selected safety requirement {index}",
                "preconditions": "The monitor is powered on and ready for use.",
                "steps": ["Start a measurement", "Observe the pressure release behavior"],
                "expected_result": "Observed behavior matches the selected manual requirement.",
                "requirement_refs": ["Pressure release"],
                "risk": "medium",
            }
            for index in range(1, 4)
        ],
        "assumptions": ["Synthetic demo fixture; verify all thresholds against the real manual."],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tri9t-demo-") as tmp:
        settings.generated_store_path = str(Path(tmp) / "generated.json")
        main_module.generate_cases = stub_generation
        client = TestClient(main_module.app)
        name = f"CT-200-DEMO-{uuid.uuid4().hex[:8]}"

        v1 = client.post(
            "/documents/ingest", params={"document_name": name},
            files={"file": ("ct200_manual_v1.pdf", make_pdf(2), "application/pdf")},
        )
        v1.raise_for_status()
        first = v1.json()
        roots = client.get(f"/documents/{first['document_id']}/sections", params={"version": first["version"]})
        roots.raise_for_status()
        root = roots.json()["sections"][0]

        selection_response = client.post("/selections", json={
            "name": "Safety section demo", "document_id": first["document_id"],
            "version_id": first["version_id"], "node_ids": [root["id"]],
        })
        selection_response.raise_for_status()
        selection = selection_response.json()

        generation_response = client.post("/generations", json={"selection_id": selection["selection_id"]})
        generation_response.raise_for_status()
        generation = generation_response.json()

        v2 = client.post(
            "/documents/ingest", params={"document_name": name},
            files={"file": ("ct200_manual_v2.pdf", make_pdf(3), "application/pdf")},
        )
        v2.raise_for_status()
        latest = v2.json()
        retrieval = client.get(f"/generations/{generation['generation_id']}")
        retrieval.raise_for_status()
        staleness = retrieval.json()["staleness"]

        result = {
            "demo_type": "synthetic fixture + stubbed LLM; not the actual CT-200 manuals",
            "v1": {"document_id": first["document_id"], "version": first["version"], "node_count": first["node_count"]},
            "selection": {"selection_id": selection["selection_id"], "captured_node_ids": selection["captured_node_ids"]},
            "generation_id": generation["generation_id"],
            "v2": {"version": latest["version"], "node_count": latest["node_count"]},
            "staleness": staleness,
        }
        print(json.dumps(result, indent=2))
        assert latest["version"] == 2
        assert staleness["stale"] is True
        assert any(item["reason"] == "content_changed" for item in staleness["changed_sources"])
        print("PASS: v1 -> selection -> generation -> v2 -> stale detection")


if __name__ == "__main__":
    main()
