-- Sign in with Face ID or a fingerprint (passkeys): one row per phone a person set it up on, and the short-lived one-time challenges the phone must answer.
CREATE TABLE IF NOT EXISTS platform.passkeys (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id      uuid NOT NULL REFERENCES platform.staff_users(id) ON DELETE CASCADE,
  credential_id text NOT NULL UNIQUE,                 -- the phone's name for this key (base64url)
  public_key    bytea NOT NULL,                       -- the public half only (an uncompressed P-256 point); the private half never leaves the phone
  sign_count    bigint NOT NULL DEFAULT 0,
  name          text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz
);
CREATE INDEX IF NOT EXISTS passkeys_staff_idx ON platform.passkeys (staff_id);
CREATE TABLE IF NOT EXISTS platform.passkey_challenges (
  challenge  text PRIMARY KEY,
  purpose    text NOT NULL CHECK (purpose IN ('register','login')),
  staff_id   uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.passkeys, platform.passkey_challenges TO platform_admin;
