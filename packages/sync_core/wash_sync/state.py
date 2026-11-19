"""Private synchronization state: manifest, versions, fingerprints, crosswalk, history.

Layout (all under ``Settings.state_dir``; never published, git-ignored)::

    manifest.json              current version pointer + schema/engine info
    warehouse_versions.json    immutable list of published versions
    source_fingerprints.jsonl  one line per observation key (latest sync status)
    facility_crosswalk.csv     verified / reviewed cross-source links and separations
    change_history.jsonl       append-only event log (apply, decisions, rollback, ...)
    pending_conflicts.json     deferred review items carried between runs
    versions/                  WASH_Warehouse_vNNN.xlsx (read-only files)
    plans/  runs/  index/  jobs/  uploads/  tmp/
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from . import SCHEMA_VERSION, __version__

CROSSWALK_FIELDS = ["source", "source_record_id", "candidate_id", "link_type", "evidence", "decided_by",
                    "decided_at", "plan_id", "reason"]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def atomic_write_text(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def atomic_write_json(path: str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


class StateLockedError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class StateStore:
    root: str

    # ------------------------------------------------------------- layout
    def p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def ensure(self) -> "StateStore":
        for sub in ("versions", "plans", "runs", "index", "jobs", "uploads", "tmp"):
            os.makedirs(self.p(sub), exist_ok=True)
        if not os.path.exists(self.p("manifest.json")):
            atomic_write_json(self.p("manifest.json"), {
                "schema_version": SCHEMA_VERSION, "engine_version": __version__, "created_at": now_iso(),
                "current_version": None, "warehouse_name": "WASH_Warehouse",
            })
        if not os.path.exists(self.p("warehouse_versions.json")):
            atomic_write_json(self.p("warehouse_versions.json"), [])
        if not os.path.exists(self.p("pending_conflicts.json")):
            atomic_write_json(self.p("pending_conflicts.json"), [])
        if not os.path.exists(self.p("facility_crosswalk.csv")):
            atomic_write_text(self.p("facility_crosswalk.csv"), ",".join(CROSSWALK_FIELDS) + "\n")
        for name in ("source_fingerprints.jsonl", "change_history.jsonl"):
            if not os.path.exists(self.p(name)):
                atomic_write_text(self.p(name), "")
        return self

    @property
    def initialized(self) -> bool:
        return self.current_version() is not None

    # ------------------------------------------------------------- lock
    @contextmanager
    def lock(self, purpose: str) -> Iterator[None]:
        path = self.p("sync.lock")
        os.makedirs(self.root, exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as fh:
                    json.dump({"pid": os.getpid(), "purpose": purpose, "since": now_iso()}, fh)
                break
            except FileExistsError:
                try:
                    with open(path, encoding="utf-8") as fh:
                        info = json.load(fh)
                except (OSError, ValueError):
                    info = {}
                if not _pid_alive(int(info.get("pid", 0))):
                    os.remove(path)  # stale lock from a crashed process
                    continue
                raise StateLockedError(
                    f"Another operation is running ({info.get('purpose')} since {info.get('since')}, pid {info.get('pid')})"
                ) from None
        try:
            yield
        finally:
            if os.path.exists(path):
                os.remove(path)

    # ------------------------------------------------------------- manifest / versions
    def manifest(self) -> dict[str, Any]:
        with open(self.p("manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def save_manifest(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.p("manifest.json"), data)

    def versions(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.p("warehouse_versions.json")):
            return []
        with open(self.p("warehouse_versions.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def version(self, number: int) -> dict[str, Any]:
        for v in self.versions():
            if v["version"] == number:
                return v
        raise KeyError(f"Version {number} not found")

    def current_version(self) -> dict[str, Any] | None:
        if not os.path.exists(self.p("manifest.json")):
            return None
        current = self.manifest().get("current_version")
        if current is None:
            return None
        return self.version(int(current))

    def version_path(self, record: dict[str, Any]) -> str:
        return self.p("versions", record["file"])

    def next_version_number(self) -> int:
        versions = self.versions()
        return (max(v["version"] for v in versions) + 1) if versions else 0

    def version_file_name(self, number: int) -> str:
        name = self.manifest().get("warehouse_name", "WASH_Warehouse")
        return f"{name}_v{number:03d}.xlsx"

    def record_version(self, record: dict[str, Any], make_current: bool = True) -> None:
        versions = self.versions()
        if any(v["version"] == record["version"] for v in versions):
            raise ValueError(f"Version {record['version']} already exists")
        versions.append(record)
        atomic_write_json(self.p("warehouse_versions.json"), versions)
        if make_current:
            manifest = self.manifest()
            manifest["current_version"] = record["version"]
            manifest["updated_at"] = now_iso()
            self.save_manifest(manifest)

    def lineage(self, number: int | None = None) -> list[int]:
        """Version numbers from ``number`` (default: current) back to the baseline."""
        by_num = {v["version"]: v for v in self.versions()}
        if number is None:
            cur = self.current_version()
            number = cur["version"] if cur else None
        chain = []
        while number is not None and number in by_num:
            chain.append(number)
            number = by_num[number].get("previous_version")
        return chain

    # ------------------------------------------------------------- history
    def history_append(self, event: dict[str, Any]) -> None:
        event = {"at": now_iso(), **event}
        with open(self.p("change_history.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def history(self, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.p("change_history.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        return events[-limit:] if limit else events

    # ------------------------------------------------------------- fingerprints ledger
    def ledger(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        path = self.p("source_fingerprints.jsonl")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        entry = json.loads(line)
                        out[entry.get("k") or entry["key"]] = entry
        return out

    def save_ledger(self, entries: dict[str, dict[str, Any]]) -> None:
        lines = [json.dumps(entries[k], ensure_ascii=False, sort_keys=True) for k in sorted(entries)]
        atomic_write_text(self.p("source_fingerprints.jsonl"), "\n".join(lines) + ("\n" if lines else ""))

    # ------------------------------------------------------------- crosswalk
    def crosswalk(self) -> list[dict[str, str]]:
        path = self.p("facility_crosswalk.csv")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    def add_crosswalk(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        existing = self.crosswalk()
        keys = {(r["source"], r["source_record_id"], r["candidate_id"], r["link_type"]) for r in existing}
        merged = existing + [
            {k: str(r.get(k, "") or "") for k in CROSSWALK_FIELDS} for r in rows
            if (r["source"], r["source_record_id"], r["candidate_id"], r["link_type"]) not in keys
        ]
        import io  # noqa: PLC0415

        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CROSSWALK_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(merged)
        atomic_write_text(self.p("facility_crosswalk.csv"), out.getvalue())

    # ------------------------------------------------------------- pending conflicts
    def pending(self) -> list[dict[str, Any]]:
        path = self.p("pending_conflicts.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def save_pending(self, items: list[dict[str, Any]]) -> None:
        atomic_write_json(self.p("pending_conflicts.json"), items)

    # ------------------------------------------------------------- plans
    def plan_path(self, plan_id: str) -> str:
        if not plan_id.replace("-", "").isalnum():
            raise ValueError("invalid plan id")
        return self.p("plans", f"{plan_id}.json")

    def run_dir(self, plan_id: str, stage: str) -> str:
        if not plan_id.replace("-", "").isalnum() or stage not in ("preview", "apply"):
            raise ValueError("invalid run reference")
        path = self.p("runs", plan_id, stage)
        os.makedirs(path, exist_ok=True)
        return path

    def list_plans(self) -> list[str]:
        if not os.path.isdir(self.p("plans")):
            return []
        names = [n[:-5] for n in os.listdir(self.p("plans")) if n.endswith(".json")]
        return sorted(names, key=lambda n: os.path.getmtime(self.p("plans", n + ".json")), reverse=True)

    # ------------------------------------------------------------- housekeeping
    def apply_retention(self, keep: int) -> list[str]:
        """Delete old non-current versions beyond ``keep`` (baseline v000 is kept)."""
        if keep <= 0:
            return []
        versions = sorted(self.versions(), key=lambda v: v["version"])
        current = self.current_version()
        protected = {0, current["version"] if current else -1}
        candidates = [v for v in versions if v["version"] not in protected]
        removed = []
        for v in candidates[:-keep] if len(candidates) > keep else []:
            path = self.version_path(v)
            if os.path.exists(path):
                os.chmod(path, 0o644)
                os.remove(path)
                removed.append(v["file"])
        return removed

    def cleanup_tmp(self) -> None:
        tmp = self.p("tmp")
        if os.path.isdir(tmp):
            for name in os.listdir(tmp):
                full = os.path.join(tmp, name)
                shutil.rmtree(full, ignore_errors=True) if os.path.isdir(full) else os.remove(full)
