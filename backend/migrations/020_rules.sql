-- Customer-built rules: several rules of one alert kind, each with its own camera, marked area, numbers, people and quiet hours.
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -v ON_ERROR_STOP=1 -1 -f backend/migrations/020_rules.sql"
-- New databases get this from schema.sql and must not run it.

CREATE TABLE IF NOT EXISTS app.rules (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  rule_type   text NOT NULL,
  camera_id   uuid REFERENCES app.cameras(id) ON DELETE CASCADE,
  zone_id     uuid REFERENCES app.camera_zones(id) ON DELETE CASCADE,
  condition   jsonb NOT NULL DEFAULT '{}',
  channels    app.channel_kind[] NOT NULL DEFAULT '{whatsapp}',
  recipients  uuid[] NOT NULL DEFAULT '{}',
  quiet_start time,
  quiet_end   time,
  enabled     boolean NOT NULL DEFAULT false,
  is_default  boolean NOT NULL DEFAULT false,
  version     integer NOT NULL DEFAULT 1,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT rules_zone_needs_camera CHECK (zone_id IS NULL OR camera_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS rules_tenant_idx ON app.rules (tenant_id, rule_type);
CREATE INDEX IF NOT EXISTS rules_tenant_enabled_idx ON app.rules (tenant_id, enabled);
CREATE INDEX IF NOT EXISTS rules_camera_idx ON app.rules (camera_id) WHERE camera_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS rules_zone_idx ON app.rules (zone_id) WHERE zone_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS rules_one_default_idx ON app.rules (tenant_id, rule_type) WHERE is_default;

-- app.alerts.rule_id used to point at app.alert_rules only. It now holds either kind of rule, so the foreign key goes; the
-- row is history, and an alert keeps its type and details even when the rule that raised it is deleted.
ALTER TABLE app.alerts DROP CONSTRAINT IF EXISTS alerts_rule_id_fkey;

-- One account-wide rule for every setting the customer already had, so nothing changes until they edit it in the new builder.
INSERT INTO app.rules (tenant_id, name, rule_type, condition, channels, recipients, quiet_start, quiet_end, enabled, is_default)
SELECT r.tenant_id,
       initcap(replace(r.rule_type, '_', ' ')),
       r.rule_type,
       coalesce(r.params, '{}'::jsonb),
       r.channels,
       r.recipients,
       r.quiet_start,
       r.quiet_end,
       r.enabled,
       true
  FROM app.alert_rules r
 WHERE NOT EXISTS (SELECT 1 FROM app.rules x WHERE x.tenant_id = r.tenant_id AND x.rule_type = r.rule_type AND x.is_default);

-- Every marked area gets its own rule, carrying the customers' people and quiet hours from the alert setting it belongs to.
INSERT INTO app.rules (tenant_id, name, rule_type, camera_id, zone_id, condition, recipients, quiet_start, quiet_end, enabled)
SELECT z.tenant_id,
       left(coalesce(nullif(z.name, ''), c.name || ' area'), 80),
       CASE z.kind::text WHEN 'area_count' THEN 'people_in_area' WHEN 'queue_area' THEN 'queue_long' ELSE 'phone_use' END,
       z.camera_id,
       z.id,
       coalesce(z.params, '{}'::jsonb),
       coalesce(ar.recipients, '{}'::uuid[]),
       ar.quiet_start,
       ar.quiet_end,
       coalesce(ar.enabled, false)
  FROM app.camera_zones z
  JOIN app.cameras c ON c.id = z.camera_id
  LEFT JOIN app.alert_rules ar
         ON ar.tenant_id = z.tenant_id
        AND ar.rule_type = CASE z.kind::text WHEN 'area_count' THEN 'people_in_area' WHEN 'queue_area' THEN 'queue_long' ELSE 'phone_use' END
 WHERE z.kind::text IN ('area_count', 'queue_area', 'staff_area')
   AND NOT EXISTS (SELECT 1 FROM app.rules x WHERE x.zone_id = z.id);

ALTER TABLE app.rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.rules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON app.rules;
CREATE POLICY tenant_isolation ON app.rules USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.rules TO app_user, platform_admin, service_worker;
