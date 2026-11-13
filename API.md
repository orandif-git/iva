# Jas iVision API routes

Generated from the code. All `/admin/v1` routes need a staff token (`Authorization: Bearer ...`) except `/admin/v1/auth/*`. `/internal/v1` routes need the service token. Open `/docs` on a non-production server for details.


## admin_alerts

- `GET /admin/v1/alert-catalog`
- `PUT /admin/v1/alert-catalog/{alert_type}/check`
- `GET /admin/v1/platform-faults`
- `PUT /admin/v1/platform-faults`
- `PUT /admin/v1/alert-catalog/{alert_type}`

## admin_framerate

- `GET /admin/v1/tenants/{tenant_id}/frame-rate`
- `PUT /admin/v1/tenants/{tenant_id}/frame-rate`
- `GET /admin/v1/tenants/{tenant_id}/cameras`
- `PUT /admin/v1/cameras/{camera_id}/frame-rate`
- `DELETE /admin/v1/cameras/{camera_id}/frame-rate`
- `PUT /admin/v1/cameras/{camera_id}/source`
- `DELETE /admin/v1/cameras/{camera_id}/source`

## admin_maintenance

- `POST /admin/v1/maintenance/retention/run`
- `GET /admin/v1/maintenance/keep`
- `GET /admin/v1/maintenance/keep/preview`
- `PUT /admin/v1/maintenance/keep`

## admin_messaging

- `GET /admin/v1/messaging/tenants/{tenant_id}`
- `PUT /admin/v1/messaging/tenants/{tenant_id}`
- `GET /admin/v1/messaging/notifications`
- `GET /admin/v1/messaging/log`
- `GET /admin/v1/messaging/summary`

## admin_misc

- `GET /admin/v1/me`
- `PATCH /admin/v1/me`
- `POST /admin/v1/me/change-password`
- `POST /admin/v1/me/sign-out-others`
- `GET /admin/v1/overview`
- `GET /admin/v1/audit`

## admin_plans

- `GET /admin/v1/plans`
- `POST /admin/v1/plans`
- `PUT /admin/v1/plans/{plan_id}`
- `DELETE /admin/v1/plans/{plan_id}`

## admin_recorders

- `POST /admin/v1/tenants/{tenant_id}/recorders`
- `GET /admin/v1/recorders`
- `GET /admin/v1/recorders/{recorder_id}/connection`
- `PATCH /admin/v1/recorders/{recorder_id}`
- `POST /admin/v1/recorders/{recorder_id}/new-password`
- `GET /admin/v1/recorders/{recorder_id}/delete-preview`
- `DELETE /admin/v1/recorders/{recorder_id}`

## admin_shop

- `GET /admin/v1/tenants/{tenant_id}/shop/hours`
- `PUT /admin/v1/tenants/{tenant_id}/shop/hours`
- `GET /admin/v1/tenants/{tenant_id}/shop/people`
- `POST /admin/v1/tenants/{tenant_id}/shop/people`
- `PATCH /admin/v1/tenants/{tenant_id}/shop/people/{rid}`
- `DELETE /admin/v1/tenants/{tenant_id}/shop/people/{rid}`
- `GET /admin/v1/tenants/{tenant_id}/shop/events`
- `PUT /admin/v1/tenants/{tenant_id}/shop/events/{rule_type}`
- `DELETE /admin/v1/tenants/{tenant_id}/shop/events/{rule_type}`
- `POST /admin/v1/tenants/{tenant_id}/shop/events/{rule_type}/test`
- `GET /admin/v1/tenants/{tenant_id}/shop/area-kinds`
- `GET /admin/v1/tenants/{tenant_id}/shop/cameras/{camera_id}/areas`
- `PUT /admin/v1/tenants/{tenant_id}/shop/cameras/{camera_id}/areas`
- `GET /admin/v1/tenants/{tenant_id}/shop/recorders/{recorder_id}/channels`
- `GET /admin/v1/tenants/{tenant_id}/shop/cameras`
- `POST /admin/v1/tenants/{tenant_id}/shop/cameras`
- `PATCH /admin/v1/tenants/{tenant_id}/shop/cameras/{camera_id}`
- `GET /admin/v1/tenants/{tenant_id}/shop/recorders/{recorder_id}/channels/{channel_no}/snapshot`
- `GET /admin/v1/tenants/{tenant_id}/shop/cameras/{camera_id}/zones`
- `PUT /admin/v1/tenants/{tenant_id}/shop/cameras/{camera_id}/zones`
- `GET /admin/v1/tenants/{tenant_id}/shop/cameras/{camera_id}/snapshot`
- `GET /admin/v1/tenants/{tenant_id}/shop/vehicle-lists`
- `POST /admin/v1/tenants/{tenant_id}/shop/vehicle-lists`
- `DELETE /admin/v1/tenants/{tenant_id}/shop/vehicle-lists/{entry_id}`

## admin_staff

- `GET /admin/v1/staff`
- `POST /admin/v1/staff`
- `PATCH /admin/v1/staff/{sid}`
- `DELETE /admin/v1/staff/{sid}`
- `POST /admin/v1/staff/{sid}/disable`
- `POST /admin/v1/staff/{sid}/enable`
- `POST /admin/v1/staff/{sid}/reset-link`

## admin_storage

- `GET /admin/v1/storage`
- `POST /admin/v1/storage/test`
- `PUT /admin/v1/storage`

## admin_summaries

- `GET /admin/v1/summaries`
- `PUT /admin/v1/summaries`
- `PUT /admin/v1/summaries/key`
- `POST /admin/v1/summaries/test`

## admin_system

- `GET /admin/v1/system/health`

## admin_tenants

- `GET /admin/v1/tenants`
- `POST /admin/v1/tenants`
- `GET /admin/v1/tenants/{tenant_id}`
- `PATCH /admin/v1/tenants/{tenant_id}`
- `POST /admin/v1/tenants/{tenant_id}/suspend`
- `POST /admin/v1/tenants/{tenant_id}/reactivate`
- `DELETE /admin/v1/tenants/{tenant_id}`

## admin_views

- `GET /admin/v1/tenants/{tenant_id}/shop/views`
- `GET /admin/v1/tenants/{tenant_id}/shop/people-count`
- `GET /admin/v1/tenants/{tenant_id}/shop/people-count/export`
- `GET /admin/v1/tenants/{tenant_id}/shop/vehicles`
- `GET /admin/v1/tenants/{tenant_id}/shop/vehicles/export`

## admin_webhooks

- `GET /admin/v1/webhooks`
- `POST /admin/v1/webhooks`
- `POST /admin/v1/webhooks/standard`
- `GET /admin/v1/webhooks/settings`
- `PUT /admin/v1/webhooks/settings`
- `POST /admin/v1/webhooks/settings/secret`
- `GET /admin/v1/webhooks/deliveries`
- `POST /admin/v1/webhooks/deliveries/{notification_id}/resend`
- `GET /admin/v1/webhooks/connection`
- `PUT /admin/v1/webhooks/connection`
- `POST /admin/v1/webhooks/connection/apply`
- `POST /admin/v1/webhooks/connection/test`
- `POST /admin/v1/webhooks/key`
- `PUT /admin/v1/webhooks/{endpoint_id}`
- `DELETE /admin/v1/webhooks/{endpoint_id}`
- `POST /admin/v1/webhooks/{endpoint_id}/send-now`

## auth

- `POST /admin/v1/auth/login`
- `POST /admin/v1/auth/refresh`
- `POST /admin/v1/auth/logout`
- `POST /admin/v1/auth/set-password`

## internal

- `GET /internal/v1/recorders/{device_id}/credentials`
- `POST /internal/v1/recorders/{device_id}/heartbeat`
- `POST /internal/v1/recorders/{device_id}/health`
- `POST /internal/v1/recorders/{device_id}/alarms`
- `POST /internal/v1/recorders/{device_id}/offline`
- `POST /internal/v1/recorders/{device_id}/preview-ok`
- `GET /internal/v1/workers/cameras`
- `GET /internal/v1/cameras/{camera_id}/config`
- `GET /internal/v1/cameras/{camera_id}/source`
- `POST /internal/v1/cameras/{camera_id}/frame-status`
- `GET /internal/v1/cameras/{camera_id}/ai-view`
- `POST /internal/v1/cameras/{camera_id}/ai-frame`

## internal_events

- `POST /internal/v1/events/footfall`
- `POST /internal/v1/events/plates`
- `POST /internal/v1/events/pictures`
- `POST /internal/v1/events/zones`

## internal_evidence

- `POST /internal/v1/evidence`
- `POST /internal/v1/alerts/{alert_id}/clip`
