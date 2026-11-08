"""Authenticated HTTP API for journal entries and batch processing."""

import json
import logging
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request as StarletteRequest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import verify_token, verify_token_hash
from .config import Settings
from .entry_views import export_item, facet_values, filter_entries, summary_fields, summary_item
from .pipeline import Runner
from .search import tiered_search
from .storage import Conflict, NotFound, Store
from .web_auth import (
    SESSION_TTL_S,
    LoginLimiter,
    TokenChanged,
    new_session,
    revoke_session,
    valid_session,
)

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
_WEB_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'"
)


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
            return date.fromisoformat(value).isoformat()
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
            return date.fromisoformat(value).isoformat()
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


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_date: str | None = Field(default=None, alias="from")
    to_date: str | None = Field(default=None, alias="to")
    tag: list[str] = Field(default_factory=list)
    project: list[str] = Field(default_factory=list)
    skill: list[str] = Field(default_factory=list)

    @field_validator("from_date", "to_date")
    @classmethod
    def strict_iso_date(cls, value: str | None) -> str | None:
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use YYYY-MM-DD calendar dates")
        return value


class APIProblem(Exception):
    def __init__(self, status: int, title: str, detail: str, code: str | None = None) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.code = code


def _problem(status: int, title: str, detail: str, code: str | None = None) -> JSONResponse:
    content: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
    }
    if code is not None:
        content["code"] = code
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content=content,
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


class _PrivateErrorMiddleware:
    """Prevent unhandled exception values from reaching server traceback logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False
        complete = False

        async def track_send(message: Message) -> None:
            nonlocal started, complete
            match message["type"]:
                case "http.response.start":
                    started = True
                case "http.response.body":
                    complete = not message.get("more_body", False)
            await send(message)

        try:
            await self.app(scope, receive, track_send)
        except Exception as exc:
            _logger.error("Request failed (%s); check service configuration", type(exc).__name__)
            if not started:
                response = _problem(
                    500,
                    "Internal Server Error",
                    "The request failed unexpectedly; check service logs.",
                )
                await response(scope, receive, send)
            elif not complete:
                await send({"type": "http.response.body", "body": b"", "more_body": False})


def create_app(settings: Settings | None = None) -> FastAPI:
    from .config import load_settings

    actual_settings = settings or load_settings()
    store = Store(actual_settings)
    store.migrate()
    runner = Runner(store, actual_settings)
    from .embedding import EmbeddingService

    embedding_service = EmbeddingService(store, actual_settings)
    app = FastAPI(
        title="my-smart-journal", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.store = store
    app.state.runner = runner
    app.state.settings = actual_settings
    app.state.embedding = embedding_service
    app.add_middleware(_PrivateErrorMiddleware)

    @app.exception_handler(APIProblem)
    async def api_problem_handler(_request: Request, exc: APIProblem) -> JSONResponse:
        return _problem(exc.status, exc.title, exc.detail, exc.code)

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
        _logger.error("Request failed (%s); check storage configuration", type(_exc).__name__)
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

    limiter = LoginLimiter(actual_settings.login_rate_limit, actual_settings.login_rate_window_s)

    async def authorized(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        bearer_valid = credentials is not None and await run_in_threadpool(
            verify_token, store, credentials.credentials
        )
        cookie_value = request.cookies.get("msj_session")
        cookie_valid = not bearer_valid and await run_in_threadpool(
            valid_session, store, cookie_value
        )
        is_valid = bearer_valid or cookie_valid
        if not is_valid:
            raise APIProblem(401, "Unauthorized", "Sign in with a valid token.", "UNAUTHORIZED")
        if (
            cookie_valid
            and request.method in {"POST", "PATCH", "DELETE"}
            and request.headers.get("X-MSJ-CSRF") != "1"
        ):
            raise APIProblem(
                403,
                "Forbidden",
                "Cookie-authenticated changes require X-MSJ-CSRF: 1.",
                "CSRF_HEADER_MISSING",
            )

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/session", status_code=204)
    async def login(request: Request) -> Response:
        address = request.client.host if request.client is not None else "unknown"
        retry = limiter.retry_after(address)
        if retry is not None:
            return Response(
                content=json.dumps(
                    {
                        "type": "about:blank",
                        "title": "Too Many Requests",
                        "status": 429,
                        "detail": "Sign-in attempts are rate limited; wait before trying again.",
                        "code": "RATE_LIMITED",
                    }
                ),
                status_code=429,
                media_type="application/problem+json",
                headers={"Retry-After": str(retry)},
            )
        try:
            payload = json.loads(await _body(request, 4096))
            token = payload.get("token") if isinstance(payload, dict) else None
        except ValueError, AttributeError:
            token = None
        stored_hash = await run_in_threadpool(store.token_hash)
        if not isinstance(token, str) or not await run_in_threadpool(
            verify_token_hash, token, stored_hash
        ):
            raise APIProblem(401, "Unauthorized", "The supplied token is invalid.", "BAD_TOKEN")
        try:
            session_id = await run_in_threadpool(new_session, store, stored_hash or "")
        except TokenChanged:
            raise APIProblem(
                401,
                "Unauthorized",
                "The supplied token changed during sign-in; retry with the current token.",
                "BAD_TOKEN",
            ) from None
        response = Response(status_code=204)
        response.set_cookie(
            "msj_session",
            session_id,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=SESSION_TTL_S,
            secure=not actual_settings.insecure_cookies,
        )
        return response

    @app.delete("/api/v1/session", status_code=204)
    async def logout(request: Request) -> Response:
        await run_in_threadpool(revoke_session, store, request.cookies.get("msj_session"))
        response = Response(status_code=204)
        response.delete_cookie(
            "msj_session",
            path="/",
            httponly=True,
            samesite="strict",
            secure=not actual_settings.insecure_cookies,
        )
        return response

    @app.get("/api/v1/session", status_code=204)
    async def session_status(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Response:
        bearer_valid = credentials is not None and await run_in_threadpool(
            verify_token, store, credentials.credentials
        )
        cookie_valid = await run_in_threadpool(
            valid_session, store, request.cookies.get("msj_session")
        )
        if not bearer_valid and not cookie_valid:
            raise APIProblem(401, "Unauthorized", "Sign in with a valid token.", "UNAUTHORIZED")
        return Response(status_code=204)

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
            declared_type = str(upload.content_type or "").split(";", maxsplit=1)[0].strip().lower()
            extension = _AUDIO_TYPES.get(declared_type)
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
            entry_date_value = form.get("entry_date")
            if entry_date_value is not None:
                try:
                    entry_date = date.fromisoformat(str(entry_date_value)).isoformat()
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
                entry_date=entry_date if entry_date_value else None,
            )
        finally:
            await form.close()

    @app.get("/api/v1/entries", dependencies=[Depends(authorized)])
    def list_entries(
        from_date: Annotated[str | None, Query(alias="from")] = None,
        to_date: Annotated[str | None, Query(alias="to")] = None,
        tag: Annotated[list[str] | None, Query()] = None,
        project: Annotated[list[str] | None, Query()] = None,
        skill: Annotated[list[str] | None, Query()] = None,
        view: str = "summary",
        order: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if (
            limit < 1
            or limit > 500
            or offset < 0
            or view not in {"summary", "full"}
            or order not in {"asc", "desc"}
        ):
            raise APIProblem(
                422,
                "Validation Error",
                "Use limit 1-500, non-negative offset, view summary/full, and order asc/desc.",
                "INVALID_ENTRY_QUERY",
            )
        try:
            filtered = filter_entries(
                _all_entries(store),
                from_date=from_date,
                to_date=to_date,
                tags=tag,
                projects=project,
                skills=skill,
                order=order,
            )
        except ValueError:
            raise APIProblem(
                422,
                "Validation Error",
                "Date bounds must be ISO calendar dates.",
                "INVALID_DATE_FILTER",
            ) from None
        page = filtered[offset : offset + limit]
        if view == "summary":
            page = [summary_item(item) for item in page]
        return {"items": page, "total": len(filtered)}

    @app.get("/api/v1/entries/{entry_id}", dependencies=[Depends(authorized)])
    def get_entry(entry_id: str, history: bool = False) -> dict[str, Any]:
        entry = store.get_entry(entry_id, history=history)
        return entry | summary_fields(entry)

    @app.get("/api/v1/entries/{entry_id}/neighbors", dependencies=[Depends(authorized)])
    def entry_neighbors(
        entry_id: str,
        from_date: Annotated[str | None, Query(alias="from")] = None,
        to_date: Annotated[str | None, Query(alias="to")] = None,
        tag: Annotated[list[str] | None, Query()] = None,
        project: Annotated[list[str] | None, Query()] = None,
        skill: Annotated[list[str] | None, Query()] = None,
        order: str = "desc",
    ) -> dict[str, str | None]:
        if order not in {"asc", "desc"}:
            raise APIProblem(
                422, "Validation Error", "order must be asc or desc.", "INVALID_ENTRY_QUERY"
            )
        all_entries = _all_entries(store)
        if not any(entry["id"] == entry_id for entry in all_entries):
            raise NotFound("Entry not found")
        filtered = filter_entries(
            all_entries,
            from_date=from_date,
            to_date=to_date,
            tags=tag,
            projects=project,
            skills=skill,
            order=order,
        )
        ids = [entry["id"] for entry in filtered]
        index = ids.index(entry_id) if entry_id in ids else None
        before = ids[index - 1] if index is not None and index > 0 else None
        after = ids[index + 1] if index is not None and index + 1 < len(ids) else None
        return {
            "newer": before if order == "desc" else after,
            "older": after if order == "desc" else before,
        }

    @app.get("/api/v1/facets", dependencies=[Depends(authorized)])
    def facets() -> dict[str, Any]:
        return facet_values(_all_entries(store))

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

    @app.get("/api/v1/search", dependencies=[Depends(authorized)])
    async def search(
        q: str,
        from_date: Annotated[str | None, Query(alias="from")] = None,
        to_date: Annotated[str | None, Query(alias="to")] = None,
        tag: Annotated[list[str] | None, Query()] = None,
        project: Annotated[list[str] | None, Query()] = None,
        skill: Annotated[list[str] | None, Query()] = None,
        tiers: str = "1,2,3,4,5",
    ) -> StreamingResponse:
        if not q.strip() or len(q) > 500:
            raise APIProblem(
                422, "Validation Error", "q must contain 1-500 characters.", "INVALID_QUERY"
            )
        try:
            selected = tuple(int(value) for value in tiers.split(","))
            if (
                not selected
                or len(set(selected)) != len(selected)
                or any(value not in range(1, 6) for value in selected)
            ):
                raise ValueError
            all_items = filter_entries(
                await run_in_threadpool(_all_entries, store),
                from_date=from_date,
                to_date=to_date,
                tags=tag,
                projects=project,
                skills=skill,
            )
        except ValueError:
            raise APIProblem(
                422,
                "Validation Error",
                "tiers must be a unique comma list from 1 to 5 and date filters must be valid.",
                "INVALID_SEARCH_FILTER",
            ) from None
        allowed_ids = {item["id"] for item in all_items}
        documents = [
            item
            for item in await run_in_threadpool(store.search_documents)
            if item.get("entry_id") in allowed_ids
        ]
        events = tiered_search(
            q,
            documents,
            lambda: [row for row in store.fts_search(q) if row.get("entry_id") in allowed_ids],
            fuzzy_threshold=actual_settings.fuzzy_threshold,
            tiers=selected,
            semantic=lambda: embedding_service.search(q, allowed_ids=allowed_ids),
        )

        def stream() -> Iterator[bytes]:
            try:
                for event in events:
                    yield (
                        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                    ).encode()
            except Exception as exc:
                _logger.error(
                    "Search failed (%s); check storage and search index", type(exc).__name__
                )
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

    _install_additional_routes(app, store, actual_settings, authorized)
    return app


def _all_entries(store: Store) -> list[dict[str, Any]]:
    return store.all_entries()


def _retrieve_sync(
    store: Store, embedding: Any, query: str, k: int, filters: RetrievalFilters
) -> dict[str, Any]:
    matched = filter_entries(
        _all_entries(store),
        from_date=filters.from_date,
        to_date=filters.to_date,
        tags=filters.tag,
        projects=filters.project,
        skills=filters.skill,
    )
    allowed_ids = {item["id"] for item in matched}
    semantic_rows, unavailable = embedding.search(query, allowed_ids=allowed_ids)
    bm25_rows = [row for row in store.fts_search(query) if row.get("entry_id") in allowed_ids]
    from .search import reciprocal_rank_fusion

    fused = reciprocal_rank_fusion([bm25_rows, semantic_rows], k=60)[:k]
    by_id = {entry["id"]: entry for entry in matched}
    semantic_by_id = {str(row["entry_id"]): row for row in semantic_rows}
    items: list[dict[str, Any]] = []
    for entry_id, score in fused:
        passage = semantic_by_id.get(entry_id, {}).get("passage")
        if not passage:
            passage = by_id[entry_id].get("text", "")[:1000]
        items.append(
            {
                "entry": summary_item(by_id[entry_id]),
                "score": round(score, 6),
                "passages": [passage] if passage else [],
            }
        )
    return {"items": items, "unavailable": unavailable}


def _install_additional_routes(
    app: FastAPI, store: Store, settings: Settings, authorized: Any
) -> None:
    @app.post("/api/v1/retrieve", dependencies=[Depends(authorized)])
    async def retrieve(request: Request) -> dict[str, Any]:
        try:
            payload = json.loads(await _body(request, 65_536))
            query, k, filters = (
                payload["query"],
                int(payload.get("k", 20)),
                payload.get("filters", {}),
            )
            if (
                not isinstance(query, str)
                or not query.strip()
                or len(query) > 500
                or not 1 <= k <= 100
            ):
                raise ValueError
        except ValueError, TypeError, KeyError, AttributeError:
            raise APIProblem(
                422,
                "Validation Error",
                "Provide a query, k from 1-100, and an optional filters object.",
                "INVALID_RETRIEVAL_REQUEST",
            ) from None
        try:
            parsed_filters = RetrievalFilters.model_validate(filters)
        except ValidationError:
            raise APIProblem(
                422,
                "Validation Error",
                "Retrieval filters require ISO dates and string arrays for tag, project, "
                "and skill.",
                "INVALID_RETRIEVAL_FILTER",
            ) from None
        return await run_in_threadpool(
            _retrieve_sync, store, app.state.embedding, query, k, parsed_filters
        )

    @app.post("/api/v1/export", dependencies=[Depends(authorized)])
    async def export_entries(request: Request) -> Response:
        try:
            payload = json.loads(await _body(request, 65_536))
            ids, format_name = payload["entry_ids"], payload["format"]
            include, order = payload.get("include", {}), payload.get("order", "desc")
            if (
                not isinstance(ids, list)
                or not all(isinstance(value, str) for value in ids)
                or len(ids) > 1000
                or format_name not in {"text", "markdown", "json"}
                or not isinstance(include, dict)
                or not all(
                    isinstance(key, str) and isinstance(value, bool)
                    for key, value in include.items()
                )
                or order not in {"asc", "desc"}
            ):
                raise ValueError
        except ValueError, TypeError, KeyError:
            raise APIProblem(
                422,
                "Validation Error",
                "Provide up to 1000 entry ids, a supported format, include flags, and "
                "order asc/desc.",
                "INVALID_EXPORT_REQUEST",
            ) from None
        by_id = {item["id"]: item for item in await run_in_threadpool(_all_entries, store)}
        missing = list(dict.fromkeys(entry_id for entry_id in ids if entry_id not in by_id))
        if missing:
            raise APIProblem(
                404, "Not Found", "Unknown entry ids: " + ", ".join(missing[:20]), "ENTRY_NOT_FOUND"
            )
        from .exporting import render

        def render_selected() -> tuple[bytes, str, str]:
            return render(
                [export_item(by_id[entry_id]) for entry_id in dict.fromkeys(ids)],
                format=format_name,
                include=include,
                order=order,
            )

        content, media_type, extension = await run_in_threadpool(render_selected)
        filename = f"journal-export-{date.today().isoformat()}.{extension}"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    from .batch import BatchCoordinator
    from .compilations import CompilationService

    coordinator = BatchCoordinator(store, app.state.runner)
    app.state.batch_coordinator = coordinator
    compilation_service = CompilationService(store)
    app.state.compilation_service = compilation_service

    @app.get("/api/v1/queue", dependencies=[Depends(authorized)])
    def queue() -> dict[str, Any]:
        return coordinator.status()

    @app.post("/api/v1/batches/run", status_code=202, dependencies=[Depends(authorized)])
    def run_batch() -> dict[str, Any]:
        batch_id = coordinator.start(trigger="manual")
        if batch_id is None:
            raise APIProblem(
                409,
                "Conflict",
                "A batch is already running; wait for it to finish.",
                "BATCH_ALREADY_RUNNING",
            )
        return {"started": True, "batch_id": batch_id}

    @app.post("/api/v1/compilations", status_code=202, dependencies=[Depends(authorized)])
    async def create_compilation(request: Request) -> dict[str, Any]:
        try:
            payload = json.loads(await _body(request, 65_536))
            ids, purpose, period = payload["entry_ids"], payload["purpose"], payload["period"]
            if (
                not isinstance(ids, list)
                or not all(isinstance(value, str) for value in ids)
                or len(ids) > 1000
                or not isinstance(purpose, str)
                or not purpose.strip()
                or not isinstance(period, str)
                or not period.strip()
            ):
                raise ValueError
        except ValueError, TypeError, KeyError:
            raise APIProblem(
                422,
                "Validation Error",
                "Provide up to 1000 entry ids, a purpose, and a period.",
                "INVALID_COMPILATION_REQUEST",
            ) from None
        return await run_in_threadpool(compilation_service.create, ids, purpose, period)

    @app.get("/api/v1/compilations", dependencies=[Depends(authorized)])
    def list_compilations() -> list[dict[str, Any]]:
        return compilation_service.list()

    @app.get("/api/v1/compilations/{compilation_id}", dependencies=[Depends(authorized)])
    def get_compilation(compilation_id: str) -> dict[str, Any]:
        return compilation_service.get(compilation_id)

    @app.delete(
        "/api/v1/compilations/{compilation_id}", status_code=204, dependencies=[Depends(authorized)]
    )
    def delete_compilation(compilation_id: str) -> None:
        compilation_service.delete(compilation_id)

    if settings.web_dir is not None:
        from pathlib import Path

        root = Path(settings.web_dir).expanduser().resolve()

        @app.get("/{asset_path:path}", include_in_schema=False)
        async def web_asset(asset_path: str) -> Response:
            if asset_path == "api" or asset_path.startswith("api/"):
                raise HTTPException(404, "API route not found")
            target = (root / asset_path).resolve()
            if target != root and root not in target.parents:
                raise HTTPException(404, "Asset not found")
            selected = target if target.is_file() else (root / "index.html").resolve()
            if selected != root and root not in selected.parents:
                raise HTTPException(404, "Web index is outside MSJ_WEB_DIR")
            if not selected.is_file():
                raise HTTPException(404, "Web UI is not built")
            response = FileResponse(selected)
            hash_suffix = selected.stem.rsplit("-", maxsplit=1)[-1]
            hash_candidates = (hash_suffix, selected.stem.rsplit(".", maxsplit=1)[-1])
            hashed_name = any(
                candidate.isalnum()
                and (
                    len(candidate) == 8
                    or (
                        len(candidate) > 8
                        and all(character in "0123456789abcdefABCDEF" for character in candidate)
                    )
                )
                for candidate in hash_candidates
            )
            if selected.name == "index.html":
                response.headers["Cache-Control"] = "no-cache"
            elif hashed_name:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            response.headers.update(
                {
                    "Content-Security-Policy": _WEB_CSP,
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                    "Permissions-Policy": "microphone=(self)",
                }
            )
            return response
