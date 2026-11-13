-- Jas iVision Control Center: two more staff roles, a service end date, different opening hours on different days, and a name for each event.
-- Run each statement on its own (psql -f does), because a new enum value cannot be used in the same transaction that adds it.
ALTER TYPE platform.staff_role ADD VALUE IF NOT EXISTS 'admin';
ALTER TYPE platform.staff_role ADD VALUE IF NOT EXISTS 'onboarding';
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS day_hours jsonb;
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS ends_on date;
ALTER TABLE app.alert_rules ADD COLUMN IF NOT EXISTS label text;
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS msg_language text NOT NULL DEFAULT 'en+ta' CHECK (msg_language IN ('en','ta','en+ta'));
