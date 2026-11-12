-- The daily and weekly summary, sent to the owner's WhatsApp straight from the server (Direct Send) instead of through a webhook template.
-- One row: how to reach the provider, when summaries go out, and what they say. The provider's key is kept encrypted and never read back.
CREATE TABLE IF NOT EXISTS platform.summary_settings (
  id             boolean PRIMARY KEY DEFAULT true CHECK (id),
  config         jsonb NOT NULL DEFAULT '{}',
  key_ciphertext bytea,
  key_hint       text,                      -- the last four characters, so Platform Admin can say which key is in use
  key_changed_at timestamptz,
  key_changed_by text,
  updated_at     timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON platform.summary_settings TO service_worker;
GRANT SELECT, INSERT, UPDATE ON platform.summary_settings TO platform_admin;

-- The weekly summary is a new alert. A plan that lists its alerts and has the daily summary gets the weekly one too, and so does a customer who was given the daily one.
UPDATE platform.plans SET alert_types = array_append(alert_types, 'weekly_summary')
 WHERE alert_types IS NOT NULL AND 'daily_summary' = ANY(alert_types) AND NOT ('weekly_summary' = ANY(alert_types));
INSERT INTO app.tenant_alert_grants (tenant_id, alert_type)
SELECT tenant_id, 'weekly_summary' FROM app.tenant_alert_grants WHERE alert_type = 'daily_summary' ON CONFLICT DO NOTHING;
