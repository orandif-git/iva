-- Lets a camera be created from an RTSP address instead of a registered recorder's channel.
-- For a database that was created from an older schema.sql. Run once, as the database owner:
--   sudo -u postgres psql -d gateinsight -f backend/migrations/001_rtsp_cameras.sql
-- New databases get this from schema.sql and must not run it.
BEGIN;
ALTER TABLE app.cameras ADD COLUMN connection text NOT NULL DEFAULT 'recorder' CHECK (connection IN ('recorder','rtsp'));
ALTER TABLE app.cameras ALTER COLUMN recorder_id DROP NOT NULL;
ALTER TABLE app.cameras ALTER COLUMN channel_no DROP NOT NULL;
ALTER TABLE app.cameras ADD CONSTRAINT cameras_kind_check CHECK (
  (connection = 'recorder' AND recorder_id IS NOT NULL AND channel_no IS NOT NULL) OR (connection = 'rtsp' AND recorder_id IS NULL AND channel_no IS NULL));
COMMIT;
