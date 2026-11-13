# Jas iVision

- **Product:** the Control Center (`backend/web/console`, served at `/console`, sign-in at `/`) is the only interface. Staff sign in with email and password. Customers get WhatsApp alerts through webhooks.
- **Words:** plain language for shop owners and staff. No setting names, no jargon.
- **Checks:** `pytest` in `backend/`. Old code lives in the `archive/legacy-portals` branch; do not bring it back.
- **Database:** changes go in `backend/migrations/NNN_*.sql` and in `schema.sql`.
- **Front end:** vanilla JS, strict CSP (no inline styles or scripts).
