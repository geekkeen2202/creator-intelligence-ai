---
name: verify
description: Build/launch/drive recipe for verifying backend changes end-to-end against the real Supabase DB + local Redis.
---

# Verifying the Creator Intelligence backend

All commands run from `backend/` using `.venv/bin/*` (Python 3.14 venv, already provisioned).

## Launch (real stack: Supabase Postgres from .env + local Redis)

```bash
lsof -ti:8011 | xargs -r kill -9
.venv/bin/uvicorn app.main:app --port 8011 > /tmp/verify_api.log 2>&1 &
.venv/bin/celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2 > /tmp/verify_worker.log 2>&1 &
```

Use port 8011+ — the user's own dev server may hold 8000. `redis-cli ping` should say PONG (Homebrew Redis).

## Auth handle

Endpoints use Supabase-style HS256 JWTs. Mint one:

```bash
.venv/bin/python -c "
import uuid; from jose import jwt; from app.config import get_settings
uid = str(uuid.uuid4())
print(uid, jwt.encode({'sub': uid, 'email': 'v@example.com'}, get_settings().supabase_jwt_secret, algorithm='HS256'))"
```

The user row must exist first (channels FK): insert into `users` via a quick SQLAlchemy `text()` script, or reuse the seeded verify user `1d41e1cd-1bb7-491a-9ce1-b0ad93af41a3` (owns channel `04e1c3bb-...` with a real extracted Voice DNA profile).

## Flows worth driving

- `POST /api/v1/channels` → watch `/tmp/verify_worker.log` for `ingest_channel → transcribe_video ×N → extract_voice_profile` chord; then `videos`/`transcripts`/`voice_profiles` rows + `channels.current_voice_profile_id` pointer.
- `POST /api/v1/scripts` → check provenance columns on the row; hook/body should echo the channel's Voice DNA signature phrases.
- `GET /api/v1/scripts/{id}/stream` → word-by-word SSE, ends with `data: [DONE]`.
- `POST /api/v1/thumbnails` from a script; `POST /scripts/{id}/publish` → `link_script_outcome` task → `script_outcomes` row.
- Cross-user probes: second JWT must get 404 on stream/publish/thumbnails of another user's script.
- `events` table should gain a row per emitted event.

## Gotchas

- **Free-tier OpenRouter (`openrouter/free`) is flaky on structured JSON** — ~1/3 of generate calls return 502 "unparseable output — please retry". Retry; not a code bug.
- Celery task retry backoff is 180s — don't wait for a stale retry; drive the service function directly (import module models for `users`/`channels` first or the mapper fails with NoReferencedTableError).
- Read-only GET routes (`/voice-profiles/{id}`, `/channels/{id}`, `/analytics/{id}`) are deliberately unauthenticated for now (user decision, deferred).
- Standalone scripts using ORM models must import `app.modules.users.models` + `app.modules.channels.models` (and any FK-referenced module) before creating sessions.
