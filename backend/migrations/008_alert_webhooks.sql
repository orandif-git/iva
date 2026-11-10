-- Alerts leave as webhook events instead of through a WhatsApp provider, and never as email.
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -f backend/migrations/008_alert_webhooks.sql"
-- New databases get this from schema.sql and must not run it.
-- WhatsApp providers and message templates are removed. Email (SMTP) stays for scheduled reports.
DELETE FROM platform.messaging_providers WHERE channel <> 'email' OR kind <> 'smtp';
ALTER TABLE platform.messaging_providers DROP CONSTRAINT messaging_providers_channel_check;
ALTER TABLE platform.messaging_providers DROP CONSTRAINT messaging_providers_kind_check;
ALTER TABLE platform.messaging_providers ADD CHECK (channel = 'email');
ALTER TABLE platform.messaging_providers ADD CHECK (kind = 'smtp');
DROP TABLE platform.message_templates;
-- A WhatsApp message that was waiting for the old provider is dropped; the webhook flow starts clean.
UPDATE app.notifications SET status='cancelled', error='Replaced by webhook delivery.', locked_until=NULL WHERE status='queued' AND channel IN ('whatsapp','email') AND report_id IS NULL;
ALTER TABLE app.notifications ADD COLUMN event text, ADD COLUMN endpoint_id uuid, ADD COLUMN response_status integer;
CREATE TABLE platform.webhook_endpoints (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name               text NOT NULL,
  alert_type         text NOT NULL UNIQUE,
  url                text NOT NULL,
  headers_ciphertext bytea,
  payload_template   text NOT NULL,
  active             boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE platform.webhook_settings (
  id                        boolean PRIMARY KEY DEFAULT true CHECK (id),
  signing_secret_ciphertext bytea,
  link_hours                integer NOT NULL DEFAULT 4 CHECK (link_hours BETWEEN 1 AND 4)
);
GRANT SELECT ON platform.webhook_endpoints, platform.webhook_settings TO service_worker;
GRANT SELECT, INSERT, UPDATE ON platform.webhook_endpoints, platform.webhook_settings TO platform_admin;
GRANT DELETE ON platform.webhook_endpoints TO platform_admin;
