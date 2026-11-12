from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DahuaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GI_DH_", env_file=".env", extra="ignore")

    backend_url: str = "http://127.0.0.1:8000"
    service_token: str

    listen_bind: str = "0.0.0.0"
    listen_port: int = 7000               # the port the customer types into the recorder's Register page
    viewer_user: str = "giview"           # the viewer-only user the customer creates on the recorder; its password is the recorder's key

    health_interval: float = 600.0        # how often each recorder is asked about its disks, clock and recordings
    heartbeat_interval: float = 30.0      # must be well under the backend's heartbeat_timeout_seconds (90)
    login_backoff_min: float = 30.0       # after a refused sign-in, ignore that recorder this long (a recorder locks the account after a few tries)
    login_backoff_max: float = 900.0
    login_timeout: float = 30.0
    max_pending_registrations: int = 200  # a flood of unknown recorders cannot pile up work

    # Video relay for the workers (same HTTP contract as the registrar's relay; see registrar/relay.py).
    relay_http_bind: str = "127.0.0.1"
    relay_http_port: int = 8082           # -1 turns it off
    relay_public_base: str = ""
    relay_idle_grace: float = 10.0
    relay_stall_seconds: float = 10.0
    relay_max_streams: int = 100
    relay_max_streams_per_device: int = 4
    relay_token_ttl: float = 60.0
    relay_subscriber_buffer: int = 8 * 1024 * 1024    # main streams are bigger than the sub streams the relay was sized for

    @field_validator("service_token")
    @classmethod
    def _long(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("must be at least 32 characters")
        return v
