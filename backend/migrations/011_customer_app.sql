-- The new customer app: an alert can be snoozed (hidden from "Needs you" for a while) as well as marked done (acknowledged).
ALTER TABLE app.alerts ADD COLUMN snoozed_until timestamptz;

-- Live view and AI view from the customer app. A session is one person watching one camera for a limited time (see services/live.py).
CREATE TABLE app.live_sessions (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  camera_id  uuid NOT NULL REFERENCES app.cameras(id) ON DELETE CASCADE,
  user_id    uuid NOT NULL,
  mode       text NOT NULL CHECK (mode IN ('live','ai')),
  started_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,                      -- closes by itself at this time unless extended
  max_end_at timestamptz NOT NULL,                      -- the hard limit, however many times it is extended
  extensions integer NOT NULL DEFAULT 0,
  ended_at   timestamptz,
  ended_why  text
);
CREATE INDEX live_sessions_tenant_idx ON app.live_sessions (tenant_id, started_at DESC);
CREATE INDEX live_sessions_active_idx ON app.live_sessions (tenant_id) WHERE ended_at IS NULL;
ALTER TABLE app.live_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.live_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.live_sessions USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.live_sessions TO app_user, platform_admin, service_worker;
