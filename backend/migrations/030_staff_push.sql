-- Notifications on a console user's phone (Web Push): one row per phone or browser that turned them on, what each person chose to hear about, and what the watcher last saw
-- (so a problem is announced once, not every minute).
CREATE TABLE IF NOT EXISTS platform.push_subscriptions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id    uuid NOT NULL REFERENCES platform.staff_users(id) ON DELETE CASCADE,
  endpoint    text NOT NULL UNIQUE,
  p256dh      text NOT NULL,
  auth        text NOT NULL,
  device      text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_ok_at  timestamptz,
  failures    integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS push_subscriptions_staff_idx ON platform.push_subscriptions (staff_id);
ALTER TABLE platform.staff_users ADD COLUMN IF NOT EXISTS push_prefs jsonb;           -- null: the defaults for the person's role
CREATE TABLE IF NOT EXISTS platform.push_state (
  key    text PRIMARY KEY,
  value  text NOT NULL,
  at     timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.push_subscriptions, platform.push_state TO platform_admin;
