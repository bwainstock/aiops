import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    worker_poll_seconds: float
    job_lease_seconds: float
    worker_processing_delay_seconds: float
    job_max_attempts: int

    @classmethod
    def from_environment(cls) -> "Settings":
        settings = cls(
            database_path=Path(os.environ.get("AIOPS_DATABASE_PATH", "data/aiops.db")),
            worker_poll_seconds=float(os.environ.get("AIOPS_WORKER_POLL_SECONDS", "0.5")),
            job_lease_seconds=float(os.environ.get("AIOPS_JOB_LEASE_SECONDS", "5")),
            worker_processing_delay_seconds=float(
                os.environ.get("AIOPS_WORKER_PROCESSING_DELAY_SECONDS", "0")
            ),
            job_max_attempts=int(os.environ.get("AIOPS_JOB_MAX_ATTEMPTS", "3")),
        )
        if settings.worker_poll_seconds <= 0:
            raise ValueError("AIOPS_WORKER_POLL_SECONDS must be positive")
        if settings.job_lease_seconds <= 0:
            raise ValueError("AIOPS_JOB_LEASE_SECONDS must be positive")
        if settings.worker_processing_delay_seconds < 0:
            raise ValueError("AIOPS_WORKER_PROCESSING_DELAY_SECONDS must not be negative")
        if settings.job_max_attempts < 1:
            raise ValueError("AIOPS_JOB_MAX_ATTEMPTS must be at least 1")
        return settings
