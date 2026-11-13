-- One shared WhatsApp connection for alerts (address and key), like the one for summaries. An alert that "uses the shared connection" sends there,
-- so a changed key is saved once. Alerts that already have their own address keep it until the owner chooses "Use for all alerts".
CREATE TABLE IF NOT EXISTS platform.alert_connection (
  id             boolean PRIMARY KEY DEFAULT true CHECK (id),
  url            text,
  key_ciphertext bytea,
  key_hint       text,
  updated_at     timestamptz,
  updated_by     text
);
INSERT INTO platform.alert_connection (id) VALUES (true) ON CONFLICT DO NOTHING;
ALTER TABLE platform.webhook_endpoints ADD COLUMN IF NOT EXISTS use_shared boolean NOT NULL DEFAULT false;
GRANT SELECT ON platform.alert_connection TO service_worker;
GRANT SELECT, INSERT, UPDATE ON platform.alert_connection TO platform_admin;
