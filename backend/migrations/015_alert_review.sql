-- Alert review (November 2026).

-- 1. Four alerts are removed from the product: plan limit, message allowance, payment due, new sign-in.
DELETE FROM app.alert_rules WHERE rule_type IN ('plan_limit','message_allowance','payment_due','new_sign_in');
DELETE FROM app.tenant_alert_grants WHERE alert_type IN ('plan_limit','message_allowance','payment_due','new_sign_in');
DELETE FROM platform.alert_switches WHERE alert_type IN ('plan_limit','message_allowance','payment_due','new_sign_in');
DELETE FROM platform.webhook_endpoints WHERE alert_type IN ('plan_limit','message_allowance','payment_due','new_sign_in');
UPDATE platform.plans SET alert_types = ARRAY(SELECT x FROM unnest(alert_types) x WHERE x NOT IN ('plan_limit','message_allowance','payment_due','new_sign_in'))
 WHERE alert_types IS NOT NULL AND alert_types && ARRAY['plan_limit','message_allowance','payment_due','new_sign_in'];
DROP TABLE IF EXISTS app.user_devices;                       -- only kept for the new sign-in alert

-- 2. No monthly limit on WhatsApp messages for now: the limit is cleared for every customer and no longer enforced.
UPDATE app.tenants SET msg_monthly_cap = NULL WHERE msg_monthly_cap IS NOT NULL;

-- 3. How often each timed alert is checked. No row means every 60 seconds. Set in Platform Admin.
CREATE TABLE IF NOT EXISTS platform.alert_checks (
  alert_type    text PRIMARY KEY,
  every_seconds integer NOT NULL CHECK (every_seconds BETWEEN 30 AND 3600),
  changed_by    uuid,
  changed_at    timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.alert_checks TO platform_admin;
GRANT SELECT ON platform.alert_checks TO service_worker;

-- 4. Our own video analysis: when each analysis worker last asked for its cameras, and the faults we have had.
CREATE TABLE IF NOT EXISTS platform.worker_seen (
  shard   integer NOT NULL,
  of      integer NOT NULL,
  seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (shard, of)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.worker_seen TO service_worker;
GRANT SELECT ON platform.worker_seen TO platform_admin;

CREATE TABLE IF NOT EXISTS platform.faults (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind       text NOT NULL,                                  -- 'analysis_stopped'
  detail     text NOT NULL DEFAULT '',
  cameras    integer NOT NULL DEFAULT 0,                     -- how many cameras were not being analysed
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at   timestamptz,
  told_at    timestamptz                                     -- when staff were told
);
CREATE UNIQUE INDEX IF NOT EXISTS faults_open_idx ON platform.faults (kind) WHERE ended_at IS NULL;
GRANT SELECT, INSERT, UPDATE ON platform.faults TO service_worker;
GRANT SELECT ON platform.faults TO platform_admin;

-- Who is told about a platform fault. One row. `people` is a list of {"name": ..., "phone": ...}.
CREATE TABLE IF NOT EXISTS platform.fault_notify (
  id         boolean PRIMARY KEY DEFAULT true CHECK (id),
  enabled    boolean NOT NULL DEFAULT true,
  people     jsonb NOT NULL DEFAULT '[]',
  changed_by uuid,
  changed_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON platform.fault_notify TO platform_admin;
GRANT SELECT ON platform.fault_notify TO service_worker;
