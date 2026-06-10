# Canvas Helper

Local-first Canvas course material manager. The backend syncs Canvas data through a read-only client, stores it in SQLite, downloads course files on demand, extracts searchable text, and serves a React frontend for browsing courses, timelines, files, announcements, assignments, people, settings, and logs.

## Features

- Read-only Canvas API access. Canvas credentials stay on the backend.
- Local SQLite cache for courses, announcements, assignments, calendar events, pages, people, and file indexes.
- Course file backup with previous-copy restore on failed refresh, ZIP download, preview, text extraction, and OCR-assisted parsing.
- Structured timeline from synced assignments, calendar events, and announcements.
- Agent chat backed by an OpenAI-compatible model provider, with local Canvas cache tools.
- Runtime settings for Canvas token, Agent provider, sync schedule, OCR, Telegram, and email notifications.
- Event logs for sync, file, announcement, assignment, Agent, and notification activity.

The course AI analysis workflow has been removed. Generated course analysis routes and analysis result storage are intentionally unavailable; the Agent chat module remains available.

## Requirements

- Python 3.11+
- Node.js LTS and npm
- Optional: Tesseract OCR for scanned PDFs/images

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm install
npm --prefix frontend install
Copy-Item .env.example .env
```

Edit `.env` and set:

```dotenv
CANVAS_BASE_URL=https://your-school.instructure.com/
CANVAS_API_TOKEN=your_canvas_token
OPENAI_COMPAT_BASE_URL=https://api.openai.com/v1
OPENAI_COMPAT_API_KEY=your_model_provider_key
OPENAI_COMPAT_MODEL=gpt-4.1-mini
```

## Run

```powershell
.\start.ps1
```

Or run the development servers manually:

```powershell
npm run dev
```

Default URLs:

- Frontend: http://127.0.0.1:5173
- Backend health check: http://127.0.0.1:8000/api/health

## Test

```powershell
pip install -r requirements-dev.txt
pytest
npm --prefix frontend run build
```

## Documentation

See [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md) for architecture, API contracts, data model, and security boundaries.

## Security

Do not commit `.env`, local databases, logs, downloaded course material, virtual environments, `node_modules`, or build output. These are ignored by default.
