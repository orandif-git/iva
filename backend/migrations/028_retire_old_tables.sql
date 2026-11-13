-- Retires the tables of the old Platform Admin and customer app. Nothing in the product reads them any more.
-- They are RENAMED, not dropped, so this can be undone (see the end of this file). A later migration drops them after a quiet week.
-- Take a backup first:  su postgres -c "pg_dump -Fc gateinsight > /root/gateinsight-before-028.dump"

-- The old tables must not block anything that is still used (for example deleting a customer): remove their links to customers and staff.
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.conrelid::regclass AS tbl, c.conname FROM pg_constraint c
    WHERE c.contype = 'f' AND c.conrelid::regclass::text IN
      ('platform.impersonation_sessions','app.invoices','app.invoice_lines','app.usage_daily','app.live_sessions','platform.partner_users')
  LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
  END LOOP;
END $$;

-- No customer has a partner (checked before writing this), so the column holds nothing.
ALTER TABLE app.tenants DROP COLUMN IF EXISTS partner_id;

ALTER TABLE IF EXISTS platform.impersonation_sessions RENAME TO zz_old_impersonation_sessions;
ALTER TABLE IF EXISTS platform.partner_users          RENAME TO zz_old_partner_users;
ALTER TABLE IF EXISTS platform.partners               RENAME TO zz_old_partners;
ALTER TABLE IF EXISTS platform.billing_settings       RENAME TO zz_old_billing_settings;
ALTER TABLE IF EXISTS platform.invoice_counters       RENAME TO zz_old_invoice_counters;
ALTER TABLE IF EXISTS app.invoice_lines               RENAME TO zz_old_invoice_lines;
ALTER TABLE IF EXISTS app.invoices                    RENAME TO zz_old_invoices;
ALTER TABLE IF EXISTS app.usage_daily                 RENAME TO zz_old_usage_daily;
ALTER TABLE IF EXISTS app.live_sessions               RENAME TO zz_old_live_sessions;

-- To undo (only if something was missed): rename each table back, for example
--   ALTER TABLE app.zz_old_invoices RENAME TO invoices;
-- and so on for the nine tables above. The partner_id column was empty and is not restored.
