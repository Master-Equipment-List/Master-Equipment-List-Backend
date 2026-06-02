# Master Equipment List (MEL) — Backend

REST API backend for managing Topside / Marine equipment lists for offshore FPSO
projects, with project-scoped OneDrive sync, multi-format file ingestion
(PDF / Excel / CSV / Images / Scanned PDFs), automatic equipment data updates
from PFD and Vendor Data sources, version control, JWT auth, and granular
project-level access control.

## Stack

- Python 3.11+
- FastAPI + Pydantic v2
- PostgreSQL 13+ via SQLAlchemy 2.0 (async) + Alembic
- JWT auth (access + refresh)
- Microsoft Graph (MSAL) for OneDrive
- pdfplumber + Tesseract OCR for PDFs and scanned documents
- openpyxl + pandas for Excel

## Quick start

```bash
# 1. Create virtualenv
python -m venv .venv
. .venv/Scripts/activate     # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# edit .env — DB credentials, JWT secret, MS Graph credentials

# 3. Create DB
createdb mel_db   # or psql -c "CREATE DATABASE mel_db;"

# 4. Migrate
alembic upgrade head

# 5. Create first admin
python -m scripts.create_admin

# 6. Seed POC Topside data
python -m scripts.seed_topside_poc

# 7. Run
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Swagger UI: <http://localhost:8000/docs>

## Project Layout

```
app/
  api/v1/         REST endpoints (auth, users, projects, onedrive, files, equipment, versions, sync)
  core/           security, permissions, audit helpers
  db/             async engine, base, session
  models/         SQLAlchemy ORM models
  schemas/        Pydantic request/response models
  services/       business logic (onedrive, sync, extraction, versioning, audit)
  parsers/        file-type parsers (pdf, excel, csv, image, docx, ocr fallback)
  extractors/     domain extraction (Topside Excel, PFD updates, Vendor Data fields)
  utils/
alembic/          migrations
scripts/          create_admin, seed_topside_poc
```

## Domain rules

- **Project types**: `topside` or `marine` (selected on create).
- **Topside POC data** loaded from
  `20171-SPOG-80000-ME-LS-0001_Z1_Topside Eqipment List.xlsx`.
- **PFD Samples folder** — every synced PDF is parsed; equipment tags found
  in the PFD update matching tags in the project's Topside data
  (creates a new version).
- **Vendor Data folder** — for each `CLIENT EQUIPMENT TAG` in the project,
  every file under Vendor Data is scanned for that tag string; if matched,
  these fields are extracted and applied:
    - ABSORBED POWER PER UNIT (kW)
    - RATED POWER PER UNIT (kW)
    - L or T/T (m)
    - W or I.D (m)
    - H or T/T (m)
    - DRY WEIGHT PER UNIT in MT
    - OPERATING WEIGHT PER UNIT in MT
    - HYDROTEST WEIGHT PER UNIT in MT
- **Version control** — every update creates an `EquipmentVersion` row with
  the prior JSON snapshot, the source (`manual` / `pfd` / `vendor` / `excel`),
  the source file id, and the user id.
- **OneDrive scope** — the application has org-level Graph credentials, but
  per project we only browse / sync inside the configured folder path.

## API surface

| Path | Purpose |
| --- | --- |
| `POST /api/v1/auth/register` | Create user (admin only after bootstrap) |
| `POST /api/v1/auth/login` | Issue JWT access + refresh tokens |
| `POST /api/v1/auth/refresh` | Rotate access token |
| `GET  /api/v1/auth/me` | Current user |
| `GET  /api/v1/users` / role management | Admin user management |
| `POST /api/v1/projects` | Create project (topside/marine + onedrive root) |
| `GET  /api/v1/projects` | List projects user has access to |
| `POST /api/v1/projects/{id}/members` | Grant access (role: viewer/editor/admin) |
| `GET  /api/v1/onedrive/oauth/start` | Begin admin OAuth consent |
| `GET  /api/v1/onedrive/oauth/callback` | OAuth code exchange |
| `GET  /api/v1/projects/{id}/onedrive/browse` | List files / folders within project root |
| `POST /api/v1/projects/{id}/onedrive/selection` | Save selected paths to sync |
| `POST /api/v1/projects/{id}/sync` | Start sync (downloads + parses + extracts) |
| `GET  /api/v1/projects/{id}/files` | Files with name, location, time |
| `GET  /api/v1/projects/{id}/files/{file_id}/data` | JSON contents extracted from file |
| `GET  /api/v1/projects/{id}/equipment` | Table view of equipment |
| `GET  /api/v1/equipment/{id}/versions` | Version history |
| `GET  /api/v1/equipment/{id}/versions/{v}` | View a version |
| `GET  /api/v1/equipment/{id}/diff?from=v1&to=v2` | Compare two versions |
| `GET  /api/v1/projects/{id}/export/excel` | Excel download matching Topside template |
