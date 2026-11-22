"""High-level engine facade used by the CLI and the local API."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from typing import Any

from . import SCHEMA_VERSION, __version__
from .adapters.base import detect_source
from .adapters.tabular import estimate_rows
from .apply import ApplyError, apply_plan, load_plan, save_plan
from .config import Settings
from .index import WarehouseIndex, build_progress, file_sha256
from .plan import DecisionRecord, Plan
from .planner import Planner, PreviewInput
from .records import ChangeClass
from .schema import CORE_COLUMNS
from .state import StateStore, atomic_write_json, new_id, now_iso
from .xlsx.reader import iter_values
from .xlsx.verify import verify_workbook

ProgressFn = Callable[[str, float, str], None]


class EngineError(RuntimeError):
    pass


class IndexNotReady(EngineError):
    """The current version's lookup index is missing or still being built."""

    def __init__(self, version: int, progress: float | None) -> None:
        pct = f" ({round(progress * 100)}%)" if progress is not None else ""
        super().__init__(f"Preparing warehouse v{version:03d} for search{pct}. This takes a few minutes for a large "
                         "warehouse; the page updates automatically.")
        self.version = version
        self.progress = progress


def warehouse_headers(path: str) -> list[str] | None:
    """Header row of a workbook's first worksheet; None when the file is not a readable .xlsx workbook."""
    try:
        first = next(iter_values(path), None)
    except Exception:  # noqa: BLE001 - not a zip / not a workbook
        return None
    if first is None or first[0] != 1:
        return []
    return [str(v) for _, v in sorted(first[1].items())]


def is_warehouse_workbook(path: str) -> bool:
    """True for a workbook whose header has every core warehouse column (``candidate_id``, admin names, GPS…)."""
    headers = warehouse_headers(path)
    return bool(headers) and all(c in headers for c in CORE_COLUMNS)


class Engine:
    def __init__(self, settings: Settings | None = None, state_dir: str | None = None) -> None:
        self.settings = settings or Settings.load(state_dir)
        if state_dir:
            self.settings.state_dir = state_dir
        self.store = StateStore(self.settings.state_dir).ensure()

    # ------------------------------------------------------------------ setup
    def init(self, warehouse_path: str, actor: str = "local-user", force: bool = False,
             progress: ProgressFn | None = None) -> dict[str, Any]:
        """Register an existing verified workbook as the immutable baseline (v000)."""
        if self.store.versions() and not force:
            raise EngineError("A baseline is already registered. To make a newer warehouse file current, use "
                              "`wash-sync load-warehouse FILE` (or New Sync → Current warehouse).")
        if not os.path.isfile(warehouse_path):
            raise EngineError(f"Warehouse file not found: {warehouse_path}")
        return self._register(warehouse_path, os.path.basename(warehouse_path), actor, None, force, progress)

    def check_load(self, path: str, file_name: str | None = None, sha256: str | None = None) -> dict[str, Any] | None:
        """Fast checks before a warehouse file is loaded (header row and checksum only).

        Returns the current version when the file already *is* the current version, so there is
        nothing to do. Raises EngineError for files that must not be loaded: unreadable files,
        workbooks that are not the WASH warehouse (for example a source export chosen by mistake)
        and copies of an earlier version, which belong to rollback instead.
        """
        name = file_name or os.path.basename(path)
        if not os.path.isfile(path):
            raise EngineError(f"Warehouse file not found: {path}")
        headers = warehouse_headers(path)
        if headers is None:
            raise EngineError(f"{name} could not be read as an Excel .xlsx workbook")
        missing = [c for c in CORE_COLUMNS if c not in headers]
        if missing:
            try:
                det = detect_source(path)
            except Exception:  # noqa: BLE001
                det = None
            kind = {"AQUAWATCH": "an AquaWatch", "GPANI": "a GPANI", "WPMS": "a WPMS", "BORELOG": "a bore-log"}
            hint = (f" It looks like {kind.get(det.source, 'a ' + det.source)} source export — add it under "
                    "“Files to sync” instead." if det is not None and det.source else "")
            shown = ", ".join(missing[:4]) + (", …" if len(missing) > 4 else "")
            raise EngineError(f"{name} is not the WASH warehouse: it has no {shown} column.{hint}")
        sha = sha256 or file_sha256(path)
        current = self.store.current_version()
        if current is not None and current["sha256"] == sha:
            return current
        earlier = next((v for v in self.store.versions() if v["sha256"] == sha), None)
        if earlier is not None:
            label = f"v{earlier['version']:03d}"
            raise EngineError(f"{name} is identical to {label}, an earlier version. To use it again, open Sync History "
                              f"and choose “Make current” on {label}.")
        return None

    def load_warehouse(self, path: str, actor: str = "local-user", reason: str | None = None,
                       file_name: str | None = None, sha256: str | None = None,
                       progress: ProgressFn | None = None) -> dict[str, Any]:
        """Make a warehouse workbook the current version.

        Without a registered warehouse the file becomes the baseline (v000). Otherwise — typically
        the warehouse after edits made outside this tool — it is verified and recorded as the next
        immutable version (kind ``LOADED``); every earlier version stays available for rollback.
        A file that already is the current version changes nothing (``unchanged`` is True).
        """
        name = file_name or os.path.basename(path)
        current = self.check_load(path, name, sha256)
        if current is not None:
            return {**current, "unchanged": True}
        return self._register(path, name, actor, (reason or "").strip() or None, False, progress)

    def _register(self, warehouse_path: str, name: str, actor: str, reason: str | None, force: bool,
                  progress: ProgressFn | None) -> dict[str, Any]:
        """Verify, copy and index a workbook, then make it the current version.

        The version becomes current only after its lookup index is complete, so every page that
        reads the current version sees either the previous version or the fully prepared new one.
        """

        def p(phase: str, f: float, msg: str) -> None:
            if progress:
                progress(phase, f, msg)

        p("verify", 0.0, f"Verifying {name}")
        report = verify_workbook(warehouse_path, progress=lambda f: p("verify", f, f"Verifying {name}"), external=True)
        if not report.ok and not force:
            failed = "; ".join(f"{c.name}: {c.detail}" for c in report.checks if c.status == "FAIL")
            raise EngineError(f"{name} failed verification: {failed}")
        with self.store.lock("init"):
            previous = self.store.current_version()
            number = 0 if not self.store.versions() else self.store.next_version_number()
            kind = "BASELINE" if number == 0 else "LOADED"
            file_name = self.store.version_file_name(number)
            target = self.store.p("versions", file_name)
            tmp = target + ".partial"
            p("copy", 0.0, f"Copying into private state as {file_name}")
            shutil.copyfile(warehouse_path, tmp)
            sha_src, sha_copy = file_sha256(warehouse_path), file_sha256(tmp)
            if sha_src != sha_copy:
                os.remove(tmp)
                raise EngineError("Copy verification failed (SHA-256 mismatch)")
            os.replace(tmp, target)
            os.chmod(target, 0o444)
            p("index", 0.0, "Preparing the warehouse for search")
            try:
                WarehouseIndex.open_or_build(target, self.store.p("index"), sha_copy,
                                             progress=lambda f: p("index", f, "Preparing the warehouse for search")).close()
            except BaseException:
                os.chmod(target, 0o644)
                os.remove(target)  # never leave an unrecorded version file behind
                raise
            record = {
                "version": number, "file": file_name, "sha256": sha_copy, "size": os.path.getsize(target),
                "created_at": now_iso(), "previous_version": previous["version"] if previous else None, "kind": kind,
                "imported_from": name, "reason": reason, "schema_version": SCHEMA_VERSION,
                "engine_version": __version__, "rows": report.stats.get("data_rows"),
                "columns": report.stats.get("max_column"), "sources": [], "counts": {},
                "validation": {"ok": report.ok, "checks": [c.__dict__ for c in report.checks]},
                "applied_by": actor,
            }
            self.store.record_version(record)
            if kind == "BASELINE":
                self.store.history_append({"event": "INIT", "version": number, "by": actor, "imported_from": name})
            else:
                self.store.history_append({"event": "LOAD_WAREHOUSE", "version": number,
                                           "previous_version": record["previous_version"], "by": actor,
                                           "imported_from": name, "reason": reason})
        p("done", 1.0, "Baseline registered" if kind == "BASELINE" else f"{name} is now the current warehouse ({file_name})")
        return record

    # ------------------------------------------------------------------ queries
    def require_current(self) -> dict[str, Any]:
        current = self.store.current_version()
        if current is None:
            raise EngineError("No warehouse registered. Run `wash-sync init --warehouse <file.xlsx>` first.")
        return current

    def index(self, version: int | None = None, progress: Callable[[float], None] | None = None,
              build: bool = True) -> WarehouseIndex:
        """Lookup index of a version (default: current). With ``build=False`` it never builds: a missing
        or unfinished index raises IndexNotReady (with the progress of a build running in this process)."""
        record = self.store.version(version) if version is not None else self.require_current()
        if not build:
            index = WarehouseIndex.try_open(self.store.p("index"), record["sha256"])
            if index is None:
                raise IndexNotReady(record["version"], build_progress(self.store.p("index"), record["sha256"]))
            return index
        return WarehouseIndex.open_or_build(self.store.version_path(record), self.store.p("index"), record["sha256"],
                                            progress=progress)

    def status(self) -> dict[str, Any]:
        current = self.store.current_version()
        versions = self.store.versions()
        history = self.store.history(limit=30)
        plans = []
        for plan_id in self.store.list_plans()[:12]:
            try:
                plan = load_plan(self.store, plan_id)
            except Exception:  # noqa: BLE001
                continue
            plans.append(plan_summary(plan))
        last_sync = next((v for v in reversed(versions) if v.get("kind") == "SYNC"), None)
        return {
            "mode": "local", "engine_version": __version__, "schema_version": SCHEMA_VERSION,
            "initialized": current is not None, "current_version": current, "versions": len(versions),
            "last_sync": last_sync, "recent_plans": plans, "recent_events": history[::-1],
            "pending_conflicts": len(self.store.pending()),
            "state_dir_configured": True,
        }

    def inspect(self, paths: list[str], source: str | None = None) -> list[dict[str, Any]]:
        out = []
        for path in paths:
            det = detect_source(path, source)
            try:
                rows = estimate_rows(path, det.sheet)
            except Exception:  # noqa: BLE001
                rows = None
            out.append({"file": os.path.basename(path), "size": os.path.getsize(path), "sha256": file_sha256(path),
                        "estimated_rows": rows, **det.to_dict()})
        return out

    # ------------------------------------------------------------------ preview / review
    def preview(self, inputs: list[PreviewInput], actor: str = "local-user", progress: ProgressFn | None = None,
                cancelled: Callable[[], bool] | None = None) -> Plan:
        self.require_current()
        return Planner(self.settings, self.store, progress, cancelled).run(inputs, actor)

    def plan(self, plan_id: str) -> Plan:
        return load_plan(self.store, plan_id)

    def decide(self, plan_id: str, item_id: str, choice: str, reason: str, actor: str,
               option_index: int | None = None) -> dict[str, Any]:
        plan = load_plan(self.store, plan_id)
        result = self._decide_in(plan, item_id, choice, reason, actor, option_index)
        save_plan(self.store, plan)
        self.store.history_append(result.pop("_event"))
        return result

    def _decide_in(self, plan: Plan, item_id: str, choice: str, reason: str, actor: str,
                   option_index: int | None) -> dict[str, Any]:
        if not reason or not reason.strip():
            raise EngineError("A reason is required for every manual decision")
        if plan.status in ("APPLIED", "SUPERSEDED"):
            raise EngineError(f"Plan {plan.plan_id} is {plan.status}; decisions can no longer change")
        item = plan.review_index().get(item_id)
        if item is None:
            raise EngineError(f"Review item {item_id} not found in plan {plan.plan_id}")
        candidates = [i for i, o in enumerate(item.options) if o.decision == choice]
        if option_index is not None:
            if option_index not in candidates:
                raise EngineError(f"Option {option_index} is not a {choice} option for {item_id}")
            idx = option_index
        elif len(candidates) == 1:
            idx = candidates[0]
        elif not candidates:
            raise EngineError(f"{choice} is not available for {item_id}; options: {[o.decision for o in item.options]}")
        else:
            raise EngineError(f"{choice} is ambiguous for {item_id}; specify the option index ({candidates})")
        item.decision = DecisionRecord(choice=choice, option_index=idx, reason=reason.strip(), decided_by=actor,
                                       decided_at=now_iso())
        if plan.status == "APPROVED":
            plan.status = "DRAFT"
            plan.approval = None
        return {"item_id": item_id, "choice": choice, "option_index": idx,
                "_event": {"event": "DECISION", "plan_id": plan.plan_id, "item_id": item_id, "choice": choice,
                           "option": idx, "by": actor, "reason": reason.strip()}}

    def bulk_decide(self, plan_id: str, classification: str, choice: str, reason: str, actor: str) -> int:
        """Decide every undecided item of a class in one pass (one load, one save)."""
        plan = load_plan(self.store, plan_id)
        events = []
        for item in plan.review_items:
            if item.decision is None and item.classification.value == classification:
                options = [i for i, o in enumerate(item.options) if o.decision == choice]
                if len(options) == 1:
                    events.append(self._decide_in(plan, item.item_id, choice, reason, actor, options[0])["_event"])
        if events:
            save_plan(self.store, plan)
            for event in events:
                self.store.history_append(event)
        return len(events)

    def approve(self, plan_id: str, actor: str, note: str | None = None) -> Plan:
        plan = load_plan(self.store, plan_id)
        if plan.status in ("APPLIED", "SUPERSEDED"):
            raise EngineError(f"Plan {plan_id} is {plan.status}")
        current = self.require_current()
        if current["sha256"] != plan.base_sha256:
            raise EngineError("The plan's base version is no longer current; run a new preview")
        from .plan import Approval  # noqa: PLC0415

        plan.status = "APPROVED"
        plan.approval = Approval(approved_by=actor, approved_at=now_iso(), plan_hash=plan.content_hash(), note=note)
        save_plan(self.store, plan)
        self.store.history_append({"event": "APPROVE", "plan_id": plan_id, "by": actor,
                                   "undecided_items_deferred": len(plan.undecided), "note": note})
        return plan

    def apply(self, plan_id: str, actor: str = "local-user", authorize: bool = False, defer_unresolved: bool = True,
              force_export: bool = False, progress: ProgressFn | None = None,
              cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        try:
            return apply_plan(self.settings, self.store, plan_id, actor, authorize, defer_unresolved, force_export,
                              progress, cancelled)
        except ApplyError as exc:
            raise EngineError(str(exc)) from exc

    # ------------------------------------------------------------------ versions
    def verify(self, version: int | None = None, path: str | None = None, deep: bool = True,
               progress: Callable[[float], None] | None = None) -> dict[str, Any]:
        record = None
        if path is None:
            record = self.store.version(version) if version is not None else self.require_current()
            path = self.store.version_path(record)
        # Registered baselines and loaded files come from outside the engine; synced versions are its own output.
        external = record is None or record.get("kind") != "SYNC"
        report = verify_workbook(path, progress=progress, deep=deep, external=external)
        out = report.to_dict()
        out["path"] = os.path.basename(path)
        if record is not None:
            actual = file_sha256(path)
            out["sha256_matches_record"] = actual == record["sha256"]
            if actual != record["sha256"]:
                out["ok"] = False
                out["checks"].append({"name": "recorded_sha256", "status": "FAIL",
                                      "detail": "file content differs from the version record"})
        return out

    def history(self) -> dict[str, Any]:
        current = self.store.current_version()
        return {"current_version": current["version"] if current else None, "versions": self.store.versions(),
                "events": self.store.history()}

    def rollback(self, to_version: int, actor: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise EngineError("A reason is required for rollback")
        with self.store.lock("rollback"):
            target = self.store.version(to_version)
            path = self.store.version_path(target)
            if not os.path.exists(path):
                raise EngineError(f"Version file {target['file']} is missing (removed by retention?)")
            if file_sha256(path) != target["sha256"]:
                raise EngineError(f"Version file {target['file']} does not match its recorded SHA-256")
            current = self.require_current()
            manifest = self.store.manifest()
            manifest["current_version"] = to_version
            manifest["updated_at"] = now_iso()
            self.store.save_manifest(manifest)
            self.store.history_append({"event": "ROLLBACK", "from_version": current["version"], "to_version": to_version,
                                       "by": actor, "reason": reason})
        return {"current_version": to_version, "previous_current": current["version"],
                "note": "Versions are immutable; later versions are kept and can be restored again."}

    def publish(self, version: int | None, actor: str, confirm: bool) -> dict[str, Any]:
        ps = self.settings.private_storage
        if not ps.enabled or not ps.destination:
            raise EngineError("Private storage is not configured. Set private_storage.enabled and destination in settings.")
        if not confirm:
            raise EngineError("Publishing copies the private warehouse to another location; pass explicit confirmation")
        record = self.store.version(version) if version is not None else self.require_current()
        dest_dir = os.path.abspath(ps.destination)
        if not os.path.isdir(dest_dir):
            raise EngineError(f"Destination folder does not exist: {dest_dir}")
        src = self.store.version_path(record)
        dest = os.path.join(dest_dir, record["file"])
        tmp = dest + ".partial"
        shutil.copyfile(src, tmp)
        if file_sha256(tmp) != record["sha256"]:
            os.remove(tmp)
            raise EngineError("Copy verification failed (SHA-256 mismatch); nothing was published")
        os.replace(tmp, dest)
        atomic_write_json(dest + ".manifest.json", {k: record.get(k) for k in (
            "version", "file", "sha256", "created_at", "previous_version", "rows", "columns", "counts",
            "schema_version", "engine_version")})
        self.store.history_append({"event": "PUBLISH", "version": record["version"], "destination": ps.label or "private storage",
                                   "by": actor})
        return {"published": record["file"], "destination": ps.label or dest_dir, "sha256": record["sha256"]}

    def recover(self) -> dict[str, Any]:
        """Clean up after interrupted runs. The verified versions are never touched."""
        cleaned, interrupted = [], []
        jobs_dir = self.store.p("jobs")
        for name in sorted(os.listdir(jobs_dir)) if os.path.isdir(jobs_dir) else []:
            path = os.path.join(jobs_dir, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    job = json.load(fh)
            except (OSError, ValueError):
                continue
            if job.get("status") in ("writing", "verifying"):
                from .state import _pid_alive  # noqa: PLC0415

                if _pid_alive(int(job.get("pid", 0))) and int(job.get("pid", 0)) != os.getpid():
                    continue
                tmp = job.get("tmp")
                if tmp and os.path.exists(os.path.dirname(tmp)):
                    shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
                    cleaned.append(os.path.basename(tmp))
                job["status"] = "interrupted"
                job["recovered_at"] = now_iso()
                atomic_write_json(path, job)
                interrupted.append(job.get("plan_id"))
        stale_lock = self.store.p("sync.lock")
        if os.path.exists(stale_lock):
            try:
                with open(stale_lock, encoding="utf-8") as fh:
                    pid = int(json.load(fh).get("pid", 0))
            except (OSError, ValueError):
                pid = 0
            from .state import _pid_alive  # noqa: PLC0415

            if not _pid_alive(pid):
                os.remove(stale_lock)
                cleaned.append("sync.lock")
        if interrupted:
            self.store.history_append({"event": "RECOVER", "interrupted_plans": interrupted, "cleaned": cleaned})
        return {"interrupted_plans": interrupted, "cleaned": cleaned,
                "message": "The current verified version was not modified. Re-run `apply` for interrupted plans."}

    def new_upload_path(self, filename: str) -> str:
        safe = "".join(ch for ch in os.path.basename(filename) if ch.isalnum() or ch in "._- ()").strip() or "upload"
        folder = self.store.p("uploads", new_id("up"))
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, safe)


def plan_summary(plan: Plan) -> dict[str, Any]:
    return {"plan_id": plan.plan_id, "created_at": plan.created_at, "status": plan.status,
            "base_version": plan.base_version, "applied_version": plan.applied_version,
            "files": [s.file_name for s in plan.sources], "summary": plan.summary.get("by_classification", {}),
            "review_items": len(plan.review_items), "undecided": len(plan.undecided),
            "new_columns": [c.name for c in plan.new_columns], "approval": plan.approval.model_dump() if plan.approval else None}


__all__ = ["Engine", "EngineError", "IndexNotReady", "PreviewInput", "plan_summary", "ChangeClass", "is_warehouse_workbook"]
