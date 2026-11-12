-- What each camera's picture usually looks like, so a camera that has been moved, turned or covered, or whose picture has stayed too dark or too blurry, can be noticed.
CREATE TABLE IF NOT EXISTS app.camera_picture_state (
  camera_id       uuid PRIMARY KEY REFERENCES app.cameras(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL REFERENCES app.tenants(id) ON DELETE CASCADE,
  ref_day         bytea,                          -- the outline sketch of the usual view in daylight, and at night (the sketch changes when the camera switches to infrared)
  ref_day_at      timestamptz,
  ref_night       bytea,
  ref_night_at    timestamptz,
  baseline_sharp  real,                           -- how sharp its picture usually is in daylight (a slow average)
  day_samples     integer NOT NULL DEFAULT 0,
  bad_view_since  timestamptz,                    -- since when the view has not matched ('moved') or has been flat ('covered')
  bad_view_kind   text,
  poor_since      timestamptz,                    -- since when the picture has been 'blurry' or 'dark'
  poor_kind       text,
  last_at         timestamptz,
  last_brightness real,
  last_sharpness  real
);
ALTER TABLE app.camera_picture_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.camera_picture_state FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.camera_picture_state USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.camera_picture_state TO app_user, platform_admin, service_worker;
