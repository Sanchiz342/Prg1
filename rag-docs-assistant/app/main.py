"""FastAPI application: REST API + the single-page chat UI in app/static."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.config import Settings
from app.ingest.loaders import SUPPORTED_EXTENSIONS, LoaderError
from app.rag import MAX_QUESTION_CHARS
from app.services import Services, build_services, seed_samples

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("docurag")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    k: int | None = Field(default=None, ge=1, le=20)
    doc_ids: list[int] | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = build_services(settings)
        if settings.seed_samples:
            seeded = seed_samples(services)
            if seeded:
                log.info("Seeded %d demo documents from %s", seeded, settings.samples_dir)
        app.state.services = services
        yield
        services.close()

    app = FastAPI(title="DocuRAG", version=__version__, lifespan=lifespan)

    def services(request: Request) -> Services:
        return request.app.state.services

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        """Write endpoints are open in dev; set RAG_API_KEY to require the X-API-Key header."""
        if settings.api_key and not (x_api_key and secrets.compare_digest(x_api_key, settings.api_key)):
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    @app.get("/api/health")
    def health(svc: Services = Depends(services)) -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "embedder": svc.embedder.id,
            "provider": svc.pipeline.generator.name,
            **svc.store.stats(),
        }

    @app.get("/api/documents")
    def list_documents(svc: Services = Depends(services)) -> list[dict]:
        return [d.__dict__ for d in svc.store.list_documents()]

    @app.post("/api/documents", dependencies=[Depends(require_key)])
    async def upload_documents(files: list[UploadFile] = File(...), svc: Services = Depends(services)) -> dict:
        limit = settings.max_upload_mb * 1024 * 1024
        results = []
        for upload in files:
            name = upload.filename or "unnamed"
            item: dict = {"filename": name}
            data = await upload.read(limit + 1)
            if len(data) > limit:
                item.update(status="error", error=f"File is larger than {settings.max_upload_mb} MB")
            else:
                try:
                    result = await run_in_threadpool(svc.ingestor.ingest_bytes, name, data)
                    item.update(status=result.status, document=result.document.__dict__)
                except LoaderError as exc:
                    item.update(status="error", error=str(exc))
            results.append(item)
        return {"results": results, "supported": sorted(SUPPORTED_EXTENSIONS)}

    @app.delete("/api/documents/{doc_id}", dependencies=[Depends(require_key)])
    def delete_document(doc_id: int, svc: Services = Depends(services)) -> dict:
        if not svc.store.delete_document(doc_id):
            raise HTTPException(status_code=404, detail="Document not found")
        return {"deleted": doc_id}

    @app.get("/api/documents/{doc_id}/chunks")
    def document_chunks(doc_id: int, svc: Services = Depends(services)) -> list[dict]:
        if svc.store.get_document(doc_id) is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return [c.__dict__ for c in svc.store.list_chunks(doc_id)]

    @app.get("/api/chunks/{chunk_id}")
    def get_chunk(chunk_id: int, svc: Services = Depends(services)) -> dict:
        chunk = svc.store.get_chunk(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        return chunk.__dict__

    @app.post("/api/ask")
    def ask(body: AskRequest, svc: Services = Depends(services)) -> dict:
        try:
            answer = svc.pipeline.ask(body.question, k=body.k, doc_ids=set(body.doc_ids) if body.doc_ids else None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return answer.to_dict()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
