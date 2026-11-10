-- Smart Alerts on marked areas: people in an area, queue too long, possible phone use in a staff area.
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -v ON_ERROR_STOP=1 -1 -f backend/migrations/010_area_alerts.sql"
-- New databases get this from schema.sql and must not run it.
ALTER TYPE app.zone_kind ADD VALUE 'area_count';
ALTER TYPE app.zone_kind ADD VALUE 'queue_area';
ALTER TYPE app.zone_kind ADD VALUE 'staff_area';
ALTER TABLE app.camera_zones ADD COLUMN name text, ADD COLUMN params jsonb NOT NULL DEFAULT '{}';
ALTER TABLE app.cameras ADD COLUMN use_areas boolean NOT NULL DEFAULT false;
DO $$
DECLARE c text;
BEGIN
  SELECT conname INTO c FROM pg_constraint WHERE conrelid = 'app.cameras'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%use_footfall%';
  IF c IS NOT NULL THEN EXECUTE format('ALTER TABLE app.cameras DROP CONSTRAINT %I', c); END IF;
END $$;
ALTER TABLE app.cameras ADD CHECK (use_footfall OR use_anpr OR use_areas OR NOT enabled);
CREATE TABLE app.zone_samples (
  zone_id    uuid NOT NULL REFERENCES app.camera_zones(id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  at         timestamptz NOT NULL,
  people     integer NOT NULL CHECK (people >= 0),
  wait_seconds real,
  phone_seconds real,
  evidence_id uuid,
  PRIMARY KEY (zone_id, at)
);
CREATE INDEX zone_samples_tenant_idx ON app.zone_samples (tenant_id, at DESC);
ALTER TABLE app.zone_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.zone_samples FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.zone_samples USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.zone_samples TO app_user, platform_admin, service_worker;
-- Switched off for everyone until the platform owner turns them on (Alerts page in the staff console).
INSERT INTO platform.alert_switches (alert_type, enabled) VALUES ('people_in_area', false), ('queue_long', false), ('phone_use', false) ON CONFLICT (alert_type) DO NOTHING;
