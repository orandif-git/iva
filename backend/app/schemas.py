from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
# "hikvision" and "isup" stay valid here so old rows can still be read, but creating them is refused (services/recorders.py).
Brand = Literal["hikvision", "dahua", "other"]
Protocol = Literal["isup", "auto_register", "gb28181"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- tenants (admin)
class TenantOut(BaseModel):
    id: UUID
    name: str
    slug: str
    city: str | None
    state: str | None
    status: str
    plan_id: str
    partner_id: UUID | None
    camera_limit: int
    price_override_paise: int | None = None
    trial_ends_at: datetime | None
    retention_events_days: int
    retention_snapshots_days: int
    suspended_at: datetime | None
    suspended_reason: str | None
    created_at: datetime
    cameras_in_use: int
    recorders_total: int
    recorders_online: int


class TenantCreate(Strict):
    name: Name
    city: Name | None = None
    plan_id: str
    partner_id: UUID | None = None
    camera_limit: int = Field(5, ge=0, le=5000)


class TenantPatch(Strict):
    name: Name | None = None
    city: Name | None = None
    partner_id: UUID | None = None
    camera_limit: int | None = Field(None, ge=0, le=5000)
    retention_events_days: int | None = Field(None, ge=1, le=730)
    retention_snapshots_days: int | None = Field(None, ge=1, le=90)


class ReasonIn(Strict):
    reason: Reason


class PlanIn(Strict):
    plan_id: str
    reason: Reason


class ImpersonateIn(Strict):
    reason: Reason
    minutes: int = Field(30, ge=15, le=60)
    notify: bool = True


class ImpersonateOut(BaseModel):
    session_id: UUID
    token: str
    expires_at: datetime
    read_only: bool = True


# --------------------------------------------------------------------- recorders
class RecorderOut(BaseModel):
    id: UUID
    tenant_id: UUID
    site_id: UUID | None
    device_id: str
    name: str | None = None
    brand: str
    protocol: str
    status: str
    last_heartbeat_at: datetime | None
    last_ip: str | None
    channel_count: int
    preview_ok_at: datetime | None
    firmware: str | None
    created_at: datetime


class AdminRecorderOut(RecorderOut):
    tenant_name: str


class RecorderCreateAdmin(Strict):
    """Staff add a recorder for a customer: the only way to connect GB28181 or a brand other than Dahua."""
    brand: Brand
    protocol: Protocol | None = None        # default: dahua -> auto_register. "other" needs gb28181.
    site_id: UUID | None = None
    name: Name | None = None
    reason: Reason


class RecorderCreateCustomer(Strict):
    brand: Brand
    protocol: Protocol | None = None
    site_id: UUID | None = None
    name: Name | None = None


class RecorderPatch(Strict):
    name: Name | None = None                 # send null to clear the name and show the id again


class RecorderCredentials(BaseModel):
    """Returned once. The key is not retrievable afterwards, only replaceable."""
    recorder_id: UUID
    device_id: str
    brand: str
    protocol: str | None = None
    server: str
    port: int
    key: str
    server_id: str | None = None            # GB28181 only: the SIP server id the recorder must be given
    domain: str | None = None               # GB28181 only: the SIP domain (realm)
    viewer_user: str | None = None          # Dahua Auto Register only: the viewer-only user to create on the recorder (its password is the key)


class RecorderConnection(BaseModel):
    """What is typed into the recorder, minus the password: not secret, so it can be shown again at any time."""
    recorder_id: UUID
    device_id: str
    brand: str
    protocol: str | None = None
    server: str
    port: int
    server_id: str | None = None
    domain: str | None = None
    viewer_user: str | None = None


class RecorderStatus(BaseModel):
    status: str
    checks: dict[str, bool]
    channel_count: int
    last_heartbeat_at: datetime | None


class ChannelOut(BaseModel):
    channel_no: int
    name: str | None
    active: bool
    enabled_as_camera: bool


# ----------------------------------------------------------------------- cameras
class CameraCreate(Strict):
    recorder_id: UUID
    channel_no: int = Field(ge=1, le=256)
    name: Name
    use_footfall: bool = False
    use_anpr: bool = False
    stream: Literal["sub", "main"] | None = None       # sub: light, enough for counting people. main: full resolution, for number plates. Default: main for plates, otherwise sub.

    @model_validator(mode="after")
    def _one_use(self):
        if not (self.use_footfall or self.use_anpr):
            raise ValueError("choose at least one use: footfall or number plates")
        return self


class CameraPatch(Strict):
    name: Name | None = None
    enabled: bool | None = None
    use_footfall: bool | None = None
    use_anpr: bool | None = None
    stream: Literal["sub", "main"] | None = None


class CameraOut(BaseModel):
    id: UUID
    connection: str = "recorder"
    recorder_id: UUID | None = None
    channel_no: int | None = None
    name: str
    enabled: bool
    use_footfall: bool
    use_anpr: bool
    stream: str
    last_frame_at: datetime | None
    stream_state: str


class ZoneIn(Strict):
    kind: Literal["count_line", "anpr_zone"]
    points: list[Annotated[list[float], Field(min_length=2, max_length=2)]]
    in_direction: Annotated[list[float], Field(min_length=2, max_length=2)] | None = None

    @field_validator("points")
    @classmethod
    def _norm(cls, pts):
        for p in pts:
            if not all(0.0 <= c <= 1.0 for c in p):
                raise ValueError("points must be normalised between 0 and 1")
        return pts


class ZonesPut(Strict):
    zones: list[ZoneIn] = Field(max_length=2)


class ZoneOut(BaseModel):
    id: UUID
    kind: str
    points: list
    in_direction: list | None


# ------------------------------------------------------------------- internal
class ChannelIn(Strict):
    channel_no: int = Field(ge=1, le=256)
    name: str | None = Field(None, max_length=80)
    external_id: str | None = Field(None, max_length=64)


class HeartbeatIn(Strict):
    ip: IPv4Address | IPv6Address
    channels: list[ChannelIn] = Field(max_length=256)
    firmware: str | None = Field(None, max_length=80)


# ------------------------------------------------------------- events: ingestion
def _aware(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("must include a timezone, for example 2026-11-04T10:15:00Z")
    return v


class FootfallEvent(Strict):
    camera_id: UUID
    bucket_start: datetime                  # start of a one-minute bucket, on the minute
    people_in: int = Field(ge=0, le=10000)
    people_out: int = Field(ge=0, le=10000)

    _tz = field_validator("bucket_start")(_aware)


class FootfallBatch(Strict):
    events: list[FootfallEvent] = Field(min_length=1, max_length=1000)


class PlateEvent(Strict):
    camera_id: UUID
    seen_at: datetime
    track_id: str | None = Field(None, max_length=64)
    plate_raw: str = Field(min_length=1, max_length=20)
    confidence: int = Field(ge=0, le=100)
    vehicle_type: str | None = Field(None, max_length=30)
    direction: Literal["in", "out"] | None = None
    snapshot_key: str | None = Field(None, max_length=256)
    evidence_id: UUID | None = None          # a picture the worker uploaded first (POST /internal/v1/evidence)

    _tz = field_validator("seen_at")(_aware)


class PlateBatch(Strict):
    events: list[PlateEvent] = Field(min_length=1, max_length=200)


class Rejected(BaseModel):
    index: int
    reason: str


CLIP_BEFORE, CLIP_AFTER = 5, 4        # seconds of video before and after the moment the plate was read


class ClipRequest(BaseModel):
    """The backend asks the worker for a short video of event number `index` in the batch it just sent."""
    index: int
    alert_id: UUID
    seconds_before: int
    seconds_after: int


class IngestResult(BaseModel):
    accepted: int
    duplicates: int = 0
    rejected: list[Rejected] = []
    clips: list[ClipRequest] = []


# ----------------------------------------------------------------- events: queries
class FootfallPoint(BaseModel):
    bucket: datetime
    people_in: int
    people_out: int


class PlateOut(BaseModel):
    id: int
    camera_id: UUID
    seen_at: datetime
    plate_text: str | None
    confidence: int | None
    vehicle_type: str | None
    direction: str | None
    review_status: str
    dwell_seconds: int | None
    entry_event_id: int | None
    has_snapshot: bool
    blocked: bool


class PlateDetail(PlateOut):
    plate_raw: str | None
    entry: PlateOut | None = None           # for an "out" read: the matching "in"
    exit: PlateOut | None = None            # for an "in" read: the matching "out"


class PlateReview(Strict):
    plate_text: str | None = Field(None, max_length=20)
    confirm: bool = False

    @model_validator(mode="after")
    def _one(self):
        if bool(self.plate_text) == bool(self.confirm):
            raise ValueError("send either plate_text (a correction) or confirm=true")
        return self


class ListEntryIn(Strict):
    plate_text: str = Field(min_length=4, max_length=20)
    list_type: Literal["blocked", "allowed"]
    note: str | None = Field(None, max_length=200)


class ListEntryOut(BaseModel):
    id: UUID
    plate_text: str
    list_type: str
    note: str | None
    created_at: datetime



# --------------------------------------------------------- frame rate and stream source
class FrameRatePatch(Strict):
    """Set any of these. Parts you leave out keep their current value."""
    mode: Literal["adaptive", "fixed"] | None = None
    idle_fps: int | None = Field(None, ge=1, le=10)
    active_fps: int | None = Field(None, ge=1, le=25)
    fixed_fps: int | None = Field(None, ge=1, le=25)
    reason: Reason

    @model_validator(mode="after")
    def _something(self):
        if self.mode is None and self.idle_fps is None and self.active_fps is None and self.fixed_fps is None:
            raise ValueError("set at least one of mode, idle_fps, active_fps, fixed_fps")
        return self


class SourceIn(Strict):
    url: str = Field(min_length=8, max_length=500)
    reason: Reason


class FrameStatusIn(Strict):
    state: Literal["streaming", "reconnecting", "offline"]
    analysis_fps: float | None = Field(None, ge=0, le=60)
    dropped: int | None = Field(None, ge=0, le=2_000_000_000)      # pictures dropped this run because analysis fell behind
    error: str | None = Field(None, max_length=200)


# --------------------------------------------------------------------------------------------- sign-in
Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
Secret = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class LoginIn(Strict):
    email: Email
    password: Secret
    tenant: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None     # only needed if the email is in several organisations


class SignInIn(Strict):
    email: Email
    password: Secret
    tenant: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=8)] | None = None


class StaffLoginIn(Strict):
    email: Email
    password: Secret
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=8)] | None = None      # needed unless the server runs without two-step sign-in


class RefreshIn(Strict):
    refresh_token: Annotated[str, StringConstraints(min_length=20, max_length=200)]


class SetPasswordIn(Strict):
    token: Annotated[str, StringConstraints(min_length=20, max_length=200)]
    password: Secret


class ChangePasswordIn(Strict):
    current_password: Secret
    new_password: Secret


class UserPatchIn(Strict):
    name: Name | None = None
    email: Email | None = None
    role: Literal["admin", "regular"] | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None


class StaffCreateIn(Strict):
    name: Name
    email: Email
    role: Literal["owner", "support"] = "support"


class StaffPatchIn(Strict):
    name: Name | None = None
    role: Literal["owner", "support"] | None = None


class UserInviteIn(Strict):
    email: Email
    name: Name
    role: Literal["admin", "regular"] = "admin"
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
