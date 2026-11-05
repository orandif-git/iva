-- Gate Insight: PostgreSQL 16 schema (multi-tenant, shared database, row-level security)
-- Two schemas:
--   platform : things only you (the platform owner) manage: staff, partners, plans, audit log
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
CREATE TYPE platform.staff_role   AS ENUM ('owner','support','technician','finance','analyst');
CREATE TYPE platform.actor_type   AS ENUM ('staff','partner_user','tenant_user','system');
CREATE TYPE app.tenant_status     AS ENUM ('trial','active','past_due','suspended','cancelled');
CREATE TYPE app.tenant_role       AS ENUM ('admin','regular');
CREATE TYPE app.recorder_brand    AS ENUM ('hikvision','dahua','other');
CREATE TYPE app.recorder_protocol AS ENUM ('isup','auto_register','gb28181','rtsp','cloud_api');
CREATE TYPE app.link_status       AS ENUM ('never','online','offline');
CREATE TYPE app.zone_kind         AS ENUM ('count_line','anpr_zone');
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
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.partners (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name             text NOT NULL,
  city             text,
  commission_rate  numeric(5,4) NOT NULL DEFAULT 0 CHECK (commission_rate BETWEEN 0 AND 1),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused')),
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.partner_users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id    uuid NOT NULL REFERENCES platform.partners(id) ON DELETE CASCADE,
  email         citext NOT NULL UNIQUE,
  name          text NOT NULL,
  status        text NOT NULL DEFAULT 'active',
  auth_subject  text UNIQUE,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.plans (
  id                      text PRIMARY KEY,         -- 'quick', 'full'
  name                    text NOT NULL,
  price_per_camera_paise  integer NOT NULL CHECK (price_per_camera_paise >= 0),
  stream_mode             text NOT NULL CHECK (stream_mode IN ('snapshots','realtime_5fps')),
  features                jsonb NOT NULL DEFAULT '{}',   -- e.g. {"anpr": true, "review_queue": true}
  active                  boolean NOT NULL DEFAULT true
);

-- How WhatsApp and email are sent. Owned by the platform, not by tenants. Secrets (API keys, SMTP password) are encrypted.
CREATE TABLE platform.messaging_providers (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  channel            app.channel_kind NOT NULL CHECK (channel IN ('whatsapp','email')),
  name               text NOT NULL,
  kind               text NOT NULL CHECK (kind IN ('http','smtp')),
  config             jsonb NOT NULL,                     -- everything except secrets: the request to send, how to read the answer, ...
  secrets_ciphertext bytea,                              -- encrypted JSON of the secret values
  rate_per_second    numeric(6,2) NOT NULL DEFAULT 5 CHECK (rate_per_second > 0),
  active             boolean NOT NULL DEFAULT false,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_provider_per_channel ON platform.messaging_providers (channel) WHERE active;

-- The message templates (WhatsApp requires each to be approved before use). variables lists, in order, what fills {{1}}, {{2}}, ...
CREATE TABLE platform.message_templates (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  channel           app.channel_kind NOT NULL CHECK (channel IN ('whatsapp','email')),
  key               text NOT NULL,                       -- ours, for example alert_image
  provider_template text NOT NULL,                       -- the name registered with the provider
  language          text NOT NULL DEFAULT 'en',
  category          text NOT NULL DEFAULT 'utility',
  description       text,
  body_text         text NOT NULL,
  media_type        text NOT NULL DEFAULT 'none' CHECK (media_type IN ('none','image','video')),
  link_button       boolean NOT NULL DEFAULT false,
  variables         jsonb NOT NULL DEFAULT '[]',
  status            text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','approved','rejected','paused')),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (channel, key, language)
);

-- The company issuing invoices, tax and payment terms, and the unit costs used to estimate margin. One row.
CREATE TABLE platform.billing_settings (
  id                   boolean PRIMARY KEY DEFAULT true CHECK (id),
  legal_name           text NOT NULL DEFAULT '',
  address              text NOT NULL DEFAULT '',
  gstin                text NOT NULL DEFAULT '',
  state_code           text NOT NULL DEFAULT '',        -- the seller's state (first two digits of the GSTIN)
  sac_code             text NOT NULL DEFAULT '',        -- service accounting code shown on invoices; confirm with your accountant
  bank_details         text NOT NULL DEFAULT '',
  invoice_prefix       text NOT NULL DEFAULT 'GI',
  gst_rate_percent     numeric(5,2) NOT NULL DEFAULT 18 CHECK (gst_rate_percent BETWEEN 0 AND 40),
  due_days             integer NOT NULL DEFAULT 15 CHECK (due_days BETWEEN 0 AND 120),
  auto_suspend_days    integer CHECK (auto_suspend_days IS NULL OR auto_suspend_days BETWEEN 1 AND 365),   -- NULL: never suspend automatically
  cost_per_million_frames_paise bigint NOT NULL DEFAULT 15000 CHECK (cost_per_million_frames_paise >= 0),
  cost_per_gb_month_paise       integer NOT NULL DEFAULT 200 CHECK (cost_per_gb_month_paise >= 0)
);
INSERT INTO platform.billing_settings DEFAULT VALUES;
CREATE TABLE platform.invoice_counters (fy text PRIMARY KEY, last integer NOT NULL DEFAULT 0);

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
  partner_id               uuid REFERENCES platform.partners(id),
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
  msg_monthly_cap          integer CHECK (msg_monthly_cap IS NULL OR msg_monthly_cap >= 0),   -- WhatsApp messages per calendar month, NULL = no cap
  suspended_at             timestamptz,
  suspended_reason         text,
  billing_customer_id      text,                      -- Razorpay / Stripe customer
  created_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tenants_partner_idx ON app.tenants (partner_id);
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
  recorder_id   uuid NOT NULL REFERENCES app.recorders(id) ON DELETE CASCADE,
  channel_no    integer NOT NULL,
  name          text NOT NULL,
  enabled       boolean NOT NULL DEFAULT true,
  use_footfall  boolean NOT NULL DEFAULT false,
  use_anpr      boolean NOT NULL DEFAULT false,
  stream        text NOT NULL DEFAULT 'sub' CHECK (stream IN ('sub','main')),
  last_frame_at timestamptz,
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
  CHECK (use_footfall OR use_anpr OR NOT enabled)
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

-- One rule per kind of alert per tenant. The customer switches each on or off, picks the channels and the people, and sets any limits.
CREATE TABLE app.alert_rules (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  rule_type   text NOT NULL CHECK (rule_type IN ('blocked_plate','vehicle_overstay','crowding','camera_offline','recorder_offline','low_confidence_plate')),
  enabled     boolean NOT NULL DEFAULT false,
  params      jsonb NOT NULL DEFAULT '{}',             -- thresholds, for example {"max_hours": 4} or {"people": 40, "minutes": 5}
  channels    app.channel_kind[] NOT NULL DEFAULT '{whatsapp}',
  recipients  uuid[] NOT NULL DEFAULT '{}',            -- app.recipients.id
  quiet_start time,                                    -- no messages between these local times (the alert is still recorded)
  quiet_end   time,
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
  acknowledged_at timestamptz
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
  template             text,                            -- our template key, for example alert_image
  payload              jsonb NOT NULL DEFAULT '{}',     -- the variables for the message
  status               app.delivery_status NOT NULL DEFAULT 'queued',
  provider_id          uuid,
  provider_message_id  text,
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

-- Invoices. Amounts are whole paise. Numbers are issued gaplessly per financial year when an invoice is issued, never for a draft.
CREATE TABLE app.invoices (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  period_month    date NOT NULL CHECK (period_month = date_trunc('month', period_month)::date),
  number          text UNIQUE,
  status          text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','issued','paid','void')),
  subtotal_paise  bigint NOT NULL CHECK (subtotal_paise >= 0),
  cgst_paise      bigint NOT NULL DEFAULT 0,
  sgst_paise      bigint NOT NULL DEFAULT 0,
  igst_paise      bigint NOT NULL DEFAULT 0,
  total_paise     bigint NOT NULL,
  tax_rate        numeric(5,2) NOT NULL,
  seller          jsonb,                                -- seller and buyer details frozen when issued, so a later change never rewrites an old invoice
  buyer           jsonb,
  issued_at       timestamptz,
  due_at          timestamptz,
  paid_at         timestamptz,
  payment_method  text,
  payment_reference text,
  void_reason     text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (status = 'draft' OR number IS NOT NULL OR status = 'void')
);
CREATE UNIQUE INDEX invoices_one_per_month ON app.invoices (tenant_id, period_month) WHERE status <> 'void';
CREATE INDEX invoices_due_idx ON app.invoices (due_at) WHERE status = 'issued';

CREATE TABLE app.invoice_lines (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  invoice_id   uuid NOT NULL REFERENCES app.invoices(id) ON DELETE CASCADE,
  tenant_id    uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  description  text NOT NULL,
  camera_days  integer NOT NULL,
  unit_paise   bigint NOT NULL,                         -- price per camera per month
  amount_paise bigint NOT NULL
);
CREATE INDEX invoice_lines_idx ON app.invoice_lines (invoice_id);

-- Daily metering. Feeds the admin billing page (revenue, cost, margin per tenant).
CREATE TABLE app.usage_daily (
  tenant_id           uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  day                 date NOT NULL,
  active_cameras      integer NOT NULL DEFAULT 0,
  frames_analysed     bigint  NOT NULL DEFAULT 0,
  alerts_whatsapp     integer NOT NULL DEFAULT 0,
  alerts_email        integer NOT NULL DEFAULT 0,
  storage_bytes       bigint  NOT NULL DEFAULT 0,
  compute_cost_paise  bigint  NOT NULL DEFAULT 0,
  billable_cameras    integer NOT NULL DEFAULT 0,       -- active cameras on a day the account was paying (trial and suspended days bill nothing)
  plan_id             text,                              -- the plan that day, so a mid-month change is billed fairly
  messaging_cost_paise bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, day)
);

-- Staff access to a tenant ("view as customer"). Tokens issued from this row are read-only and short-lived.
CREATE TABLE platform.impersonation_sessions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id    uuid NOT NULL REFERENCES platform.staff_users(id),
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id),
  reason      text NOT NULL CHECK (length(trim(reason)) >= 5),
  read_only   boolean NOT NULL DEFAULT true,
  notified    boolean NOT NULL DEFAULT false,
  started_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  ended_at    timestamptz,
  CHECK (expires_at > started_at AND expires_at <= started_at + interval '60 minutes')
);
CREATE INDEX imp_tenant_idx ON platform.impersonation_sessions (tenant_id, started_at DESC);

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

-- Grants
GRANT EXECUTE ON FUNCTION app.ensure_plate_partition(date) TO platform_admin, service_worker;
GRANT EXECUTE ON FUNCTION app.drop_old_plate_partitions(date) TO service_worker;
GRANT USAGE ON SCHEMA app TO app_user, platform_admin, service_worker;
GRANT USAGE ON SCHEMA platform TO platform_admin, service_worker, app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_user, platform_admin, service_worker;
GRANT SELECT ON platform.plans TO app_user, service_worker;
GRANT SELECT ON platform.messaging_providers, platform.message_templates TO service_worker;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA platform TO platform_admin;
REVOKE UPDATE, DELETE ON platform.audit_log FROM platform_admin, service_worker, app_user;
GRANT SELECT ON platform.audit_log TO platform_admin;
GRANT INSERT ON platform.audit_log TO platform_admin, service_worker;
GRANT INSERT ON platform.job_runs TO service_worker;
GRANT SELECT ON platform.billing_settings TO service_worker;
GRANT SELECT, INSERT, UPDATE ON platform.invoice_counters TO service_worker, platform_admin;
GRANT SELECT ON platform.job_runs TO platform_admin;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA platform TO platform_admin, service_worker;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO app_user, platform_admin, service_worker;
