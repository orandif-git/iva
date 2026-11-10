-- Alerts are chosen per plan and can be added to one customer at a time; the platform owner can switch any alert off.
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -f backend/migrations/007_alert_catalogue.sql"
-- New databases get this from schema.sql and must not run it.
ALTER TABLE app.alert_rules DROP CONSTRAINT alert_rules_rule_type_check;
ALTER TABLE platform.plans ADD COLUMN alert_types text[];
CREATE TABLE platform.alert_switches (
  alert_type text PRIMARY KEY,
  enabled    boolean NOT NULL,
  changed_by uuid,
  changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.tenant_alert_grants (
  tenant_id  uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  alert_type text NOT NULL,
  granted_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, alert_type)
);
ALTER TABLE app.tenant_alert_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.tenant_alert_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.tenant_alert_grants USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.tenant_alert_grants TO app_user, platform_admin, service_worker;
GRANT SELECT ON platform.alert_switches TO app_user, service_worker;
GRANT SELECT, INSERT, UPDATE ON platform.alert_switches TO platform_admin;
