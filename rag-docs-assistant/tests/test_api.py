from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_pdf


def _client(settings):
    return TestClient(create_app(settings))


def test_health_and_seeded_documents(settings):
    with _client(replace(settings, seed_samples=True)) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok" and health["documents"] == 5
        names = [d["name"] for d in client.get("/api/documents").json()]
        assert "api-reference.md" in names


def test_upload_ask_and_inspect_chunk(settings):
    with _client(settings) as client:
        pdf = make_pdf(["The warranty period is 18 months.", "Returns are accepted within 30 days."])
        r = client.post(
            "/api/documents",
            files=[("files", ("policy.pdf", pdf, "application/pdf")), ("files", ("x.exe", b"MZ", "application/octet-stream"))],
        )
        results = r.json()["results"]
        assert results[0]["status"] == "added"
        assert results[1]["status"] == "error"

        ans = client.post("/api/ask", json={"question": "How long is the warranty period?"}).json()
        assert ans["grounded"]
        cite = ans["citations"][0]
        assert cite["document"] == "policy.pdf" and cite["page"] == 1
        assert cite["title"] == "policy.pdf, p. 1"

        chunk = client.get(f"/api/chunks/{cite['chunk_id']}").json()
        assert "18 months" in chunk["text"]


def test_validation_and_not_found(settings):
    with _client(settings) as client:
        assert client.post("/api/ask", json={"question": ""}).status_code == 422
        assert client.post("/api/ask", json={"question": "x" * 5000}).status_code == 422
        assert client.delete("/api/documents/999").status_code == 404
        assert client.get("/api/chunks/999").status_code == 404


def test_upload_size_limit(settings):
    with _client(replace(settings, max_upload_mb=1)) as client:
        big = b"word " * 300_000  # ~1.5 MB
        result = client.post("/api/documents", files=[("files", ("big.txt", big, "text/plain"))]).json()["results"][0]
        assert result["status"] == "error" and "larger" in result["error"]


def test_api_key_protects_write_endpoints(settings):
    with _client(replace(settings, api_key="s3cret")) as client:
        files = [("files", ("a.md", b"# A\nHello world.", "text/markdown"))]
        assert client.post("/api/documents", files=files).status_code == 401
        ok = client.post("/api/documents", files=files, headers={"X-API-Key": "s3cret"})
        assert ok.status_code == 200
        assert client.get("/api/documents").status_code == 200  # reads stay open


def test_ui_is_served(settings):
    with _client(settings) as client:
        page = client.get("/")
        assert page.status_code == 200 and "DocuRAG" in page.text
        assert client.get("/static/app.js").status_code == 200
