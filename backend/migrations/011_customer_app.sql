-- The new customer app: an alert can be snoozed (hidden from "Needs you" for a while) as well as marked done (acknowledged).
ALTER TABLE app.alerts ADD COLUMN snoozed_until timestamptz;
