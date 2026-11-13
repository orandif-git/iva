-- Who owns the customer: the owner's name and email, and up to three owner mobile numbers. A number can belong to one customer only, because it will later be used
-- to sign in with a one-time code. (People who get messages are separate and can share numbers.)
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS owner_name  text;
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS owner_email text;
CREATE TABLE IF NOT EXISTS platform.tenant_owner_phones (
  tenant_id uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  phone     text NOT NULL UNIQUE,
  position  smallint NOT NULL CHECK (position BETWEEN 1 AND 3),
  PRIMARY KEY (tenant_id, position)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.tenant_owner_phones TO platform_admin;
