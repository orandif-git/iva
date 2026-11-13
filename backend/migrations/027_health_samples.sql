-- System health: one row every few minutes (disk, memory, load, cameras) so the Control Center can draw the last day and say how long the disk will last.
CREATE TABLE IF NOT EXISTS platform.health_samples (
  ts                timestamptz PRIMARY KEY DEFAULT now(),
  disk_pct          real,
  mem_pct           real,
  load_pct          real,
  cameras_expected  integer,
  cameras_streaming integer,
  evidence_mb       real,
  db_mb             real
);
GRANT SELECT, INSERT, DELETE ON platform.health_samples TO service_worker;
GRANT SELECT ON platform.health_samples TO platform_admin;
