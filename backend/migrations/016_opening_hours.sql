-- Opening hours of a customer's premises, in their own time zone. Used by "Counting stopped" (only expected while open) and "After-hours activity" (only while closed).
-- No hours set (both times NULL): neither alert can fire. closed_days: days of the week that are closed all day, 0 = Monday ... 6 = Sunday.
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS open_time time;
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS close_time time;
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS closed_days smallint[] NOT NULL DEFAULT '{}';
