from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path
    visibility_timeout: float = 5.0   # SQS visibility timeout (seconds)
    max_receives: int = 3             # SQS redrive policy: maxReceiveCount
    max_upload_bytes: int = 20 * 1024 * 1024
    cache_ttl: float = 45.0           # Redis TTL for job status (seconds)
    poll_interval: float = 0.2        # worker polling interval when queue is empty
    processing_delay: float = 1.0     # simulated OCR time (seconds)
    upload_url_ttl: float = 900.0     # pre-signed URL lifetime (seconds)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            data_dir=Path(e("DOCFLOW_DATA", "./data")),
            visibility_timeout=float(e("DOCFLOW_VISIBILITY_TIMEOUT", "5")),
            max_receives=int(e("DOCFLOW_MAX_RECEIVES", "3")),
            max_upload_bytes=int(e("DOCFLOW_MAX_UPLOAD_MB", "20")) * 1024 * 1024,
            cache_ttl=float(e("DOCFLOW_CACHE_TTL", "45")),
            processing_delay=float(e("DOCFLOW_PROCESSING_DELAY", "1")),
        )
