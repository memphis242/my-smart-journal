"""Authenticated HTTP API for journal entries and batch processing."""

import json
import logging
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request as StarletteRequest

from .auth import verify_token
from .config import Settings
from .pipeline import Runner
from .search import tiered_search
from .storage import Conflict, NotFound, Store

_AUDIO_TYPES: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}
_bearer = HTTPBearer(auto_error=False)
_logger = logging.getLogger(__name__)


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    entry_date: str | None = None

    @field_validator("entry_date")
    @classmethod
    def valid_entry_date(cls, value: str | None) -> str | None:
        if value is not None:
            _ = date.fromisoformat(value)
        return value


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    tags: list[str] | None = None
    entry_date: str | None = None
    text: str | None = None

    @field_validator("entry_date")
    @classmethod
    def valid_entry_date(cls, value: str | None) -> str | None:
        if value is not None:
            _ = date.fromisoformat(value)
        return value

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Text must contain non-whitespace characters")
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> EntryPatch:
        if not self.model_fields_set:
            raise ValueError("Provide at least one entry field")
        if "tags" in self.model_fields_set and self.tags is None:
            raise ValueError("Tags must be an array of strings")
        if "entry_date" in self.model_fields_set and self.entry_date is None:
            raise ValueError("Entry date must be an ISO calendar date")
        if "text" in self.model_fields_set and self.text is None:
            raise ValueError("Text must contain non-whitespace characters")
        return self


class APIProblem(Exception):
    def __init__(self, status: int, title: str, detail: str) -> None:
        self.status = status
        self.title = title
        self.detail = detail


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


async def _body(request: Request, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise APIProblem(413, "Payload Too Large", "Request exceeds the configured size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(settings: Settings | None = None) -> FastAPI:
    from .config import load_settings

    actual_settings = settings or load_settings()
    store = Store(actual_settings)
    store.migrate()
    runner = Runner(store, actual_settings)
    app = FastAPI(
        title="my-smart-journal", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.store = store
    app.state.runner = runner
    app.state.settings = actual_settings

    @app.exception_handler(APIProblem)
    async def api_problem_handler(_request: Request, exc: APIProblem) -> JSONResponse:
        return _problem(exc.status, exc.title, exc.detail)

    @app.exception_handler(NotFound)
    async def not_found_handler(_request: Request, exc: NotFound) -> JSONResponse:
        return _problem(404, "Not Found", str(exc))

    @app.exception_handler(Conflict)
    async def conflict_handler(_request: Request, exc: Conflict) -> JSONResponse:
        return _problem(409, "Conflict", str(exc))

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return _problem(422, "Validation Error", str(exc))

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
        _logger.error("Request failed; check storage and service configuration", exc_info=_exc)
        return _problem(
            500, "Internal Server Error", "The request failed unexpectedly; check service logs."
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        response = _problem(exc.status_code, "HTTP Error", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Validation Error",
                "status": 422,
                "detail": "Request parameters are invalid.",
                "errors": issues,
            },
        )

    @app.exception_handler(ValidationError)
    async def pydantic_handler(_request: Request, _exc: ValidationError) -> JSONResponse:
        return _problem(422, "Validation Error", "Request body is invalid.")

    async def authorized(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        is_valid = credentials is not None and await run_in_threadpool(
            verify_token, store, credentials.credentials
        )
        if not is_valid:
            raise APIProblem(401, "Unauthorized", "Supply a valid bearer token.")

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/entries", status_code=201, dependencies=[Depends(authorized)])
    async def create_entry(request: Request) -> dict[str, Any]:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        max_request_bytes = actual_settings.upload_limit_bytes + 16_384
        body = await _body(request, max_request_bytes)
        if content_type == "application/json":
            try:
                payload = EntryCreate.model_validate(json.loads(body))
            except ValueError:
                raise APIProblem(
                    422, "Validation Error", "Provide non-empty text and valid entry fields."
                ) from None
            return await run_in_threadpool(
                store.create_entry,
                text=payload.text,
                title=payload.title,
                tags=payload.tags,
                entry_date=payload.entry_date,
            )
        if content_type != "multipart/form-data":
            raise APIProblem(
                415,
                "Unsupported Media Type",
                "Use application/json for text entries or multipart/form-data for audio.",
            )
        replayed = False

        async def replay_body() -> dict[str, Any]:
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        parsed_request = StarletteRequest(request.scope, replay_body)
        form = await parsed_request.form()
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise APIProblem(
                    422, "Validation Error", "Include an audio file in the multipart field 'file'."
                )
            extension = _AUDIO_TYPES.get(str(upload.content_type or "").lower())
            if extension is None:
                raise APIProblem(
                    415,
                    "Unsupported Media Type",
                    "Allowed audio types are WAV, MP3, MP4, WebM, Ogg, and FLAC.",
                )
            if upload.size is None or upload.size == 0:
                raise APIProblem(422, "Validation Error", "Audio file must not be empty.")
            if upload.size > actual_settings.upload_limit_bytes:
                raise APIProblem(
                    413, "Payload Too Large", "Audio exceeds the configured upload limit."
                )
            try:
                tags_value = json.loads(str(form.get("tags", "[]")))
            except ValueError:
                raise APIProblem(
                    422, "Validation Error", "Multipart tags must be a JSON array."
                ) from None
            if not isinstance(tags_value, list) or not all(
                isinstance(tag, str) for tag in tags_value
            ):
                raise APIProblem(
                    422, "Validation Error", "Multipart tags must be a JSON array of strings."
                )
            audio = await upload.read(actual_settings.upload_limit_bytes + 1)
            if len(audio) > actual_settings.upload_limit_bytes:
                raise APIProblem(
                    413, "Payload Too Large", "Audio exceeds the configured upload limit."
                )
            entry_date = form.get("entry_date")
            if entry_date is not None:
                try:
                    _ = date.fromisoformat(str(entry_date))
                except ValueError:
                    raise APIProblem(
                        422, "Validation Error", "entry_date must be an ISO calendar date."
                    ) from None
            return await run_in_threadpool(
                store.create_entry,
                audio=audio,
                extension=extension,
                title=str(form.get("title")) if form.get("title") else None,
                tags=tags_value,
                entry_date=str(entry_date) if entry_date else None,
            )
        finally:
            await form.close()

    @app.get("/api/v1/entries", dependencies=[Depends(authorized)])
    def list_entries(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500 or offset < 0:
            raise APIProblem(
                422, "Validation Error", "limit must be 1-500 and offset must be non-negative."
            )
        return store.list_entries(limit=limit, offset=offset)

    @app.get("/api/v1/entries/{entry_id}", dependencies=[Depends(authorized)])
    def get_entry(entry_id: str, history: bool = False) -> dict[str, Any]:
        return store.get_entry(entry_id, history=history)

    @app.patch("/api/v1/entries/{entry_id}", dependencies=[Depends(authorized)])
    async def patch_entry(entry_id: str, request: Request) -> dict[str, Any]:
        try:
            changes = json.loads(await _body(request, actual_settings.upload_limit_bytes + 16_384))
        except ValueError:
            raise APIProblem(400, "Bad Request", "Body must be valid JSON.") from None
        try:
            patch = EntryPatch.model_validate(changes)
        except ValidationError as exc:
            raise APIProblem(422, "Validation Error", "Provide valid entry fields.") from exc
        return await run_in_threadpool(
            store.patch_entry, entry_id, patch.model_dump(exclude_unset=True)
        )

    @app.delete("/api/v1/entries/{entry_id}", status_code=204, dependencies=[Depends(authorized)])
    def delete_entry(entry_id: str) -> None:
        store.delete_entry(entry_id)

    @app.get("/api/v1/jobs", dependencies=[Depends(authorized)])
    def list_jobs(status: str | None = None) -> list[dict[str, Any]]:
        return store.list_jobs(status=status)

    @app.post("/api/v1/jobs/{job_id}/retry", dependencies=[Depends(authorized)])
    def retry_job(job_id: str) -> dict[str, Any]:
        return store.retry_job(job_id)

    @app.post("/api/v1/batches/run", dependencies=[Depends(authorized)])
    def run_batch() -> dict[str, Any]:
        return runner.run_batch()

    @app.get("/api/v1/search", dependencies=[Depends(authorized)])
    async def search(q: str) -> StreamingResponse:
        if not q.strip() or len(q) > 500:
            raise APIProblem(422, "Validation Error", "q must contain 1-500 characters.")
        documents = await run_in_threadpool(store.search_documents)
        events = tiered_search(
            q,
            documents,
            lambda: store.fts_search(q),
            fuzzy_threshold=actual_settings.fuzzy_threshold,
        )

        def stream() -> Iterator[bytes]:
            try:
                for event in events:
                    yield (
                        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                    ).encode()
            except Exception:
                _logger.exception("Search failed; check storage and search index")
                yield (
                    json.dumps(
                        {
                            "done": True,
                            "error": {
                                "status": 500,
                                "detail": "Search failed; check storage and service logs.",
                            },
                        }
                    )
                    + "\n"
                ).encode()

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app
