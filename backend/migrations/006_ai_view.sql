-- "What the AI sees": an on-demand annotated picture of what the detector found.
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -f backend/migrations/006_ai_view.sql"
-- New databases get this from schema.sql and must not run it.
ALTER TABLE app.cameras ADD COLUMN ai_view_until timestamptz;
CREATE TABLE app.ai_view_frames (
  camera_id uuid PRIMARY KEY REFERENCES app.cameras(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  taken_at  timestamptz NOT NULL DEFAULT now(),
  jpeg      bytea NOT NULL
);
ALTER TABLE app.ai_view_frames ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ai_view_frames FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.ai_view_frames USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.ai_view_frames TO app_user, platform_admin, service_worker;
