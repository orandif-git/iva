-- Connection history and known sign-in devices, for the System Alerts "connection keeps dropping", "back to normal" and "new sign-in".
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -v ON_ERROR_STOP=1 -1 -f backend/migrations/009_system_alert_data.sql"
-- New databases get this from schema.sql and must not run it.
CREATE TABLE app.recorder_link_events (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  recorder_id uuid NOT NULL REFERENCES app.recorders(id) ON DELETE CASCADE,
  status      text NOT NULL CHECK (status IN ('online','offline')),
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recorder_link_events_idx ON app.recorder_link_events (recorder_id, at DESC);
CREATE TABLE app.user_devices (
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES app.tenant_users(id) ON DELETE CASCADE,
  fingerprint text NOT NULL,
  label       text NOT NULL,
  first_seen  timestamptz NOT NULL DEFAULT now(),
  last_seen   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, fingerprint)
);
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['recorder_link_events','user_devices'] LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app.%I TO app_user, platform_admin, service_worker', t);
  END LOOP;
END $$;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO app_user, platform_admin, service_worker;
