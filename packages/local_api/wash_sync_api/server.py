"""FastAPI application for Local Production Mode.

Security model (local, single-user tool):
* Binds to 127.0.0.1 by default; ``TrustedHostMiddleware`` rejects other Host
  headers (DNS-rebinding protection). No CORS headers are ever sent.
* A random session token is generated at start-up and injected into the served
  ``index.html``; every ``/api/v1`` call except ``/health`` must send it in
  ``X-Wash-Sync-Token``. Other websites cannot read it, so they cannot drive the API.
* Uploaded files stay in the private state directory. Downloads are restricted to
  known audit files and registered warehouse versions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from wash_sync import SCHEMA_VERSION, __version__
from wash_sync.adapters.base import detect_source
from wash_sync.apply import load_plan
from wash_sync.audit import FILES as AUDIT_FILES
from wash_sync.config import Settings
from wash_sync.engine import Engine, EngineError, IndexNotReady, is_warehouse_workbook, plan_summary
from wash_sync.planner import PreviewInput

from .jobs import JobManager

MAX_UPLOAD_BYTES = 400 * 1024 * 1024
MAX_WAREHOUSE_BYTES = 1024 * 1024 * 1024  # the real ~627k-row warehouse is ~70 MB
COOKIE_NAME = "wash_sync_session"
ALLOWED_UPLOAD_EXT = (".xlsx", ".xlsm", ".xls", ".csv")
AUDIT_DOWNLOADS = {f"{name}.csv" for name in AUDIT_FILES} | {"sync_summary.json", "report.html"}


def default_static_dir() -> Path | None:
    env = os.environ.get("WASH_SYNC_WEB_DIST")
    candidates = [Path(env)] if env else []
    root = Path(__file__).resolve().parents[3]
    candidates += [root / "apps" / "web" / "dist-local", Path(__file__).resolve().parent / "static"]
    return next((c for c in candidates if (c / "index.html").exists()), None)


class DecisionBody(BaseModel):
    choice: str
    reason: str = Field(min_length=1, max_length=2000)
    option_index: int | None = None


class BulkBody(BaseModel):
    classification: str
    choice: str
    reason: str = Field(min_length=1, max_length=2000)


class PreviewBody(BaseModel):
    upload_ids: list[str] = Field(min_length=1, max_length=50)
    source: str | None = None
    period: str | None = None
    strict: bool = False


class ApplyBody(BaseModel):
    authorize: bool = False
    require_all_decided: bool = False


class ApproveBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class InitBody(BaseModel):
    warehouse_path: str


class RollbackBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class PublishBody(BaseModel):
    version: int | None = None
    confirm: bool = False


def create_app(state_dir: str, static_dir: Path | None = None, token: str | None = None,
               allowed_hosts: list[str] | None = None) -> FastAPI:
    settings = Settings.load(state_dir)
    engine = Engine(settings, state_dir)
    jobs = JobManager()
    session_token = token or secrets.token_urlsafe(32)
    uploads: dict[str, dict[str, Any]] = {}
    app = FastAPI(title="WASH Warehouse Sync — local API", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["127.0.0.1", "localhost", "testserver"])
    app.state.token = session_token
    app.state.engine = engine
    app.state.jobs = jobs

    def require_token(request: Request, x_wash_sync_token: str | None = Header(default=None)) -> None:
        if x_wash_sync_token and secrets.compare_digest(x_wash_sync_token, session_token):
            return
        # Plain links (workbook / audit downloads) cannot send headers. A SameSite=Strict,
        # HttpOnly cookie set by the served page is accepted for read-only GET requests;
        # every state-changing request still requires the header.
        cookie = request.cookies.get(COOKIE_NAME)
        if request.method == "GET" and cookie and secrets.compare_digest(cookie, session_token):
            return
        raise HTTPException(status_code=401, detail="Missing or invalid session token. Reload the local app.")

    def actor(x_wash_sync_user: str | None = Header(default=None)) -> str:
        name = (x_wash_sync_user or "").strip()
        name = re.sub(r"[^\w .@\-]", "", name)[:64]
        return name or os.environ.get("WASH_SYNC_ACTOR") or os.environ.get("USER") or "local-user"

    auth = [Depends(require_token)]

    def _prepare_index(exc: IndexNotReady) -> None:
        """Start preparing the current version's index in the background unless that is already happening."""
        if exc.progress is None and not jobs.active("index") and not jobs.active_write():
            jobs.start("index", lambda j: engine.index(progress=lambda f: jobs.progress(j)(
                "index", f, "Preparing the warehouse for search")).close())

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else \
            response.headers.get("Cache-Control", "no-cache")
        return response

    def fail(exc: Exception, status: int = 400) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    # ---------------------------------------------------------------- health & status
    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "app": "wash-warehouse-sync", "mode": "local", "version": __version__,
                "schema_version": SCHEMA_VERSION, "requires_token": True}

    @app.get("/api/v1/status", dependencies=auth)
    def status() -> dict[str, Any]:
        data = engine.status()
        active = jobs.active_write()
        data["active_job"] = active.to_dict() if active else None
        data["index"] = None
        if data["current_version"]:
            try:
                engine.index(build=False).close()
                data["index"] = {"ready": True, "progress": 1.0}
            except IndexNotReady as exc:
                _prepare_index(exc)
                data["index"] = {"ready": False, "progress": exc.progress}
        data["state_dir"] = settings.state_dir
        return data

    @app.get("/api/v1/settings", dependencies=auth)
    def get_settings() -> dict[str, Any]:
        return engine.settings.public_dict()

    @app.put("/api/v1/settings", dependencies=auth)
    def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in body.items() if k != "state_dir"}
        try:
            updated = Settings.model_validate({**engine.settings.public_dict(), **body, "state_dir": settings.state_dir})
        except Exception as exc:  # noqa: BLE001
            raise fail(exc, 422) from exc
        updated.save()
        engine.settings = updated
        engine.store.history_append({"event": "SETTINGS_UPDATED", "keys": sorted(body)})
        return updated.public_dict()

    # ---------------------------------------------------------------- jobs
    @app.get("/api/v1/jobs/{job_id}", dependencies=auth)
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @app.post("/api/v1/jobs/{job_id}/cancel", dependencies=auth)
    def cancel_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        job.cancel_event.set()
        return job.to_dict()

    # ---------------------------------------------------------------- init
    @app.post("/api/v1/init", dependencies=auth)
    def init(body: InitBody, who: str = Depends(actor)) -> dict[str, Any]:
        path = os.path.abspath(os.path.expanduser(body.warehouse_path))
        if not path.lower().endswith((".xlsx", ".xlsm")) or not os.path.isfile(path):
            raise HTTPException(400, "Provide the full path of an existing .xlsx warehouse on this computer")
        try:
            job = jobs.start("init", lambda j: engine.init(path, who, progress=jobs.progress(j)))
        except RuntimeError as exc:
            raise fail(exc, 409) from exc
        return job.to_dict()

    @app.post("/api/v1/warehouse/load", dependencies=auth)
    async def load_warehouse(request: Request, file_name: str = Query(min_length=1, max_length=255),
                             reason: str | None = Query(default=None, max_length=2000),
                             who: str = Depends(actor)) -> dict[str, Any]:
        """Receive the current warehouse workbook (raw request body) and make it the current version.

        The body is streamed straight into the private state directory — no temporary copy
        elsewhere — and checked at once (is it the warehouse? already current? an older version?)
        before verification, copying and indexing run as a background job.
        """
        name = os.path.basename(file_name)
        if not name.lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(400, "The warehouse must be an .xlsx workbook")
        if jobs.active_write():
            raise HTTPException(409, "Another write operation is already running")
        target = engine.new_upload_path(name)
        folder = os.path.dirname(target)
        digest, size = hashlib.sha256(), 0
        try:
            with open(target, "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_WAREHOUSE_BYTES:
                        raise HTTPException(413, "The file is larger than the 1 GB warehouse limit")
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise HTTPException(400, "The file is empty")
            sha = digest.hexdigest()
            engine.check_load(target, name, sha)
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        except EngineError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise fail(exc, 400) from exc

        def run(job: Any) -> dict[str, Any]:
            try:
                return engine.load_warehouse(target, who, reason, name, sha, progress=jobs.progress(job))
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        try:
            return jobs.start("load", run).to_dict()
        except RuntimeError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise fail(exc, 409) from exc

    # ---------------------------------------------------------------- uploads & inspect
    @app.post("/api/v1/uploads", dependencies=auth)
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        name = os.path.basename(file.filename or "upload")
        if not name.lower().endswith(ALLOWED_UPLOAD_EXT):
            raise HTTPException(400, f"Unsupported file type; allowed: {', '.join(ALLOWED_UPLOAD_EXT)}")
        target = engine.new_upload_path(name)
        digest = hashlib.sha256()
        size = 0
        with open(target, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    os.remove(target)
                    raise HTTPException(413, "File is larger than the local upload limit (400 MB)")
                digest.update(chunk)
                out.write(chunk)
        upload_id = secrets.token_hex(8)
        try:
            det = detect_source(target).to_dict()
        except Exception as exc:  # noqa: BLE001
            det = {"source": None, "reason": f"Could not read file: {exc}"}
        if not det.get("source") and name.lower().endswith((".xlsx", ".xlsm")) and is_warehouse_workbook(target):
            det["reason"] = ("This is a WASH warehouse workbook, not a source export. To make it the current "
                             "warehouse, load it in step 1 (Current warehouse).")
        uploads[upload_id] = {"path": target, "file_name": name, "size": size, "sha256": digest.hexdigest()}
        return {"upload_id": upload_id, "file_name": name, "size": size, "sha256": digest.hexdigest(), "detection": det}

    # ---------------------------------------------------------------- preview / plans
    @app.post("/api/v1/previews", dependencies=auth)
    def start_preview(body: PreviewBody, who: str = Depends(actor)) -> dict[str, Any]:
        missing = [u for u in body.upload_ids if u not in uploads]
        if missing:
            raise HTTPException(404, f"Unknown upload(s): {missing}; upload the files again")
        inputs = [PreviewInput(path=uploads[u]["path"], source=body.source, period=body.period,
                               display_name=uploads[u]["file_name"]) for u in body.upload_ids]

        def run(job: Any) -> dict[str, Any]:
            eng = Engine(Settings.load(state_dir), state_dir)
            if body.strict:
                eng.settings.validation.mode = "strict"
            plan = eng.preview(inputs, who, jobs.progress(job), job.cancel_event.is_set)
            return plan_summary(plan)

        return jobs.start("preview", run).to_dict()

    @app.get("/api/v1/plans", dependencies=auth)
    def list_plans() -> list[dict[str, Any]]:
        out = []
        for plan_id in engine.store.list_plans()[:100]:
            try:
                out.append(plan_summary(load_plan(engine.store, plan_id)))
            except Exception:  # noqa: BLE001
                continue
        return out

    def _plan(plan_id: str):  # type: ignore[no-untyped-def]
        try:
            return load_plan(engine.store, plan_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/v1/plans/{plan_id}", dependencies=auth)
    def get_plan(plan_id: str) -> dict[str, Any]:
        plan = _plan(plan_id)
        data = json.loads(plan.model_dump_json(exclude={"records", "review_items"}))
        data["review_counts"] = {"total": len(plan.review_items), "undecided": len(plan.undecided)}
        data["record_count"] = len(plan.records)
        return data

    @app.get("/api/v1/plans/{plan_id}/records", dependencies=auth)
    def plan_records(plan_id: str, classification: str | None = None, source: str | None = None,
                     q: str | None = None, offset: int = 0, limit: int = Query(50, le=500)) -> dict[str, Any]:
        plan = _plan(plan_id)
        rows = plan.records
        if classification:
            rows = [r for r in rows if r.classification.value == classification]
        if source:
            rows = [r for r in rows if r.source == source]
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in (r.source_record_id or "").lower() or ql in r.source_file.lower()
                    or any(ql in c.lower() for c in r.match.candidate_ids)]
        page = rows[offset: offset + limit]
        return {"total": len(rows), "offset": offset, "limit": limit,
                "items": [json.loads(r.model_dump_json()) for r in page]}

    @app.get("/api/v1/plans/{plan_id}/review-items", dependencies=auth)
    def review_items(plan_id: str, status: str = "all", classification: str | None = None, q: str | None = None,
                     offset: int = 0, limit: int = Query(50, le=500)) -> dict[str, Any]:
        plan = _plan(plan_id)
        records = {r.rec_id: r for r in plan.records}
        items = plan.review_items
        if status == "undecided":
            items = [i for i in items if i.decision is None]
        elif status == "decided":
            items = [i for i in items if i.decision is not None]
        if classification:
            items = [i for i in items if i.classification.value == classification]
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i.title.lower() or ql in i.reason.lower()]
        page = items[offset: offset + limit]
        return {"total": len(items), "offset": offset, "limit": limit,
                "items": [{**json.loads(i.model_dump_json()),
                           "record": json.loads(records[i.rec_id].model_dump_json()) if i.rec_id in records else None}
                          for i in page]}

    @app.post("/api/v1/plans/{plan_id}/review-items/{item_id}/decision", dependencies=auth)
    def decide(plan_id: str, item_id: str, body: DecisionBody, who: str = Depends(actor)) -> dict[str, Any]:
        try:
            return engine.decide(plan_id, item_id, body.choice.upper(), body.reason, who, body.option_index)
        except EngineError as exc:
            raise fail(exc) from exc

    @app.post("/api/v1/plans/{plan_id}/bulk-decision", dependencies=auth)
    def bulk(plan_id: str, body: BulkBody, who: str = Depends(actor)) -> dict[str, Any]:
        return {"decided": engine.bulk_decide(plan_id, body.classification.upper(), body.choice.upper(), body.reason, who)}

    @app.post("/api/v1/plans/{plan_id}/approve", dependencies=auth)
    def approve(plan_id: str, body: ApproveBody, who: str = Depends(actor)) -> dict[str, Any]:
        try:
            return plan_summary(engine.approve(plan_id, who, body.note))
        except EngineError as exc:
            raise fail(exc) from exc

    @app.post("/api/v1/plans/{plan_id}/apply", dependencies=auth)
    def apply(plan_id: str, body: ApplyBody, who: str = Depends(actor)) -> dict[str, Any]:
        def run(job: Any) -> dict[str, Any]:
            return engine.apply(plan_id, who, authorize=body.authorize, defer_unresolved=not body.require_all_decided,
                                progress=jobs.progress(job), cancelled=job.cancel_event.is_set)

        try:
            return jobs.start("apply", run).to_dict()
        except RuntimeError as exc:
            raise fail(exc, 409) from exc

    @app.get("/api/v1/plans/{plan_id}/audit/{stage}/{name}", dependencies=auth)
    def audit_file(plan_id: str, stage: str, name: str) -> FileResponse:
        if stage not in ("preview", "apply") or name not in AUDIT_DOWNLOADS:
            raise HTTPException(404, "Unknown audit file")
        path = os.path.join(engine.store.p("runs", plan_id, stage), name)
        if not re.fullmatch(r"[A-Za-z0-9\-]+", plan_id) or not os.path.isfile(path):
            raise HTTPException(404, "Audit file not available")
        return FileResponse(path, filename=f"{plan_id}_{stage}_{name}")

    # ---------------------------------------------------------------- versions
    @app.get("/api/v1/versions", dependencies=auth)
    def versions() -> dict[str, Any]:
        return engine.history()

    @app.get("/api/v1/pending", dependencies=auth)
    def pending() -> list[dict[str, Any]]:
        return engine.store.pending()

    @app.post("/api/v1/versions/{number}/verify", dependencies=auth)
    def verify(number: int) -> dict[str, Any]:
        return jobs.start("verify", lambda j: engine.verify(number, progress=lambda f: jobs.progress(j)(
            "verify", f, "Verifying workbook"))).to_dict()

    @app.post("/api/v1/versions/{number}/rollback", dependencies=auth)
    def rollback(number: int, body: RollbackBody, who: str = Depends(actor)) -> dict[str, Any]:
        try:
            return engine.rollback(number, who, body.reason)
        except (EngineError, KeyError) as exc:
            raise fail(exc) from exc

    @app.get("/api/v1/versions/{number}/download", dependencies=auth)
    def download(number: int) -> FileResponse:
        try:
            record = engine.store.version(number)
        except KeyError as exc:
            raise HTTPException(404, "Version not found") from exc
        path = engine.store.version_path(record)
        if not os.path.isfile(path):
            raise HTTPException(410, "Version file was removed by the retention policy")
        return FileResponse(path, filename=record["file"],
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.post("/api/v1/publish", dependencies=auth)
    def publish(body: PublishBody, who: str = Depends(actor)) -> dict[str, Any]:
        try:
            return engine.publish(body.version, who, body.confirm)
        except (EngineError, KeyError) as exc:
            raise fail(exc) from exc

    # ---------------------------------------------------------------- warehouse explorer & quality
    def _index():  # type: ignore[no-untyped-def]
        """Index of the current version for read endpoints. Never builds inline: while it is missing
        or being built the answer is 503 (with progress), and a background job prepares it."""
        try:
            return engine.index(build=False)
        except IndexNotReady as exc:
            _prepare_index(exc)
            raise HTTPException(503, str(exc), headers={"Retry-After": "3"}) from exc
        except EngineError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/warehouse/schema", dependencies=auth)
    def schema() -> dict[str, Any]:
        idx = _index()
        try:
            return {"headers": idx.headers, "columns": idx.column_profiles(), "rows": idx.n_rows,
                    "periods": idx.periods(), "sheet": idx.sheet_name}
        finally:
            idx.close()

    @app.get("/api/v1/warehouse/rows", dependencies=auth)
    def rows(q: str | None = None, division: str | None = None, district: str | None = None,
             upazila: str | None = None, source: str | None = None, match_status: str | None = None,
             flag: str | None = None, gps: str | None = None, columns: str | None = None, offset: int = 0,
             limit: int = Query(50, le=500)) -> dict[str, Any]:
        idx = _index()
        try:
            filters = {"division": division, "district": district, "upazila": upazila, "source": source,
                       "match_status": match_status, "flag": flag, "gps": gps}
            return idx.query(q, filters, columns.split(",") if columns else None, offset, limit)
        finally:
            idx.close()

    @app.get("/api/v1/warehouse/rows/{row}", dependencies=auth)
    def row_detail(row: int) -> dict[str, Any]:
        idx = _index()
        try:
            data = idx.get_row(row)
            if data is None:
                raise HTTPException(404, "Row not found")
            return {"row": row, "values": data}
        finally:
            idx.close()

    @app.get("/api/v1/warehouse/facets", dependencies=auth)
    def facets() -> dict[str, Any]:
        idx = _index()
        try:
            return idx.facets()
        finally:
            idx.close()

    @app.get("/api/v1/quality", dependencies=auth)
    def quality() -> dict[str, Any]:
        idx = _index()
        try:
            return {"quality": idx.quality(), "columns": idx.column_profiles(), "rows": idx.n_rows}
        finally:
            idx.close()

    # ---------------------------------------------------------------- web app
    static = static_dir if static_dir is not None else default_static_dir()
    if static is not None and not (static / "index.html").exists():
        static = None  # engine runs; the page explains how to build the interface

    def index_html() -> HTMLResponse:
        if static is None:
            return HTMLResponse(_MISSING_UI.replace("{version}", __version__), status_code=200)
        html = (static / "index.html").read_text(encoding="utf-8")
        meta = f'<meta name="wash-sync-token" content="{session_token}"><meta name="wash-sync-mode" content="local">'
        response = HTMLResponse(html.replace("<head>", "<head>" + meta, 1), headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE_NAME, session_token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if static is not None and path:
            candidate = (static / path).resolve()
            if candidate.is_file() and static.resolve() in candidate.parents:
                return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"}
                                    if "/assets/" in f"/{path}" else None)
        return index_html()

    return app


_MISSING_UI = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>WASH Warehouse Sync</title>
<style>body{font:15px/1.6 system-ui,sans-serif;max-width:720px;margin:64px auto;padding:0 16px;color:#0f172a}
code{background:#f1f5f9;padding:2px 6px;border-radius:4px}</style></head><body>
<h1>WASH Warehouse Sync {version} — local engine is running</h1>
<p>The web interface has not been built yet. Build it once, then reload this page:</p>
<pre><code>npm --prefix apps/web ci
npm --prefix apps/web run build:local</code></pre>
<p>The command-line interface works without it: <code>wash-sync --help</code>.</p></body></html>"""


def run(host: str = "127.0.0.1", port: int = 8765, state_dir: str | None = None, open_browser: bool = True) -> None:
    import uvicorn  # noqa: PLC0415
    from wash_sync.config import default_state_dir  # noqa: PLC0415

    state = state_dir or default_state_dir()
    hosts = ["127.0.0.1", "localhost"] if host in ("127.0.0.1", "localhost") else ["127.0.0.1", "localhost", host]
    app = create_app(state, allowed_hosts=hosts)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/"
    print(f"WASH Warehouse Sync (local production mode) → {url}")
    print(f"Private state directory: {state}")
    if host not in ("127.0.0.1", "localhost"):
        print("WARNING: listening beyond this computer. Only do this on a trusted, private network.")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
