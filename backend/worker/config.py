from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GI_WORKER_", env_file=".env", extra="ignore")

    backend_url: str = "http://127.0.0.1:8000"
    relay_url: str = "http://127.0.0.1:8081"        # the registrar's video relay, for cameras whose recorder registered with us
    service_token: str = ""
    model_dir: Path = Path.home() / ".cache" / "gateinsight" / "models"
    detector_model: str = "yolox_tiny"      # yolox_nano (fastest), yolox_tiny, yolox_s (most accurate)
    threads: int = 2

    @field_validator("service_token")
    @classmethod
    def _token(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("must be at least 32 characters")
        return v
