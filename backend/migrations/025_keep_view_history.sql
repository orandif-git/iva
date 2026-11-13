-- Deleting a console user keeps the record of what they looked at: the record keeps their name and loses only the link to the account.
ALTER TABLE platform.impersonation_sessions ADD COLUMN IF NOT EXISTS staff_name text;
UPDATE platform.impersonation_sessions s SET staff_name = st.name FROM platform.staff_users st WHERE st.id = s.staff_id AND s.staff_name IS NULL;
ALTER TABLE platform.impersonation_sessions ALTER COLUMN staff_id DROP NOT NULL;
ALTER TABLE platform.impersonation_sessions DROP CONSTRAINT IF EXISTS impersonation_sessions_staff_id_fkey;
ALTER TABLE platform.impersonation_sessions ADD CONSTRAINT impersonation_sessions_staff_id_fkey FOREIGN KEY (staff_id) REFERENCES platform.staff_users(id) ON DELETE SET NULL;
