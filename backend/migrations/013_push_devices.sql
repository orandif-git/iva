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
ALTER TABLE app.push_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.push_devices FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.push_devices USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.push_devices TO app_user, platform_admin, service_worker;
