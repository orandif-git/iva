-- The pages and addresses anyone on the internet can reach (an evidence link, a report link, a Hikvision recorder's alert address) no longer run with the strongest database role.
-- They use "public_edge": it cannot read any table of a customer. It can only call the few fixed lookups below, each of which hands back exactly what that one page needs.
-- (Raising the alert itself runs as the customer's own restricted role, so one customer's page can never touch another's data.)
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='public_edge') THEN CREATE ROLE public_edge NOLOGIN; END IF; END $$;
GRANT USAGE ON SCHEMA platform TO public_edge;

CREATE OR REPLACE FUNCTION platform.edge_evidence(p_id uuid)
RETURNS TABLE (id uuid, kind text, storage_key text, content_type text, created_at timestamptz, expires_at timestamptz, camera text, title text, body text, timezone text, company text, clip_id uuid, clip_key text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT e.id, e.kind, e.storage_key, e.content_type, e.created_at, e.expires_at, c.name::text, a.title::text, a.body::text, t.timezone::text, t.name::text, cl.id, cl.storage_key
  FROM app.evidence e JOIN app.tenants t ON t.id = e.tenant_id LEFT JOIN app.cameras c ON c.id = e.camera_id
       LEFT JOIN app.alerts a ON a.evidence_id = e.id OR a.clip_id = e.id
       LEFT JOIN app.evidence cl ON cl.id = a.clip_id AND cl.expires_at > now()
  WHERE e.id = p_id AND e.expires_at > now() AND t.status::text NOT IN ('suspended','cancelled')
  LIMIT 1
$$;

CREATE OR REPLACE FUNCTION platform.edge_report(p_id uuid)
RETURNS TABLE (report_type text, period_start timestamptz, period_end timestamptz, file_key text, expires_at timestamptz, timezone text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT r.report_type, r.period_start, r.period_end, r.file_key, r.expires_at, t.timezone::text
  FROM app.reports r JOIN app.tenants t ON t.id = r.tenant_id
  WHERE r.id = p_id AND r.status = 'ready' AND r.expires_at > now() AND t.status::text NOT IN ('suspended','cancelled')
$$;

-- A Hikvision recorder's message arrived with this code: say which link and customer it belongs to, and note that it was heard.
CREATE OR REPLACE FUNCTION platform.edge_hik_touch(p_hash text, p_ip inet)
RETURNS TABLE (id uuid, tenant_id uuid, name text, tenant_status text)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  UPDATE platform.alert_links l SET last_message_at = now(), last_ip = p_ip FROM app.tenants t WHERE l.code_hash = p_hash AND t.id = l.tenant_id
  RETURNING l.id, l.tenant_id, l.name, t.status::text
$$;

CREATE OR REPLACE FUNCTION platform.edge_hik_exists(p_hash text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM platform.alert_links WHERE code_hash = p_hash)
$$;

REVOKE ALL ON FUNCTION platform.edge_evidence(uuid), platform.edge_report(uuid), platform.edge_hik_touch(text, inet), platform.edge_hik_exists(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.edge_evidence(uuid), platform.edge_report(uuid), platform.edge_hik_touch(text, inet), platform.edge_hik_exists(text) TO public_edge;

-- The Hikvision log: the only table the edge role touches directly.
GRANT SELECT, INSERT, DELETE ON platform.alert_link_messages TO public_edge;
GRANT USAGE ON SEQUENCE platform.alert_link_messages_id_seq TO public_edge;
-- The workers never need these tables.
REVOKE ALL ON platform.alert_links, platform.alert_link_messages FROM service_worker;
-- Raising an alert as the customer's own restricted role needs to know whether an alert has a webhook set up (and nothing else about it: not the address, not the keys).
GRANT SELECT (alert_type, active, payload_template) ON platform.webhook_endpoints TO app_user;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gi_api') THEN GRANT public_edge TO gi_api; END IF; END $$;
