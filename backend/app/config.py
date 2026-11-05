import base64
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GI_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str
    db_pool_min: int = 2
    db_pool_max: int = 10

    # One secret per token audience, so a customer token can never be accepted by the admin API.
    jwt_secret_customer: str
    jwt_secret_platform: str
    service_token: str                      # registrar and AI workers (also protect with mTLS in production)
    key_encryption_key: str                 # base64 of 32 bytes. Replace with KMS in production.

    registrar_host: str = "connect.example.com"
    isup_port: int = 7660
    auto_register_port: int = 7000
    # GB28181 (SIP) settings handed to customers. The registrar must be configured with the same realm and server id.
    gb_realm: str = "3402000000"            # 10 digits: the SIP domain
    gb_server_id: str = "34020000002000000001"   # 20 digits: this server's SIP id
    gb_port: int = 5060
    dahua_viewer_user: str = "giview"      # the viewer-only user a customer creates on a Dahua recorder (the gateway signs in as this user)
    heartbeat_timeout_seconds: int = 90
    # Sign-in
    access_token_minutes: int = 15
    customer_refresh_days: int = 30
    staff_refresh_hours: int = 12
    invite_days: int = 7
    reset_hours: int = 1
    lockout_failures: int = 5                # wrong passwords before an account is locked
    lockout_minutes: int = 15
    login_ip_limit: int = 30                 # sign-in attempts per address per 5 minutes
    sweep_interval_seconds: int = 30
    cors_origins: list[str] = []

    # Alerts: messaging and evidence pictures.
    public_base_url: str = "http://localhost:8000"      # used to build the evidence links sent to people
    messaging_allow_private_hosts: bool = False         # tests only: lets an integration call a local server
    delivery_poll_seconds: float = 2.0
    rtsp_probe_seconds: float = 20.0                     # how long to wait for a camera to answer a check
    ffmpeg_path: str = "ffmpeg"                          # used to take one picture from a camera for the "draw your counting line" screen
    dahua_relay_url: str = "http://127.0.0.1:8082"       # the Dahua gateway's video relay
    gb_relay_url: str = "http://127.0.0.1:8081"          # the registrar's video relay
    web_dir: str = ""                                    # folder of the web pages; default: backend/web next to this package
    ffprobe_path: str = "ffprobe"                       # used to check an RTSP address before a camera is created from it
    delivery_batch: int = 20
    evidence_backend: str = "local"                     # local (development) or r2 (Cloudflare R2)
    evidence_local_dir: str = "/tmp/gi-evidence"
    evidence_link_ttl_hours: int = 72
    retention_batch: int = 5000                         # rows deleted per statement, so a big clean-up never locks a table for long
    retention_seconds: int = 120                        # time budget for one clean-up run; it carries on at the next run
    evidence_max_bytes: int = 2_000_000
    clip_max_bytes: int = 15_000_000                    # WhatsApp accepts video up to 16 MB
    clip_wait_seconds: int = 40                         # how long a WhatsApp alert waits for its clip before going out without it
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_endpoint_url: str = ""                           # empty = https://<account>.r2.cloudflarestorage.com

    @field_validator("jwt_secret_customer", "jwt_secret_platform", "service_token")
    @classmethod
    def _long_enough(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("must be at least 32 characters")
        return v

    @field_validator("gb_realm")
    @classmethod
    def _realm(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 10):
            raise ValueError("must be 10 digits")
        return v

    @field_validator("gb_server_id")
    @classmethod
    def _server_id(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 20):
            raise ValueError("must be 20 digits")
        return v

    @field_validator("key_encryption_key")
    @classmethod
    def _kek(cls, v: str) -> str:
        if len(base64.b64decode(v)) != 32:
            raise ValueError("must be base64 of exactly 32 bytes")
        return v

    @model_validator(mode="after")
    def _distinct(self):
        if len({self.jwt_secret_customer, self.jwt_secret_platform, self.service_token}) != 3:
            raise ValueError("jwt_secret_customer, jwt_secret_platform and service_token must all differ")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
