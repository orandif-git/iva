# Jas iVision

Camera intelligence for shops: people counts and vehicle number-plate reading from Dahua recorders, with alerts and summaries sent to the shop on WhatsApp. Our team runs everything from the **Control Center** (`/console`); customers have no sign-in.

- `backend/`: API (FastAPI), recorder registrar, video relay, AI worker. Start with `backend/README.md`.
- `backend/CONTROL_CENTER.md`: what the Control Center does, page by page.
- `backend/DEPLOY.md`: setting up a server. `backend/MESSAGING.md`: alerts and WhatsApp webhooks.
- `API.md`: the routes. `schema.sql`: the database. `docs/PUSH.md`: Firebase settings (kept for later).
- Old customer app, Platform Admin and billing are kept in the git branch `archive/legacy-portals`.

Checks: `cd backend && pytest`.
