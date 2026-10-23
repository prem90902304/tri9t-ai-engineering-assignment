"""End-to-end API test using synthetic manuals; actual CT-200 PDFs are external inputs."""
import io
import uuid

import fitz
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.db import SessionLocal
from app.models import Document, DocumentVersion, SectionNode, Selection, SelectionItem
from sqlalchemy import select, delete


def pdf_bytes(threshold_seconds: int, unchanged_text: str = "Display systolic and diastolic values.") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    text = (
        "1 Safety\n"
        "General safety information.\n"
        "1.1 Pressure release\n"
        f"The device must release pressure within {threshold_seconds} seconds.\n"
        "1.2 Display\n"
        f"{unchanged_text}\n"
    )
    page.insert_text((72, 72), text, fontsize=11)
    result = doc.tobytes()
    doc.close()
    return result


def test_version_pinning_generation_dedup_and_staleness(monkeypatch, tmp_path):
    document_name = f"CT-200-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "generated_store_path", str(tmp_path / "generated.json"))
    monkeypatch.setattr(main_module, "generate_cases", lambda context: {
        "test_cases": [
            {"title": f"Check pressure release {i}", "preconditions": "Device is powered on",
             "steps": ["Start a measurement", "Observe pressure release"],
             "expected_result": "The device follows the selected manual requirement",
             "requirement_refs": ["Pressure release"], "risk": "medium"}
            for i in range(1, 4)
        ], "assumptions": []
    })
    client = TestClient(main_module.app)
    created_doc_id = None
    try:
        v1_pdf = pdf_bytes(2)
        first = client.post(
            "/documents/ingest", params={"document_name": document_name},
            files={"file": ("ct200_manual_v1.pdf", v1_pdf, "application/pdf")},
        )
        assert first.status_code == 200, first.text
        v1 = first.json()
        created_doc_id = v1["document_id"]
        assert v1["version"] == 1
        duplicate_upload = client.post(
            "/documents/ingest", params={"document_name": document_name},
            files={"file": ("ct200_manual_v1.pdf", v1_pdf, "application/pdf")},
        )
        assert duplicate_upload.status_code == 200
        assert duplicate_upload.json()["unchanged_upload"] is True
        assert duplicate_upload.json()["version"] == 1

        sections = client.get(f"/documents/{created_doc_id}/sections", params={"version": 1}).json()["sections"]
        assert len(sections) == 1
        root = sections[0]
        tree = client.get(f"/nodes/{root['id']}").json()
        pressure = next(n for n in tree["children"] if n["heading"] == "Pressure release")
        display = next(n for n in tree["children"] if n["heading"] == "Display")
        assert "within 2 seconds" in tree["full_text"]
        assert "Display systolic and diastolic values." in tree["full_text"]

        # Selecting a parent captures its complete subtree so a changed child cannot be missed.
        root_selection_response = client.post("/selections", json={
            "name": "Complete safety section", "document_id": created_doc_id,
            "version_id": v1["version_id"], "node_ids": [root["id"]],
        })
        assert root_selection_response.status_code == 200, root_selection_response.text
        root_selection = root_selection_response.json()
        assert set(root_selection["captured_node_ids"]) == {root["id"], pressure["id"], display["id"]}
        root_generation = client.post("/generations", json={"selection_id": root_selection["selection_id"]})
        assert root_generation.status_code == 200, root_generation.text
        root_generation_id = root_generation.json()["generation_id"]

        selection_response = client.post("/selections", json={
            "name": "Pressure release requirement", "document_id": created_doc_id,
            "version_id": v1["version_id"], "node_ids": [pressure["id"]],
        })
        assert selection_response.status_code == 200, selection_response.text
        selection_id = selection_response.json()["selection_id"]

        generated = client.post("/generations", json={"selection_id": selection_id})
        assert generated.status_code == 200, generated.text
        generation_id = generated.json()["generation_id"]
        assert len(generated.json()["payload"]["test_cases"]) == 3
        assert "within 2 seconds" in generated.json()["source_nodes"][0]["text"]

        duplicate = client.post("/generations", json={"selection_id": selection_id})
        assert duplicate.status_code == 200
        assert duplicate.json()["generation_id"] == generation_id
        assert duplicate.json()["deduplicated"] is True

        second = client.post(
            "/documents/ingest", params={"document_name": document_name},
            files={"file": ("ct200_manual_v2.pdf", pdf_bytes(3), "application/pdf")},
        )
        assert second.status_code == 200, second.text
        v2 = second.json()
        assert v2["version"] == 2

        old_sections = client.get(f"/documents/{created_doc_id}/sections", params={"version": 1}).json()
        assert old_sections["version"] == 1
        stale = client.get(f"/generations/{generation_id}").json()["staleness"]
        assert stale["stale"] is True
        assert stale["changed_sources"][0]["reason"] == "content_changed"
        root_stale = client.get(f"/generations/{root_generation_id}").json()["staleness"]
        assert root_stale["stale"] is True
        assert any(change["logical_key"] == pressure["logical_key"]
                   for change in root_stale["changed_sources"])

        latest_sections = client.get(f"/documents/{created_doc_id}/sections", params={"version": 2}).json()["sections"]
        latest_root = latest_sections[0]
        latest_tree = client.get(f"/nodes/{latest_root['id']}").json()
        latest_display = next(n for n in latest_tree["children"] if n["heading"] == "Display")
        assert display["logical_key"] == latest_display["logical_key"]
        assert display["content_hash"] == latest_display["content_hash"]

        changes = client.get(f"/nodes/{pressure['id']}/changes").json()
        assert changes["changed"] is True
        assert changes["latest_status"] == "changed"
        assert any("2 seconds" in line or "3 seconds" in line for line in changes["diff_summary"]["unified_diff"])
    finally:
        if created_doc_id is not None:
            db = SessionLocal()
            try:
                version_ids = list(db.scalars(select(DocumentVersion.id).where(DocumentVersion.document_id == created_doc_id)).all())
                if version_ids:
                    node_ids = list(db.scalars(select(SectionNode.id).where(SectionNode.version_id.in_(version_ids))).all())
                    selection_ids = list(db.scalars(select(Selection.id).where(Selection.document_id == created_doc_id)).all())
                    if selection_ids:
                        db.execute(delete(SelectionItem).where(SelectionItem.selection_id.in_(selection_ids)))
                        db.execute(delete(Selection).where(Selection.id.in_(selection_ids)))
                    db.execute(delete(SectionNode).where(SectionNode.version_id.in_(version_ids)))
                    db.execute(delete(DocumentVersion).where(DocumentVersion.id.in_(version_ids)))
                db.execute(delete(Document).where(Document.id == created_doc_id))
                db.commit()
            finally:
                db.close()
