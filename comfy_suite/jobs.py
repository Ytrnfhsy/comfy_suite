"""A job queue: generation requests run one at a time on a worker thread, and finished jobs
stay in a history the user can browse and apply from."""

from __future__ import annotations

import itertools
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .comfy import Cancelled
from .generator import Result

ProgressFn = Callable[[float, str], None]
Work = Callable[[ProgressFn, threading.Event], Result]


class JobState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: int
    kind: str
    title: str
    work: Work = field(repr=False)
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    status: str = ""
    result: Result | None = None
    error: str = ""
    created: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)


Listener = Callable[[Job], None]


class JobQueue:
    def __init__(self, history_size: int = 40):
        self.history_size = history_size
        self.jobs: list[Job] = []
        self._ids = itertools.count(1)
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="comfy-suite-jobs", daemon=True)
        self._thread.start()

    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _notify(self, job: Job) -> None:
        for fn in list(self._listeners):
            try:
                fn(job)
            except Exception:  # noqa: BLE001 - a broken listener must not stop the queue
                traceback.print_exc()

    def submit(self, kind: str, title: str, work: Work, **meta: Any) -> Job:
        job = Job(next(self._ids), kind, title, work, meta=meta)
        with self._lock:
            self.jobs.append(job)
            self._trim()
        self._queue.put(job)
        self._notify(job)
        return job

    def cancel(self, job: Job) -> None:
        job.cancel.set()
        if job.state == JobState.QUEUED:
            job.state = JobState.CANCELLED
            self._notify(job)

    def cancel_all(self) -> None:
        for job in list(self.jobs):
            if not job.finished:
                self.cancel(job)

    def remove(self, job: Job) -> None:
        with self._lock:
            if job in self.jobs and job.finished:
                self.jobs.remove(job)

    def pending(self) -> int:
        return sum(1 for j in self.jobs if not j.finished)

    def shutdown(self) -> None:
        self.cancel_all()
        self._queue.put(None)

    def _trim(self) -> None:
        finished = [j for j in self.jobs if j.finished]
        excess = len(finished) - self.history_size
        for j in finished[: max(0, excess)]:
            self.jobs.remove(j)

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            if job.cancel.is_set():
                job.state = JobState.CANCELLED
                self._notify(job)
                continue
            job.state = JobState.RUNNING
            self._notify(job)

            def progress(value: float, text: str, job: Job = job) -> None:
                job.progress, job.status = value, text
                self._notify(job)

            try:
                job.result = job.work(progress, job.cancel)
                job.state = JobState.DONE
                job.progress = 1.0
            except Cancelled:
                job.state = JobState.CANCELLED
            except Exception as e:  # noqa: BLE001 - reported to the user
                if job.cancel.is_set():
                    job.state = JobState.CANCELLED
                else:
                    job.state = JobState.FAILED
                    job.error = str(e) or type(e).__name__
                    traceback.print_exc()
            with self._lock:
                self._trim()
            self._notify(job)
