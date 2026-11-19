"""Background jobs with real (work-derived) progress and cooperative cancellation."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = "queued"  # queued | running | succeeded | failed | cancelled
    phase: str = ""
    fraction: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "kind": self.kind, "status": self.status, "phase": self.phase,
                "fraction": round(self.fraction, 4), "message": self.message, "result": self.result,
                "error": self.error, "created_at": self.created_at, "started_at": self.started_at,
                "finished_at": self.finished_at,
                "elapsed": round((self.finished_at or time.time()) - (self.started_at or self.created_at), 1)}


class JobManager:
    """Runs one job at a time per kind group; write operations are serialized."""

    WRITE_KINDS = {"init", "apply", "rollback"}

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def active_write(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.kind in self.WRITE_KINDS and j.status in ("queued", "running")), None)

    def start(self, kind: str, fn: Callable[[Job], Any]) -> Job:
        with self._lock:
            if kind in self.WRITE_KINDS and self.active_write():
                raise RuntimeError("Another write operation is already running")
            job = Job(job_id=uuid.uuid4().hex[:12], kind=kind)
            self.jobs[job.job_id] = job

        def runner() -> None:
            job.status, job.started_at = "running", time.time()
            try:
                job.result = fn(job)
                job.status = "cancelled" if job.cancel_event.is_set() and job.result is None else "succeeded"
                job.fraction = 1.0 if job.status == "succeeded" else job.fraction
            except Exception as exc:  # noqa: BLE001
                cancelled = job.cancel_event.is_set() or type(exc).__name__ in ("OperationCancelled", "PlanningCancelled")
                job.status = "cancelled" if cancelled else "failed"
                job.error = str(exc)
                if not cancelled:
                    job.message = "Failed"
                    traceback.print_exc()
            finally:
                job.finished_at = time.time()

        threading.Thread(target=runner, name=f"job-{kind}-{job.job_id}", daemon=True).start()
        return job

    def progress(self, job: Job) -> Callable[[str, float, str], None]:
        def update(phase: str, fraction: float, message: str) -> None:
            job.phase, job.fraction, job.message = phase, fraction, message

        return update

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)
