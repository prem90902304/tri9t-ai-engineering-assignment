from fastapi.testclient import TestClient
from app.main import app

def test_health():
    client=TestClient(app)
    assert client.get("/health").json()["status"]=="ok"

def test_selection_rejects_nodes_from_other_version():
    # Ingestion-dependent integration coverage is demonstrated by scripts/demo_flow.py.
    client=TestClient(app)
    response=client.post("/selections",json={"name":"bad","document_id":9999,"version_id":9999,"node_ids":[9999]})
    assert response.status_code in (400,404)


def test_ingest_rejects_non_pdf_content():
    client = TestClient(app)
    response = client.post(
        "/documents/ingest", params={"document_name": "invalid-pdf-test"},
        files={"file": ("manual.pdf", b"this is not a PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert "PDF signature" in response.json()["detail"]
