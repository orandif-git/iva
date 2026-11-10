# Jas iVision

Camera intelligence as a service: people footfall and vehicle number-plate reading from recorders that connect to a cloud server, with alerts to WhatsApp and email, Excel reports and an admin console.

- `backend/`: API (FastAPI), recorder registrar (GB28181), video relay, AI worker. Start with `backend/README.md`.
- `schema.sql`: the PostgreSQL schema, `API.md`: the route plan.
- `backend/DEPLOY.md`: checklist for a first real-recorder test on a VPS.
- `backend/MESSAGING.md`: System Alerts and Smart Alerts, who sees which, webhook delivery to WhatsApp, video clips.
- `mockups/`: interactive mock-ups of the customer app, the admin console and recorder registration (open the HTML files in a browser).
- `docs/decisions/`: product decisions and why they were made. Start with `0001-dahua-only.md` (the product connects Dahua recorders only).

Status: tested against stand-ins only (553 automated tests passing, 1 skipped); not yet run against a real recorder. There is no customer sign-in yet.
