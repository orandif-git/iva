-- Lets a recorder have a friendly name (shown instead of its id), which customers and staff can rename.
-- For a database that was created from an older schema.sql. Run once, as the database owner:
--   su postgres -c "psql -d gateinsight -f backend/migrations/003_recorder_name.sql"
-- New databases get this from schema.sql and must not run it.
ALTER TABLE app.recorders ADD COLUMN name text;
