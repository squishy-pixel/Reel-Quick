"""Background jobs with progress and cancellation, for the app's API."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

ProgressCb = Callable[[float, dict], None]


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = "queued"          # queued | running | done | failed | cancelled
    progress: float = 0.0
    detail: dict = field(default_factory=dict)
    error: str | None = None
    result: Any = None
    started: float | None = None
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def eta_seconds(self) -> float | None:
        if self.status != "running" or not self.started or self.progress <= 0.01:
            return None
        elapsed = time.time() - self.started
        return elapsed / self.progress - elapsed

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
            "progress": round(self.progress, 4), "detail": self.detail, "error": self.error,
            "started": self.started, "finished": self.finished,
            "elapsed": (self.finished or time.time()) - self.started if self.started else 0,
            "eta": self.eta_seconds,
            "result": self.result if isinstance(self.result, (str, int, float, dict, list, type(None))) else str(self.result),
        }


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, label: str, fn: Callable[[ProgressCb, threading.Event], Any],
              exclusive: bool = True) -> Job:
        """Run fn(progress, cancel) on a thread. exclusive=True refuses a second running job of the same kind."""
        with self._lock:
            if exclusive and any(j.kind == kind and j.status == "running" for j in self._jobs.values()):
                raise RuntimeError(f"a {kind} job is already running")
            job = Job(id=uuid.uuid4().hex[:8], kind=kind, label=label)
            self._jobs[job.id] = job

        def progress(frac: float, detail: dict | None = None) -> None:
            job.progress = max(job.progress, min(1.0, float(frac))) if frac >= job.progress else float(frac)
            if detail:
                job.detail.update(detail)

        def run() -> None:
            job.status = "running"
            job.started = time.time()
            try:
                job.result = fn(progress, job.cancel)
                job.status = "cancelled" if job.cancel.is_set() else "done"
                if job.status == "done":
                    job.progress = 1.0
            except Exception as e:      # noqa: BLE001 - surfaced to the UI
                job.status = "cancelled" if job.cancel.is_set() else "failed"
                job.error = str(e) if job.status == "failed" else None
                if job.status == "failed":
                    job.detail["traceback"] = traceback.format_exc()[-2000:]
            finally:
                job.finished = time.time()

        threading.Thread(target=run, name=f"job-{kind}-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.cancel.set()
        return True

    def cancel_all(self) -> None:
        for j in self._jobs.values():
            if j.status in ("queued", "running"):
                j.cancel.set()

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.started or 0)

    def running(self, kind: str | None = None) -> list[Job]:
        return [j for j in self._jobs.values() if j.status == "running" and (kind is None or j.kind == kind)]
