-- Platform console: two staff roles, plan price-change policy, per-customer kept price.
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -f backend/migrations/004_platform_console.sql"
-- New databases get this from schema.sql and must not run it.
ALTER TABLE platform.plans ADD COLUMN price_change_policy text NOT NULL DEFAULT 'next' CHECK (price_change_policy IN ('next','keep'));
ALTER TABLE app.tenants ADD COLUMN price_override_paise integer CHECK (price_override_paise >= 0);
-- Only two staff roles remain. Existing technician, finance and analyst accounts become support.
UPDATE platform.staff_users SET role='support' WHERE role IN ('technician','finance','analyst');
-- Plans and the messaging setup can now be deleted from the console.
GRANT DELETE ON platform.plans, platform.messaging_providers, platform.message_templates TO platform_admin;
