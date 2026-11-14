-- Hikvision alerts: a recorder that cannot connect like a Dahua one sends its fault messages to a private address on our server.
-- One row per recorder. Only a one-way fingerprint of the secret code in that address is kept, so a copy of the database cannot be used to send fake alerts.
CREATE TABLE IF NOT EXISTS platform.alert_links (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  name            text NOT NULL,
  code_hash       text NOT NULL UNIQUE,
  last_message_at timestamptz,
  last_ip         inet,
  created_by      uuid,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alert_links_tenant_idx ON platform.alert_links (tenant_id);
-- Everything a recorder told us (apart from its "I am alive" signals), and what we did about it. Kept for 30 days.
CREATE TABLE IF NOT EXISTS platform.alert_link_messages (
  id          bigserial PRIMARY KEY,
  link_id     uuid NOT NULL REFERENCES platform.alert_links(id) ON DELETE CASCADE,
  received_at timestamptz NOT NULL DEFAULT now(),
  event_type  text NOT NULL,
  state       text,
  channel     integer,
  event_time  text,
  result      text NOT NULL,
  raw         text
);
CREATE INDEX IF NOT EXISTS alert_link_messages_idx ON platform.alert_link_messages (link_id, received_at DESC);
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.alert_links, platform.alert_link_messages TO platform_admin, service_worker;
GRANT USAGE ON SEQUENCE platform.alert_link_messages_id_seq TO platform_admin, service_worker;
