-- Evidence storage is set in the owner console, and a plate read keeps its picture.
-- For a database created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -f backend/migrations/005_storage_and_plate_pictures.sql"
-- New databases get this from schema.sql and must not run it.
ALTER TABLE app.plate_events ADD COLUMN evidence_id uuid REFERENCES app.evidence(id) ON DELETE SET NULL;
CREATE TABLE platform.storage_settings (
  id                boolean PRIMARY KEY DEFAULT true CHECK (id),
  backend           text NOT NULL CHECK (backend IN ('local','r2')),
  account_id        text,
  bucket            text,
  endpoint_url      text,
  access_key_id     text,
  secret_ciphertext bytea,
  updated_at        timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON platform.storage_settings TO platform_admin;
GRANT SELECT ON platform.storage_settings TO service_worker;
