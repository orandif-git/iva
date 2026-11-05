from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RegistrarSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GI_REG_", env_file=".env", extra="ignore")

    backend_url: str = "http://127.0.0.1:8000"
    service_token: str

    sip_bind: str = "0.0.0.0"
    sip_port: int = 5060
    public_ip: str                       # the address recorders can reach, written into SDP and Contact headers
    realm: str = "3402000000"            # must equal the backend's GI_GB_REALM
    server_id: str = "34020000002000000001"   # must equal the backend's GI_GB_SERVER_ID

    device_utc_offset_minutes: int = 330   # the clock recorders are set to when they register (330 = India)

    rtp_port_min: int = 30000
    rtp_port_max: int = 30999
    keepalive_timeout: float = 180.0     # recorders send a keepalive about every 60 s; three misses means offline
    heartbeat_interval: float = 30.0     # must be well under the backend's heartbeat_timeout_seconds (90)
    reap_interval: float = 5.0
    max_register_expires: int = 7200
    catalog_timeout: float = 10.0
    probe_timeout: float = 10.0
    probe_packets: int = 5
    probe_retries: int = 5

    # Video relay: lets the AI workers read a recorder's live video. See registrar/relay.py.
    relay_http_bind: str = "127.0.0.1"   # keep on a private address: workers reach it, nobody else should
    relay_http_port: int = 8081          # 0 picks a free port, -1 turns the relay off
    relay_public_base: str = ""          # how workers address it, e.g. http://registrar.internal:8081 (default: built from the bind address)
    relay_media_transport: str = "udp"   # "udp" (works with every recorder) or "tcp" (recommended across the internet, needs a recorder that supports it)
    relay_idle_grace: float = 10.0       # keep a stream open this long after the last viewer leaves, so a reconnecting worker does not restart it
    relay_stall_seconds: float = 10.0    # no video for this long ends the stream
    relay_max_streams: int = 100
    relay_max_streams_per_device: int = 4    # recorders only serve a few live streams at once
    relay_token_ttl: float = 60.0
    relay_subscriber_buffer: int = 4 * 1024 * 1024    # bytes a slow viewer may fall behind before it is disconnected
    relay_reorder_window: int = 16
    relay_gop_cache_bytes: int = 3 * 1024 * 1024    # replay from the last keyframe to a joining viewer; 0 turns it off

    @field_validator("service_token")
    @classmethod
    def _long(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("must be at least 32 characters")
        return v
