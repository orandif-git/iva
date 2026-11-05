# Jas iVision

Camera intelligence as a service: people footfall and vehicle number-plate reading from recorders that connect to a cloud server, with alerts to WhatsApp and email, Excel reports and an admin console.

- `backend/`: API (FastAPI), recorder registrar (GB28181), video relay, AI worker. Start with `backend/README.md`.
- `schema.sql`: the PostgreSQL schema, `API.md`: the route plan.
- `backend/DEPLOY.md`: checklist for a first real-recorder test on a VPS.
- `backend/MESSAGING.md`: WhatsApp and email alert delivery, templates, video clips.
- `mockups/`: interactive mock-ups of the customer app, the admin console and recorder registration (open the HTML files in a browser).

Status: tested against stand-ins only (553 automated tests passing, 1 skipped); not yet run against a real recorder. There is no customer sign-in yet.
