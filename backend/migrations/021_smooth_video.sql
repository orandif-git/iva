-- Smooth live video (WebRTC through go2rtc) is a switch for each customer, off for everyone. Each smooth viewer costs the server a video encode, so staff
-- choose which customers have it, and the server caps how many run at once (GI_SMOOTH_VIDEO_MAX). Run once, as the database owner.
ALTER TABLE app.tenants ADD COLUMN IF NOT EXISTS smooth_video boolean NOT NULL DEFAULT false;
-- Which live views are really using smooth video, so the cap can count them.
ALTER TABLE app.live_sessions ADD COLUMN IF NOT EXISTS smooth boolean NOT NULL DEFAULT false;
