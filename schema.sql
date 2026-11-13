-- Jas iVision: PostgreSQL 16 schema (multi-tenant, shared database, row-level security)
-- Two schemas:
--   platform : things only you (the platform owner) manage: staff, plans, audit log
--   app      : everything that belongs to a customer (tenant). Every table carries tenant_id and is protected by RLS.
--
-- Database roles (create these with real passwords in your migration tool, not here):
--   app_user       used by the customer API. Subject to RLS. Cannot see other tenants.
--   platform_admin used by the admin console API. Reads across tenants (BYPASSRLS), all use is audited.
--   service_worker used by the registrar and AI workers. BYPASSRLS, narrow grants, mTLS only.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS app;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user')       THEN CREATE ROLE app_user NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='platform_admin') THEN CREATE ROLE platform_admin NOLOGIN BYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_worker') THEN CREATE ROLE service_worker NOLOGIN BYPASSRLS; END IF;
END $$;

-- ---------- enums ----------
CREATE TYPE platform.staff_role   AS ENUM ('owner','support','technician','finance','analyst','admin','onboarding');   -- owner, admin, onboarding and support are used; technician, finance and analyst are legacy values, never assigned

CREATE TYPE platform.actor_type   AS ENUM ('staff','partner_user','tenant_user','system');
CREATE TYPE app.tenant_status     AS ENUM ('trial','active','past_due','suspended','cancelled');
CREATE TYPE app.tenant_role       AS ENUM ('admin','regular');
CREATE TYPE app.recorder_brand    AS ENUM ('hikvision','dahua','other');
CREATE TYPE app.recorder_protocol AS ENUM ('isup','auto_register','gb28181','rtsp','cloud_api');
CREATE TYPE app.link_status       AS ENUM ('never','online','offline');
CREATE TYPE app.zone_kind         AS ENUM ('count_line','anpr_zone','area_count','queue_area','staff_area','activity_area');
CREATE TYPE app.review_status     AS ENUM ('auto','needs_review','confirmed','corrected');
CREATE TYPE app.direction         AS ENUM ('in','out');
CREATE TYPE app.list_type         AS ENUM ('blocked','allowed');
CREATE TYPE app.channel_kind      AS ENUM ('whatsapp','email','push');
CREATE TYPE app.delivery_status   AS ENUM ('queued','sent','delivered','read','failed','suppressed','cancelled');

-- ======================================================================
-- PLATFORM SCHEMA
-- ======================================================================
CREATE TABLE platform.staff_users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          citext NOT NULL UNIQUE,
  name           text NOT NULL,
  role           platform.staff_role NOT NULL,
  mfa_enabled    boolean NOT NULL DEFAULT false,
  status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  auth_subject   text UNIQUE,                       -- id from your identity provider
  last_login_at  timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  -- Sign-in (see app/services/auth.py). Staff always need a password AND a one-time code from an authenticator app.
  password_hash          text,
  totp_secret_ciphertext bytea,                     -- encrypted with the key vault, bound to the staff id
  totp_last_step         bigint NOT NULL DEFAULT 0, -- the last 30-second step accepted, so a code cannot be used twice
  failed_logins          integer NOT NULL DEFAULT 0,
  locked_until           timestamptz
);

CREATE TABLE platform.plans (
  id                      text PRIMARY KEY,         -- 'quick', 'full'
  name                    text NOT NULL,
  price_per_camera_paise  integer NOT NULL CHECK (price_per_camera_paise >= 0),
  stream_mode             text NOT NULL CHECK (stream_mode IN ('snapshots','realtime_5fps')),
  features                jsonb NOT NULL DEFAULT '{}',   -- e.g. {"anpr": true, "review_queue": true}
  active                  boolean NOT NULL DEFAULT true,
  -- When the price changes: 'next' moves existing customers to the new price at their next invoice, 'keep' leaves them on the price they had.
  price_change_policy     text NOT NULL DEFAULT 'next' CHECK (price_change_policy IN ('next','keep')),
  -- The alerts this plan includes (keys of the alert catalogue). NULL means every alert that exists, which is how plans behaved before alerts were chosen per plan.
  alert_types             text[]
);

-- The platform owner's on/off switch for each alert. No row means on. An alert that is off is hidden from every customer and raises nothing.
CREATE TABLE platform.alert_switches (
  alert_type text PRIMARY KEY,
  enabled    boolean NOT NULL,
  changed_by uuid,
  changed_at timestamptz NOT NULL DEFAULT now()
);
-- The area alerts start switched off: the platform owner turns them on once they have been tried on real footage.
INSERT INTO platform.alert_switches (alert_type, enabled) VALUES ('people_in_area', false), ('queue_long', false), ('phone_use', false);

-- How often each timed alert is checked. No row means every 60 seconds. Set in Platform Admin.
CREATE TABLE platform.alert_checks (
  alert_type    text PRIMARY KEY,
  every_seconds integer NOT NULL CHECK (every_seconds BETWEEN 30 AND 3600),
  changed_by    uuid,
  changed_at    timestamptz NOT NULL DEFAULT now()
);

-- The daily and weekly summary, sent to the owner's WhatsApp straight from the server (Direct Send) instead of through a webhook template.
-- One row: how to reach the provider, when summaries go out, and what they say. The provider's key is kept encrypted and never read back.
CREATE TABLE platform.summary_settings (
  id             boolean PRIMARY KEY DEFAULT true CHECK (id),
  config         jsonb NOT NULL DEFAULT '{}',
  key_ciphertext bytea,
  key_hint       text,                      -- the last four characters, so Platform Admin can say which key is in use
  key_changed_at timestamptz,
  key_changed_by text,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Our own video analysis: when each analysis worker last asked for its cameras, and the faults we have had.
CREATE TABLE IF NOT EXISTS platform.health_samples (
  ts                timestamptz PRIMARY KEY DEFAULT now(),
  disk_pct          real,
  mem_pct           real,
  load_pct          real,
  cameras_expected  integer,
  cameras_streaming integer,
  evidence_mb       real,
  db_mb             real
);
GRANT SELECT, INSERT, DELETE ON platform.health_samples TO service_worker;
GRANT SELECT ON platform.health_samples TO platform_admin;

CREATE TABLE platform.worker_seen (
  shard   integer NOT NULL,
  of      integer NOT NULL,
  seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (shard, of)
);
CREATE TABLE platform.faults (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind       text NOT NULL,                                  -- 'analysis_stopped'
  detail     text NOT NULL DEFAULT '',
  cameras    integer NOT NULL DEFAULT 0,                     -- how many cameras were not being analysed
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at   timestamptz,
  told_at    timestamptz                                     -- when staff were told
);
CREATE UNIQUE INDEX faults_open_idx ON platform.faults (kind) WHERE ended_at IS NULL;
-- Who is told about a platform fault. One row. `people` is a list of {"name": ..., "phone": ...}.
CREATE TABLE platform.fault_notify (
  id         boolean PRIMARY KEY DEFAULT true CHECK (id),
  enabled    boolean NOT NULL DEFAULT true,
  people     jsonb NOT NULL DEFAULT '[]',
  changed_by uuid,
  changed_at timestamptz NOT NULL DEFAULT now()
);

-- Where evidence pictures, clips and reports are kept. One row, set in the owner console. With no row the GI_EVIDENCE_* settings apply.
-- The R2 secret is encrypted like every other secret here.
CREATE TABLE platform.storage_settings (
  id                boolean PRIMARY KEY DEFAULT true CHECK (id),
  backend           text NOT NULL CHECK (backend IN ('local','r2')),
  account_id        text,
  bucket            text,
  endpoint_url      text,
  access_key_id     text,
  secret_ciphertext bytea,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- How email is sent (scheduled reports). Owned by the platform, not by tenants. The SMTP password is encrypted.
-- Alerts do not use this: they leave as webhook events (platform.webhook_endpoints).
CREATE TABLE platform.messaging_providers (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  channel            app.channel_kind NOT NULL CHECK (channel = 'email'),
  name               text NOT NULL,
  kind               text NOT NULL CHECK (kind = 'smtp'),
  config             jsonb NOT NULL,                     -- everything except the secrets: host, port, sender
  secrets_ciphertext bytea,                              -- encrypted JSON of the secret values
  rate_per_second    numeric(6,2) NOT NULL DEFAULT 5 CHECK (rate_per_second > 0),
  active             boolean NOT NULL DEFAULT false,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_provider_per_channel ON platform.messaging_providers (channel) WHERE active;

-- Alert webhooks. Each alert has at most one endpoint: when the alert happens we send the endpoint's payload (the template with its shortcodes filled in)
-- to the address, once for each person to be told. The platform owner sets these up; customers never see them.
-- Header values can hold a token, so they are encrypted like every other secret here.
CREATE TABLE platform.webhook_endpoints (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name               text NOT NULL,
  alert_type         text NOT NULL UNIQUE,               -- a key of the alert catalogue
  url                text NOT NULL,
  headers_ciphertext bytea,                              -- encrypted JSON object of header name -> value
  payload_template   text NOT NULL,                      -- JSON text with {shortcodes}
  active             boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- One row. The signing secret lets the receiver check an event came from us. link_hours is how long picture and clip links in a payload work (at most 4).
CREATE TABLE platform.webhook_settings (
  id                        boolean PRIMARY KEY DEFAULT true CHECK (id),
  signing_secret_ciphertext bytea,
  link_hours                integer NOT NULL DEFAULT 4 CHECK (link_hours BETWEEN 1 AND 4)
);

-- What the background jobs did and when (retention cleanup and similar), so an administrator can see it ran.
CREATE TABLE platform.job_runs (
  id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job       text NOT NULL,
  ran_at    timestamptz NOT NULL DEFAULT now(),
  seconds   numeric(8,2) NOT NULL DEFAULT 0,
  ok        boolean NOT NULL DEFAULT true,
  details   jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX job_runs_idx ON platform.job_runs (job, ran_at DESC);

-- Append-only audit log. Every staff, partner and impersonated action lands here.
CREATE TABLE platform.audit_log (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),
  actor_type   platform.actor_type NOT NULL,
  actor_id     uuid,
  actor_name   text,
  actor_role   text,
  tenant_id    uuid,                                 -- null for platform-wide actions
  action       text NOT NULL,                        -- 'tenant.suspend', 'impersonate.start', 'key.rotate' ...
  target_type  text,
  target_id    text,
  reason       text,
  before       jsonb,
  after        jsonb,
  ip           inet,
  request_id   text,
  impersonated_by uuid                               -- set when a staff member acts inside a tenant
);
CREATE INDEX audit_ts_idx      ON platform.audit_log (ts DESC);
CREATE INDEX audit_tenant_idx  ON platform.audit_log (tenant_id, ts DESC);
CREATE INDEX audit_action_idx  ON platform.audit_log (action, ts DESC);

CREATE FUNCTION platform.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON platform.audit_log
  FOR EACH ROW EXECUTE FUNCTION platform.audit_immutable();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON platform.audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION platform.audit_immutable();

-- ======================================================================
-- APP SCHEMA (tenant data)
-- ======================================================================
CREATE TABLE app.tenants (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                     text NOT NULL,
  slug                     citext NOT NULL UNIQUE,
  city                     text,
  state                    text DEFAULT 'Tamil Nadu',
  status                   app.tenant_status NOT NULL DEFAULT 'trial',
  plan_id                  text NOT NULL REFERENCES platform.plans(id),
  price_override_paise     integer CHECK (price_override_paise >= 0),   -- a price this customer keeps after the plan price changed; NULL = follow the plan
  camera_limit             integer NOT NULL DEFAULT 5 CHECK (camera_limit >= 0),
  trial_ends_at            timestamptz,
  retention_events_days    integer NOT NULL DEFAULT 30,
  retention_snapshots_days integer NOT NULL DEFAULT 7,
  -- Frame-rate policy for every camera of this tenant, unless a camera overrides it. Set from the admin console.
  --   adaptive: analyse at fps_idle while the scene is empty and at fps_active while a person or vehicle is present
  --   fixed:    always analyse at fps_fixed
  fps_mode                 text NOT NULL DEFAULT 'adaptive' CHECK (fps_mode IN ('adaptive','fixed')),
  fps_idle                 smallint NOT NULL DEFAULT 2 CHECK (fps_idle BETWEEN 1 AND 10),
  fps_active               smallint NOT NULL DEFAULT 5 CHECK (fps_active BETWEEN 1 AND 25),
  fps_fixed                smallint NOT NULL DEFAULT 5 CHECK (fps_fixed BETWEEN 1 AND 25),
  CHECK (fps_idle <= fps_active),
  -- Messaging controls, set from the admin console.
  timezone                 text NOT NULL DEFAULT 'Asia/Kolkata',     -- used for quiet hours and times shown in messages
  billing_name             text,                                      -- as it must appear on invoices
  billing_address          text,
  billing_gstin            text CHECK (billing_gstin IS NULL OR billing_gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'),
  billing_state_code       text CHECK (billing_state_code IS NULL OR billing_state_code ~ '^[0-9]{2}$'),   -- place of supply; taken from the GSTIN when there is one
  msg_whatsapp             boolean NOT NULL DEFAULT true,
  msg_email                boolean NOT NULL DEFAULT true,
  msg_clips                boolean NOT NULL DEFAULT true,            -- short video clips with alerts (they cost storage)
  -- Opening hours, in the customer's own time zone. NULL: not set, so "Counting stopped" and "After-hours activity" cannot fire. closed_days: 0 = Monday ... 6 = Sunday.
  open_time                time,
  close_time               time,
  closed_days              smallint[] NOT NULL DEFAULT '{}',
  -- Different hours on different days: {"0": ["09:00","21:00"], ...}, a day missing from the object is closed. NULL: every open day uses open_time and close_time.
  day_hours                jsonb,
  msg_language             text NOT NULL DEFAULT 'en+ta' CHECK (msg_language IN ('en','ta','en+ta')),   -- the language of this shop's messages
  ends_on                  date,                    -- the last day of service: after it everything stops (see services/service_end.py). NULL: no end date.
  msg_monthly_cap          integer CHECK (msg_monthly_cap IS NULL OR msg_monthly_cap >= 0),   -- WhatsApp messages per calendar month, NULL = no cap
  suspended_at             timestamptz,
  suspended_reason         text,
  billing_customer_id      text,                      -- Razorpay / Stripe customer
  created_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tenants_status_idx  ON app.tenants (status);

CREATE TABLE app.tenant_users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  email         citext NOT NULL,
  name          text NOT NULL,
  phone         text,                                  -- for WhatsApp alerts
  role          app.tenant_role NOT NULL DEFAULT 'regular',
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('invited','active','disabled')),
  auth_subject  text UNIQUE,
  last_login_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  password_hash   text,
  failed_logins   integer NOT NULL DEFAULT 0,
  locked_until    timestamptz,
  UNIQUE (tenant_id, email)
);

CREATE TABLE app.sites (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  name       text NOT NULL,
  address    text,
  timezone   text NOT NULL DEFAULT 'Asia/Kolkata',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- A recorder (NVR/DVR) that registers with your registrar. device_id is what the customer types on the recorder.
CREATE TABLE app.recorders (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  site_id            uuid REFERENCES app.sites(id) ON DELETE SET NULL,
  device_id          text NOT NULL UNIQUE,             -- 'SHARMA-001'
  name               text,                             -- a friendly name the customer chose; shown instead of the id
  brand              app.recorder_brand NOT NULL,
  protocol           app.recorder_protocol NOT NULL,
  -- ISUP and similar protocols need the shared secret to verify the device, so it cannot be a one-way hash.
  -- Store it encrypted with a KMS data key (envelope encryption). Show the plain key to the user once.
  key_ciphertext     bytea,
  key_kms_id         text,
  key_set_at         timestamptz,
  status             app.link_status NOT NULL DEFAULT 'never',
  last_heartbeat_at  timestamptz,
  last_ip            inet,
  channel_count      integer NOT NULL DEFAULT 0,
  preview_ok_at      timestamptz,                      -- first successful picture from any channel
  firmware           text,
  health             jsonb,                            -- what it last said about its disks, clock and recordings (see services/recorder_health.py)
  health_at          timestamptz,
  created_by         uuid,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recorders_tenant_idx ON app.recorders (tenant_id);
CREATE INDEX recorders_status_idx ON app.recorders (status, last_heartbeat_at);

-- Channels a recorder reports when it registers. A channel becomes a camera when the customer enables it.
CREATE TABLE app.recorder_channels (
  recorder_id uuid NOT NULL REFERENCES app.recorders(id) ON DELETE CASCADE,
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  channel_no  integer NOT NULL,
  external_id text,                                    -- the recorder's own id for the channel (GB28181: 20-digit channel id)
  name        text,
  active      boolean NOT NULL DEFAULT true,
  seen_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (recorder_id, channel_no)
);

CREATE TABLE app.cameras (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  site_id       uuid REFERENCES app.sites(id) ON DELETE SET NULL,
  connection    text NOT NULL DEFAULT 'recorder' CHECK (connection IN ('recorder','rtsp')),   -- 'recorder': a channel of a registered recorder; 'rtsp': an address a technician entered
  recorder_id   uuid REFERENCES app.recorders(id) ON DELETE CASCADE,
  channel_no    integer,
  name          text NOT NULL,
  enabled       boolean NOT NULL DEFAULT true,
  use_footfall  boolean NOT NULL DEFAULT false,
  use_anpr      boolean NOT NULL DEFAULT false,
  use_areas     boolean NOT NULL DEFAULT false,          -- counts people in marked areas, queues and phone use in staff areas; switched on by saving an area
  stream        text NOT NULL DEFAULT 'sub' CHECK (stream IN ('sub','main')),
  last_frame_at timestamptz,
  ai_view_until timestamptz,                         -- while this is in the future, the worker sends annotated pictures (someone is looking at "what the AI sees")
  -- Per-camera frame-rate overrides. NULL means "use the tenant's value".
  fps_mode      text CHECK (fps_mode IN ('adaptive','fixed')),
  fps_idle      smallint CHECK (fps_idle BETWEEN 1 AND 10),
  fps_active    smallint CHECK (fps_active BETWEEN 1 AND 25),
  fps_fixed     smallint CHECK (fps_fixed BETWEEN 1 AND 25),
  -- Health reported by the AI worker every few seconds.
  stream_state    text NOT NULL DEFAULT 'unknown' CHECK (stream_state IN ('unknown','streaming','reconnecting','offline')),
  stream_seen_at  timestamptz,                         -- server time of the last worker report
  stream_state_at timestamptz,
  analysis_fps    real,                                -- frames per second actually being analysed (the adaptive average)
  frames_dropped  integer,                             -- pictures thrown away this run because analysis could not keep up
  stream_error    text,                                -- last problem, with any password already removed
  -- RTSP address for cameras the worker pulls directly. It holds the camera password, so it is stored encrypted.
  source_ciphertext bytea,
  source_host     text,                                -- host:port only, safe to show
  source_set_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (recorder_id, channel_no),
  CHECK (use_footfall OR use_anpr OR use_areas OR NOT enabled),
  CONSTRAINT cameras_kind_check CHECK (
    (connection = 'recorder' AND recorder_id IS NOT NULL AND channel_no IS NOT NULL) OR (connection = 'rtsp' AND recorder_id IS NULL AND channel_no IS NULL))
);
CREATE INDEX cameras_tenant_idx ON app.cameras (tenant_id);

-- Counting line or ANPR zone, drawn on the setup screen. Points are normalised 0..1 so they survive resolution changes.
CREATE TABLE app.camera_zones (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  camera_id    uuid NOT NULL REFERENCES app.cameras(id) ON DELETE CASCADE,
  kind         app.zone_kind NOT NULL,
  points       jsonb NOT NULL,                         -- [[x,y],[x,y],...]
  in_direction jsonb,                                  -- unit vector that counts as "in", for count_line
  name         text,                                   -- an area's name, for example "Billing counter"
  params       jsonb NOT NULL DEFAULT '{}',            -- an area's thresholds, for example {"limit": 8, "minutes": 2}
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX zones_camera_idx ON app.camera_zones (camera_id);

-- One row per camera per minute. Roll up to hours and days at query time or with a materialised view.
CREATE TABLE app.footfall_counts (
  tenant_id    uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  camera_id    uuid NOT NULL REFERENCES app.cameras(id) ON DELETE CASCADE,
  bucket_start timestamptz NOT NULL,
  people_in    integer NOT NULL DEFAULT 0,
  people_out   integer NOT NULL DEFAULT 0,
  PRIMARY KEY (camera_id, bucket_start)
);
CREATE INDEX footfall_tenant_time_idx ON app.footfall_counts (tenant_id, bucket_start DESC);

-- What each camera's picture usually looks like, so a camera that has been moved, turned or covered, or whose picture has stayed too dark or too blurry, can be noticed.
CREATE TABLE app.camera_picture_state (
  camera_id       uuid PRIMARY KEY REFERENCES app.cameras(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  ref_day         bytea,                          -- the outline sketch of the usual view in daylight, and at night (the sketch changes when the camera switches to infrared)
  ref_day_at      timestamptz,
  ref_night       bytea,
  ref_night_at    timestamptz,
  baseline_sharp  real,                           -- how sharp its picture usually is in daylight (a slow average)
  day_samples     integer NOT NULL DEFAULT 0,
  bad_view_since  timestamptz,                    -- since when the view has not matched ('moved') or has been flat ('covered')
  bad_view_kind   text,
  poor_since      timestamptz,                    -- since when the picture has been 'blurry' or 'dark'
  poor_kind       text,
  last_at         timestamptz,
  last_brightness real,
  last_sharpness  real
);

-- Plate reads. Partitioned by month so old data can be dropped cheaply (retention).
CREATE TABLE app.plate_events (
  id             bigint GENERATED ALWAYS AS IDENTITY,
  tenant_id      uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  camera_id      uuid NOT NULL REFERENCES app.cameras(id) ON DELETE CASCADE,
  seen_at        timestamptz NOT NULL,
  track_id       text,
  plate_text     text,                                 -- cleaned and format-validated, e.g. GJ14AW1013
  plate_raw      text,                                 -- what OCR actually returned
  confidence     smallint CHECK (confidence BETWEEN 0 AND 100),
  vehicle_type   text,
  direction      app.direction,
  snapshot_key   text,                                 -- object storage key
  review_status  app.review_status NOT NULL DEFAULT 'auto',
  reviewed_by    uuid,
  reviewed_at    timestamptz,
  entry_event_id bigint,                               -- set on an 'out' event to link its matching 'in'
  dwell_seconds  integer,
  PRIMARY KEY (id, seen_at)
) PARTITION BY RANGE (seen_at);
CREATE TABLE app.plate_events_default PARTITION OF app.plate_events DEFAULT;
CREATE TABLE app.plate_events_2026_11 PARTITION OF app.plate_events FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

-- Creates the monthly partition that holds a given date. Run by the backend's maintenance job (current month and the next two).
-- SECURITY DEFINER because attaching a partition needs the table owner. A partition cannot be created once rows for that range sit
-- in the default partition, so this must keep running ahead of time.
CREATE FUNCTION app.ensure_plate_partition(p_day date) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $$
DECLARE m date := date_trunc('month', p_day)::date;
BEGIN
  EXECUTE format('CREATE TABLE IF NOT EXISTS app.%I PARTITION OF app.plate_events FOR VALUES FROM (%L) TO (%L)',
                 'plate_events_' || to_char(m, 'YYYY_MM'), m, (m + interval '1 month')::date);
END $$;
REVOKE ALL ON FUNCTION app.ensure_plate_partition(date) FROM PUBLIC;

-- Drops monthly plate partitions that end on or before p_before. Whole months older than every tenant's retention are far cheaper to drop than to delete.
-- Returns the names dropped. The default partition and the current month are never touched.
CREATE FUNCTION app.drop_old_plate_partitions(p_before date) RETURNS text[] LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $$
DECLARE r record; dropped text[] := '{}'; m date;
BEGIN
  FOR r IN SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent
           WHERE p.relname = 'plate_events' AND p.relnamespace = 'app'::regnamespace AND c.relname ~ '^plate_events_[0-9]{4}_[0-9]{2}$'
  LOOP
    m := to_date(substr(r.relname, 14), 'YYYY_MM');
    IF (m + interval '1 month')::date <= p_before AND m < date_trunc('month', now())::date THEN
      EXECUTE format('DROP TABLE app.%I', r.relname);
      dropped := dropped || r.relname;
    END IF;
  END LOOP;
  RETURN dropped;
END $$;
REVOKE ALL ON FUNCTION app.drop_old_plate_partitions(date) FROM PUBLIC;
CREATE INDEX plates_tenant_time_idx ON app.plate_events (tenant_id, seen_at DESC);
CREATE INDEX plates_text_idx        ON app.plate_events (tenant_id, plate_text, seen_at DESC);
CREATE INDEX plates_entry_idx       ON app.plate_events (entry_event_id) WHERE entry_event_id IS NOT NULL;
CREATE INDEX plates_review_idx      ON app.plate_events (tenant_id, review_status) WHERE review_status = 'needs_review';

CREATE TABLE app.vehicle_lists (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  plate_text  text NOT NULL,
  list_type   app.list_type NOT NULL,
  note        text,
  created_by  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, plate_text, list_type)
);

-- People who receive alerts. They need not have a login: a security guard or a plant manager is enough.
-- Alerts added to one customer on top of their plan, one at a time. Removing a row takes the alert away again; the customer's own settings for it are kept.
CREATE TABLE app.tenant_alert_grants (
  tenant_id  uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  alert_type text NOT NULL,
  granted_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, alert_type)
);

-- What the worker saw in a marked area, every ten seconds or so. Short-lived: the alerts look at the last few minutes only.
CREATE TABLE app.zone_samples (
  zone_id    uuid NOT NULL REFERENCES app.camera_zones(id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  at         timestamptz NOT NULL,
  people     integer NOT NULL CHECK (people >= 0),
  wait_seconds real,                                    -- queue area: how long the person who has waited longest has been there
  phone_seconds real,                                   -- staff area: how long a phone has been in use without a break
  evidence_id uuid,                                     -- a picture taken when the phone threshold was reached
  PRIMARY KEY (zone_id, at)
);
CREATE INDEX zone_samples_tenant_idx ON app.zone_samples (tenant_id, at DESC);

-- When a recorder went online or offline. Lets us see a connection that keeps dropping, and tell the customer when something is back.
CREATE TABLE app.recorder_link_events (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  recorder_id uuid NOT NULL REFERENCES app.recorders(id) ON DELETE CASCADE,
  status      text NOT NULL CHECK (status IN ('online','offline')),
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recorder_link_events_idx ON app.recorder_link_events (recorder_id, at DESC);

CREATE TABLE app.recipients (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  name                 text NOT NULL,
  phone                text CHECK (phone ~ '^\+[1-9][0-9]{7,14}$'),     -- international format, for example +919876543210
  email                citext CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  active               boolean NOT NULL DEFAULT true,
  -- The customer confirms, when entering a number, that the person agreed to receive messages. Who confirmed, and when, is kept.
  consent_confirmed_by uuid,
  consent_confirmed_at timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (phone IS NOT NULL OR email IS NOT NULL),
  CHECK (phone IS NULL OR consent_confirmed_at IS NOT NULL)
);
CREATE UNIQUE INDEX recipients_phone_idx ON app.recipients (tenant_id, phone) WHERE phone IS NOT NULL;

-- A picture (or later a clip) that proves what an alert is about. Stored in object storage (Cloudflare R2), never in the database.
CREATE TABLE app.evidence (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  camera_id    uuid REFERENCES app.cameras(id) ON DELETE SET NULL,
  kind         text NOT NULL CHECK (kind IN ('image','clip')),
  storage_key  text NOT NULL,
  content_type text NOT NULL,
  bytes        integer NOT NULL CHECK (bytes >= 0),
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL                        -- when the stored file is deleted (retention)
);
CREATE INDEX evidence_expiry_idx ON app.evidence (expires_at);

-- "What the AI sees": while a staff member has the view open, the worker sends an annotated picture every few seconds, which replaces the last one.
-- It is never kept: one row per camera, overwritten, and deleted a few minutes after the last request.
CREATE TABLE app.ai_view_frames (
  camera_id uuid PRIMARY KEY REFERENCES app.cameras(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  taken_at  timestamptz NOT NULL DEFAULT now(),
  jpeg      bytea NOT NULL
);

-- Phones and browsers that have signed in to the customer app and agreed to notifications. One row per device (the token is the device's address at Google/Apple).
CREATE TABLE app.push_devices (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL,
  token        text NOT NULL UNIQUE,
  platform     text NOT NULL CHECK (platform IN ('web','android','ios')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX push_devices_tenant_idx ON app.push_devices (tenant_id);

-- The picture kept for a plate read (the same evidence store, so it follows the same retention).
ALTER TABLE app.plate_events ADD COLUMN evidence_id uuid REFERENCES app.evidence(id) ON DELETE SET NULL;

-- One rule per kind of alert per tenant. The customer switches each on or off, picks the channels and the people, and sets any limits.
CREATE TABLE app.alert_rules (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  rule_type   text NOT NULL,                           -- a key of the alert catalogue (app/services/alert_catalog.py)
  enabled     boolean NOT NULL DEFAULT false,
  params      jsonb NOT NULL DEFAULT '{}',             -- thresholds, for example {"max_hours": 4} or {"people": 40, "minutes": 5}
  channels    app.channel_kind[] NOT NULL DEFAULT '{whatsapp}',
  recipients  uuid[] NOT NULL DEFAULT '{}',            -- app.recipients.id
  quiet_start time,                                    -- no messages between these local times (the alert is still recorded)
  quiet_end   time,
  label       text,                                    -- the name the Control Center shows for this event
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, rule_type)
);

CREATE TABLE app.alerts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  rule_id         uuid REFERENCES app.alert_rules(id) ON DELETE SET NULL,
  rule_type       text,
  camera_id       uuid REFERENCES app.cameras(id) ON DELETE SET NULL,
  severity        text NOT NULL CHECK (severity IN ('info','warning','critical')),
  title           text NOT NULL,
  body            text,
  details         jsonb NOT NULL DEFAULT '{}',          -- what message templates fill in: event, location, plate, ...
  dedupe_key      text,                                 -- the same key inside its cool-down raises no second alert
  evidence_key    text,
  evidence_id     uuid REFERENCES app.evidence(id) ON DELETE SET NULL,     -- the picture
  clip_id         uuid REFERENCES app.evidence(id) ON DELETE SET NULL,     -- the short video, which arrives a few seconds after the alert
  clip_wait_until timestamptz,                                              -- WhatsApp waits for the clip until then; NULL = no clip expected
  created_at      timestamptz NOT NULL DEFAULT now(),
  acknowledged_by uuid,
  acknowledged_at timestamptz,
  snoozed_until   timestamptz                                                -- hidden from "Needs you" until then
);
CREATE INDEX alerts_tenant_time_idx ON app.alerts (tenant_id, created_at DESC);
CREATE INDEX alerts_dedupe_idx ON app.alerts (tenant_id, dedupe_key, created_at DESC) WHERE dedupe_key IS NOT NULL;

-- The outbox. An alert writes its messages here in the same transaction, so a crash can never lose one. The delivery service takes them
-- from here, sends them, retries the ones that fail for a temporary reason, and records what the provider reports back.
CREATE TABLE app.notifications (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  alert_id             uuid REFERENCES app.alerts(id) ON DELETE CASCADE,
  report_id            uuid,                            -- set instead of alert_id for a scheduled report sent by email (FK added below)
  recipient_id         uuid REFERENCES app.recipients(id) ON DELETE SET NULL,
  channel              app.channel_kind NOT NULL,
  recipient            text NOT NULL,                   -- the phone number or email address used
  template             text,                            -- unused since alerts leave as webhook events
  payload              jsonb NOT NULL DEFAULT '{}',     -- the variables for the message
  status               app.delivery_status NOT NULL DEFAULT 'queued',
  provider_id          uuid,
  provider_message_id  text,
  event                text,                            -- the webhook event sent, for example system.camera_offline
  endpoint_id          uuid,                            -- the webhook endpoint it went to
  response_status      integer,                         -- what the endpoint answered on the last try
  attempts             integer NOT NULL DEFAULT 0,
  next_attempt_at      timestamptz NOT NULL DEFAULT now(),
  locked_until         timestamptz,                     -- a worker that crashes mid-send simply lets this lapse
  error                text,
  fallback_of          uuid,                            -- set on an email sent because this WhatsApp message failed for good
  cost_paise           integer NOT NULL DEFAULT 0,
  created_at           timestamptz NOT NULL DEFAULT now(),
  sent_at              timestamptz,
  delivered_at         timestamptz,
  read_at              timestamptz
);
CREATE INDEX notif_tenant_time_idx ON app.notifications (tenant_id, created_at DESC);
CREATE INDEX notif_failed_idx      ON app.notifications (status) WHERE status = 'failed';
CREATE INDEX notif_due_idx         ON app.notifications (next_attempt_at) WHERE status = 'queued';
CREATE INDEX notif_provider_msg_idx ON app.notifications (provider_id, provider_message_id) WHERE provider_message_id IS NOT NULL;
CREATE UNIQUE INDEX notif_once_idx ON app.notifications (alert_id, recipient_id, channel) WHERE alert_id IS NOT NULL AND recipient_id IS NOT NULL;
CREATE UNIQUE INDEX notif_report_once_idx ON app.notifications (report_id, recipient_id, channel) WHERE report_id IS NOT NULL AND recipient_id IS NOT NULL;

-- Reports are Excel files built from a tenant's own data. A schedule says which report, how often, at what local time, and who gets it by email.
CREATE TABLE app.report_schedules (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  report_type     text NOT NULL CHECK (report_type IN ('gate_register','footfall_summary','vehicle_audit')),
  frequency       text NOT NULL CHECK (frequency IN ('daily','weekly','monthly')),
  send_at         time NOT NULL DEFAULT '07:00',          -- local time of the tenant
  weekday         smallint CHECK (weekday BETWEEN 0 AND 6),            -- weekly: 0 = Monday
  day_of_month    smallint CHECK (day_of_month BETWEEN 1 AND 28),      -- monthly
  recipients      uuid[] NOT NULL DEFAULT '{}',           -- app.recipients.id, by email
  enabled         boolean NOT NULL DEFAULT true,
  last_period_end timestamptz,                            -- the end of the latest period already produced, so a period is never produced twice
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (frequency <> 'weekly' OR weekday IS NOT NULL),
  CHECK (frequency <> 'monthly' OR day_of_month IS NOT NULL),
  UNIQUE (tenant_id, report_type, frequency)
);

CREATE TABLE app.reports (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  schedule_id  uuid REFERENCES app.report_schedules(id) ON DELETE SET NULL,
  report_type  text NOT NULL CHECK (report_type IN ('gate_register','footfall_summary','vehicle_audit')),
  period_start timestamptz NOT NULL,
  period_end   timestamptz NOT NULL,
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','working','ready','failed')),
  requested_by uuid,                                      -- NULL for a scheduled report
  file_key     text,
  bytes        integer,
  row_count    integer,
  truncated    boolean NOT NULL DEFAULT false,            -- more rows existed than one file may hold
  error        text,
  locked_until timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  generated_at timestamptz,
  expires_at   timestamptz NOT NULL
);
CREATE INDEX reports_tenant_idx ON app.reports (tenant_id, created_at DESC);
CREATE INDEX reports_queue_idx  ON app.reports (created_at) WHERE status = 'queued';

ALTER TABLE app.notifications ADD FOREIGN KEY (report_id) REFERENCES app.reports(id) ON DELETE CASCADE;

-- Refresh tokens, invitations and password resets. Only a hash of each token is stored, so a copy of the database cannot be used to sign in.
CREATE TABLE platform.auth_tokens (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind         text NOT NULL CHECK (kind IN ('refresh','invite','reset')),
  account_type text NOT NULL CHECK (account_type IN ('staff','customer')),
  account_id   uuid NOT NULL,
  family       uuid,                                -- refresh tokens: one family per sign-in, so reuse of an old token revokes the whole family
  token_hash   text NOT NULL UNIQUE,
  created_by   uuid,                                -- staff who made an invite or reset link
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  used_at      timestamptz,
  revoked_at   timestamptz
);
CREATE INDEX auth_tokens_account_idx ON platform.auth_tokens (account_type, account_id, kind);

-- ======================================================================
-- ROW-LEVEL SECURITY
-- The customer API runs each request in a transaction:
--   BEGIN; SET LOCAL ROLE app_user; SELECT set_config('app.tenant_id', '<uuid from the token>', true); ...
-- The tenant id always comes from the verified token, never from the URL or request body.
-- ======================================================================
CREATE FUNCTION app.current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT table_name FROM information_schema.columns
           WHERE table_schema='app' AND column_name='tenant_id'
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant())', t);
  END LOOP;
END $$;

ALTER TABLE app.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON app.tenants USING (id = app.current_tenant());

-- Lets the customer API write audit rows without being able to forge the tenant or the actor.
-- The tenant comes from app.current_tenant(); the actor comes from settings the API sets from the verified token.
CREATE FUNCTION platform.audit_from_app(p_action text, p_target_type text, p_target_id text, p_after jsonb, p_reason text DEFAULT NULL)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, platform, app AS $$
  INSERT INTO platform.audit_log (actor_type, actor_id, actor_name, actor_role, tenant_id, action, target_type, target_id, reason, after, request_id)
  VALUES ('tenant_user',
          nullif(current_setting('app.actor_id', true), '')::uuid,
          nullif(current_setting('app.actor_name', true), ''),
          nullif(current_setting('app.actor_role', true), ''),
          app.current_tenant(), p_action, p_target_type, p_target_id, p_reason, p_after,
          nullif(current_setting('app.request_id', true), ''));
$$;
REVOKE ALL ON FUNCTION platform.audit_from_app(text,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.audit_from_app(text,text,text,jsonb,text) TO app_user;

-- How long Jas iVision keeps customer data (alerts and their messages, people counts, plate reads, pictures and clips). One setting for every customer.
CREATE TABLE IF NOT EXISTS platform.data_retention (
  id         boolean PRIMARY KEY DEFAULT true CHECK (id),
  keep_days  integer NOT NULL DEFAULT 30 CHECK (keep_days BETWEEN 7 AND 730),
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by text
);
INSERT INTO platform.data_retention (id) VALUES (true) ON CONFLICT DO NOTHING;

-- The customer's owner: name and email on the customer, and up to three owner mobile numbers. A number belongs to one customer only (later used to sign in with a one-time code).
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS owner_name  text;
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS owner_email text;
CREATE TABLE IF NOT EXISTS platform.tenant_owner_phones (
  tenant_id uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  phone     text NOT NULL UNIQUE,
  position  smallint NOT NULL CHECK (position BETWEEN 1 AND 3),
  PRIMARY KEY (tenant_id, position)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.tenant_owner_phones TO platform_admin;
GRANT SELECT ON platform.tenant_owner_phones TO service_worker;

-- Grants
GRANT EXECUTE ON FUNCTION app.ensure_plate_partition(date) TO platform_admin, service_worker;
GRANT EXECUTE ON FUNCTION app.drop_old_plate_partitions(date) TO service_worker;
GRANT USAGE ON SCHEMA app TO app_user, platform_admin, service_worker;
GRANT USAGE ON SCHEMA platform TO platform_admin, service_worker, app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_user, platform_admin, service_worker;
GRANT SELECT ON platform.plans, platform.alert_switches TO app_user, service_worker;
GRANT SELECT ON platform.messaging_providers, platform.storage_settings, platform.webhook_endpoints, platform.webhook_settings TO service_worker;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA platform TO platform_admin;
GRANT DELETE ON platform.plans, platform.messaging_providers, platform.webhook_endpoints, platform.alert_checks, platform.staff_users TO platform_admin;
GRANT SELECT ON platform.alert_checks, platform.fault_notify, platform.summary_settings TO service_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.worker_seen TO service_worker;
GRANT SELECT, INSERT, UPDATE ON platform.faults TO service_worker;
GRANT SELECT ON platform.faults TO app_user;
REVOKE UPDATE, DELETE ON platform.audit_log FROM platform_admin, service_worker, app_user;
GRANT SELECT ON platform.audit_log TO platform_admin;
GRANT INSERT ON platform.audit_log TO platform_admin, service_worker;
GRANT INSERT ON platform.job_runs TO service_worker;
GRANT SELECT ON platform.job_runs TO platform_admin;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA platform TO platform_admin, service_worker;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO app_user, platform_admin, service_worker;
