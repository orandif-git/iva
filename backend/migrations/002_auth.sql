-- Adds sign-in: password hashes, authenticator-app secrets for staff, lockout counters, and the token table.
-- For a database that was created from an older schema.sql. Run once, as the database owner:
--   sudo -u postgres psql -d gateinsight -f backend/migrations/002_auth.sql
-- New databases get this from schema.sql and must not run it.
BEGIN;
ALTER TABLE platform.staff_users
  ADD COLUMN password_hash text,
  ADD COLUMN totp_secret_ciphertext bytea,
  ADD COLUMN totp_last_step bigint NOT NULL DEFAULT 0,
  ADD COLUMN failed_logins integer NOT NULL DEFAULT 0,
  ADD COLUMN locked_until timestamptz;
ALTER TABLE app.tenant_users
  ADD COLUMN password_hash text,
  ADD COLUMN failed_logins integer NOT NULL DEFAULT 0,
  ADD COLUMN locked_until timestamptz;
CREATE TABLE platform.auth_tokens (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind         text NOT NULL CHECK (kind IN ('refresh','invite','reset')),
  account_type text NOT NULL CHECK (account_type IN ('staff','customer')),
  account_id   uuid NOT NULL,
  family       uuid,
  token_hash   text NOT NULL UNIQUE,
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  used_at      timestamptz,
  revoked_at   timestamptz
);
CREATE INDEX auth_tokens_account_idx ON platform.auth_tokens (account_type, account_id, kind);
GRANT SELECT, INSERT, UPDATE ON platform.auth_tokens TO platform_admin;
COMMIT;
