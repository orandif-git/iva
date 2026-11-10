-- Scheduled reports are announced on WhatsApp (a "report ready" webhook event with a link to the file), not emailed.
-- Report emails that were still waiting are cancelled; nothing else changes in the database.
UPDATE app.notifications SET status='cancelled', error='Reports are sent by WhatsApp now.', locked_until=NULL WHERE report_id IS NOT NULL AND channel='email' AND status='queued';
