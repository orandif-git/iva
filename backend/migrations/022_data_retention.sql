-- How long Jas iVision keeps customer data (alerts and their messages, people counts, plate reads, pictures and clips). One setting for every customer.
CREATE TABLE IF NOT EXISTS platform.data_retention (
  id         boolean PRIMARY KEY DEFAULT true CHECK (id),
  keep_days  integer NOT NULL DEFAULT 30 CHECK (keep_days BETWEEN 7 AND 730),
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by text
);
INSERT INTO platform.data_retention (id) VALUES (true) ON CONFLICT DO NOTHING;
GRANT SELECT, INSERT, UPDATE ON platform.data_retention TO platform_admin;
GRANT SELECT ON platform.data_retention TO service_worker;
