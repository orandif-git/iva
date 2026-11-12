-- What a recorder last reported about itself: its disks, its clock, and whether its channels are saving video. Read by the Dahua gateway every ten minutes.
ALTER TABLE app.recorders ADD COLUMN IF NOT EXISTS health jsonb;
ALTER TABLE app.recorders ADD COLUMN IF NOT EXISTS health_at timestamptz;
