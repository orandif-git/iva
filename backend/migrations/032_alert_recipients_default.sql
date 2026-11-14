-- An alert nobody has chosen people for goes to everyone of the customer who has a mobile number (until someone chooses). "recipients_set" says whether someone chose.
ALTER TABLE app.alert_rules ADD COLUMN IF NOT EXISTS recipients_set boolean NOT NULL DEFAULT false;
UPDATE app.alert_rules SET recipients_set = true WHERE cardinality(recipients) > 0;          -- already chosen by someone: keep exactly as it is
