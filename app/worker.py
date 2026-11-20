from __future__ import annotations
"""Processor worker. Stands in for the processor Lambda triggered by SQS."""
import logging
import threading
import time
from pathlib import Path

from .extract import ValidationError, extract

log = logging.getLogger("docflow.worker")


class Processor(threading.Thread):
    def __init__(self, svc):
        super().__init__(daemon=True, name="docflow-processor")
        self.svc = svc
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        while not self._halt.is_set():
            try:
                msg = self.svc.queue.receive(on_dead=self._on_dead)
                if msg is None:
                    self._halt.wait(self.svc.settings.poll_interval)
                    continue
                self.handle(msg)
            except Exception:
                log.exception("worker loop error")
                self._halt.wait(1)

    def handle(self, msg):
        svc = self.svc
        job_id = msg.body["job_id"]
        row = svc.jobs.row(job_id)
        if row is None or row["status"] == "DONE":
            svc.queue.delete(msg.id)  # duplicate delivery: processing is idempotent
            return
        svc.jobs.set_status(job_id, "PROCESSING")
        try:
            time.sleep(svc.settings.processing_delay)  # simulated OCR time
            result = extract(Path(row["file_path"]))
        except ValidationError as e:
            svc.jobs.fail(job_id, str(e))
            svc.queue.delete(msg.id)
            self._publish(job_id, "FAILED", str(e))
        except Exception as e:
            log.warning("job %s attempt %s failed: %s", job_id, msg.receive_count, e)
            svc.jobs.set_status(job_id, "RETRYING", f"attempt {msg.receive_count} failed: {e}")
            # message is not deleted: it becomes visible again after the timeout
        else:
            svc.jobs.complete(job_id, result)
            svc.queue.delete(msg.id)
            self._publish(job_id, "DONE")

    def _on_dead(self, body: dict):
        job_id = body["job_id"]
        msg = f"gave up after {self.svc.settings.max_receives} attempts; moved to the dead-letter queue"
        self.svc.jobs.fail(job_id, msg)
        self._publish(job_id, "FAILED", msg)

    def _publish(self, job_id: str, status: str, error: str | None = None):
        row = self.svc.jobs.row(job_id)
        event = {"job_id": job_id, "user": row["user"], "filename": row["filename"], "status": status}
        if error:
            event["error"] = error
        self.svc.bus.publish("JobCompleted" if status == "DONE" else "JobFailed", event)
