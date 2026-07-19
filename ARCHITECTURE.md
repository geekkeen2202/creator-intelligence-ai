# Architecture Reference — Creator Intelligence Platform

> **Purpose of this document:** Single source of truth for the system architecture.
> Claude Code and all developers MUST follow these rules when adding or modifying code.
> If a change violates a rule here, fix the design — not the rule.
> Build sequencing, milestone targets, and exit criteria live in `VOXA_TECHNICAL_DESIGN.md`.

---

## 1. System Overview

AI-powered SaaS for Indian content creators. Core loop: trending topics →
script in creator's voice (Voice DNA) → teleprompter → analytics → sponsorship marketplace.
Post-publish, the loop closes: analytics sync → script outcome linkage → Voice DNA refinement.

**Architecture style: Modular Monolith + Event-Driven + Ports & Adapters.**
One deployable backend. Isolated feature modules. Communication via events.
External services behind adapter interfaces. NOT microservices (until a module
measurably needs independent scaling).

## 2. Tech Stack (LOCKED)

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind, Shadcn/ui, Zustand, Framer Motion |
| Backend | FastAPI, Python 3.12, Uvicorn + Gunicorn |
| AI Layer | Agno SDK + AgentOS runtime, OpenRouter → Claude (claude-sonnet-4-6) |
| Database | Supabase PostgreSQL, SQLAlchemy 2.0 (async), Alembic migrations, asyncpg |
| Cache | Upstash Redis (cache, rate limits, Celery broker) |
| Background jobs | Celery + Redis, Celery Beat (schedules), Flower (monitoring) |
| External APIs | YouTube Data API v3, pytrends, praw (Reddit), httpx |
| Transcription | YouTube captions first; Whisper fallback behind `TranscriptionPort` |
| Auth | Supabase Auth (frontend), python-jose JWT validation (backend) |
| Payments | Razorpay (India). Stripe added Year 2 via adapter — no logic changes |
| Email | Resend + Jinja2 templates |
| Observability | Sentry, structlog (structured logging), Agno built-in tracing |
| Hosting | Vercel (frontend), Railway (backend + workers), Docker Compose (local) |
| Embeddings | **Deferred to post-MVP.** Schema ships embedding-ready (nullable `vector` columns); no retrieval code before the embeddings milestone |

## 3. High-Level Diagram

```
Clients (Next.js web, future mobile)
        │  HTTPS / SSE (browser → FastAPI DIRECT for intelligence endpoints)
        ▼
API Gateway (FastAPI /api/v1/*, auth middleware, Redis rate limiting)
        ▼
Feature Modules (vertical slices — see §4)
   channels │ trending │ scripts │ thumbnails │ analytics │ billing │ notifications
   (future: calendar │ marketplace)
        │                          │
        ▼                          ▼
   Event Bus ──► Celery      AI Platform (Agno)
   (internal events           Agent Registry: voice_dna, trending,
    become tasks)             script, thumbnail_brief,
        │                     script_team (flagged OFF — see §7)
        ▼                          │
              Adapters (Ports layer — §6)
   YouTubeAdapter · TrendsAdapter · RedditAdapter · WhisperAdapter
   PaymentAdapter · EmailAdapter · LLMAdapter
        ▼
Data Layer: Supabase Postgres (truth) · Upstash Redis (cache) · Supabase Storage (files)
```

### Frontend/backend boundary (binding)

1. **Write ownership.** FastAPI owns ALL writes to intelligence tables
   (`transcripts`, `transcript_segments`, `voice_profiles`, `scripts`,
   `script_outcomes`, `thumbnail_briefs`, `trending_topics`, `events`).
   Next.js owns product-table writes (user profile fields, UI preferences).
   Either side may read anything through its own layer.
2. **Direct calls.** The browser calls FastAPI directly for intelligence endpoints
   (script generation SSE, onboarding status, trend feed). Next.js API routes exist
   only where they add value (page data assembly, Razorpay webhooks, session logic).
   No pass-through proxying.
3. **Generated contract.** The frontend TypeScript client is generated from FastAPI's
   OpenAPI schema in CI. Hand-written duplicate API types are a defect.

## 4. Feature Modules (Vertical Slices)

Every feature is a self-contained folder under `backend/app/modules/`:

```
modules/<feature>/
├── router.py        # FastAPI endpoints (thin — no business logic)
├── service.py       # business logic (the only place logic lives)
├── repository.py    # ALL database queries for this module
├── models.py        # SQLAlchemy models owned by this module
├── schemas.py       # Pydantic request/response schemas
├── events.py        # events this module EMITS (documented constants)
└── agents.py        # Agno agents this module owns (if any)
```

### HARD RULES
1. A module NEVER imports another module's `service.py` or `repository.py`.
2. Cross-module communication happens ONLY via:
   a) the event bus (preferred, async), or
   b) another module's explicitly exported public interface (`__init__.py` exports).
3. Only `repository.py` touches the database. Services call repositories.
4. Routers are thin: parse → call service → return schema. No logic in routers.
5. Adding a feature = adding a new module folder. Existing modules must not change.
6. If a new feature requires editing another module's internals, the boundary is
   wrong — raise it, don't hack it.
7. **Ownership checks live in repositories.** Every repository method that reads or
   writes user-owned data filters by the authenticated user/channel. Every repository
   ships a cross-user access-denial test. (Compensating control for deferred RLS — §8.4.)

## 5. Event Bus (Extensibility Backbone)

Modules emit domain events; subscribers (Celery tasks) react. Emitters never know
who listens. New features subscribe to existing events instead of modifying emitters.

```python
# shared/events.py — emit
event_bus.emit("channel.analyzed", {"user_id": ..., "channel_id": ..., "voice_profile_version": 1})

# any module — subscribe (runs as Celery task)
@subscribe("channel.analyzed")
def warm_trending_cache(payload): ...
```

### Canonical events (define from Day 1)
```
user.registered            channel.connected          channel.analyzed
transcripts.completed      voice_profile.updated
script.generated           script.rated               script.published
script.outcome_linked      topic.bookmarked           trending.refreshed
subscription.started       subscription.cancelled     subscription.payment_failed
analytics.synced
```
Event payloads are versioned, additive-only (never remove/rename fields).
Every emitted event is also appended to the `events` table as an **audit-only** log.
Voice DNA refinement does not replay events from this table — it reads ratings,
edits, and outcome view-counts directly off `scripts`/`script_outcomes` (see
`voice_profiles.service.refine_profile`), which is simpler than event-sourcing
the same data and avoids maintaining two paths to the same signal. Revisit this
choice only if a feature actually needs event replay (e.g. rebuilding derived
state), not preemptively.

## 6. Ports & Adapters (External Dependencies)

Every external service sits behind a Protocol interface in `shared/ports/`.
Business logic depends on the port, never the vendor SDK.

```python
class PaymentPort(Protocol):
    async def create_subscription(self, user, plan) -> Subscription: ...
    async def cancel(self, sub_id: str) -> None: ...
# adapters/razorpay_adapter.py implements it today
# adapters/stripe_adapter.py added Year 2 — zero business-logic changes
```

Required ports:
- `LLMPort`             → OpenRouterAdapter (Claude claude-sonnet-4-6)
- `TrendSourcePort`     → YouTubeTrendsAdapter, GoogleTrendsAdapter, RedditAdapter
- `SocialPlatformPort`  → YouTubeAdapter (InstagramAdapter, FacebookAdapter in V2)
- `TranscriptionPort`   → WhisperAdapter (fallback when caption quality gate fails)
- `PaymentPort`         → RazorpayAdapter (StripeAdapter Year 2)
- `EmailPort`           → ResendAdapter

Rationale: platform-risk mitigation (e.g., YouTube API policy change or caption
access tightening = swap/extend one adapter, not the product) and vendor swap
without surgery.

## 7. AI Platform (Agno)

All agents live in a central registry. Adding an agent = registry entry + agent file.

```python
# ai/registry.py
AGENT_REGISTRY = {
    "voice_dna":       build_voice_dna_agent,     # transcript batches → confidence-tiered profile
    "trending":        build_trending_agent,       # multi-source trend research
    "script":          build_script_agent,         # generation (all tiers, MVP)
    "thumbnail_brief": build_thumbnail_brief_agent,# script hook → text brief JSONB (no images)
    "script_team":     build_premium_script_team,  # FLAGGED OFF — see rule below
    # future: "trend_predictor", "calendar_planner"
}
```

Rules:
- Agents use structured outputs (Pydantic response models) — never parse raw text.
- **Every agent definition carries a `version` identifier.** Every profile and script
  row records the agent/prompt versions that produced it (see §8 provenance).
  Agno tracing shows what happened; version stamps define what to compare.
- Script generation streams via SSE directly to the browser (word-by-word UX).
- Agent memory/session state persists in Postgres via Agno storage.
- **Voice DNA lives in the `voice_profiles` table (append-only versions), NOT as a
  column on `channels`.** `channels.current_voice_profile_id` is a pointer cache only.
  Agents read the current profile + curated excerpts as context.
- **Confidence-aware prompting:** low-confidence profile dimensions fall back to
  neutral defaults. Never fabricate catchphrases for a low-confidence dimension.
- Prompt templates are versioned files under `ai/prompts/`; the profile block is
  cached in Redis and invalidated on `voice_profile.updated`.
- Rate-limit generation per user per tier in Redis (protect Claude API costs).
- **`script_team` ships behind a feature flag, disabled.** Enablement requires a
  measured quality win (ratings + voice-match score) over the single agent.
  Until then, premium tiers differentiate on limits and features, not pipeline.

## 8. Database Rules

1. New features get NEW tables — never widen existing tables for a new feature.
2. Evolving/flexible data uses JSONB columns (e.g., `voice_profiles.profile JSONB`).
3. Every table: `id uuid PK`, `created_at`, `updated_at`, soft-delete `deleted_at`.
4. **Not yet implemented**: Row Level Security. Authorization is enforced at the
   application layer — the backend connects as a single shared DB role, so real RLS
   would require rearchitecting Postgres authentication. **Compensating controls are
   mandatory, not optional** (§4 rule 7): repository-level ownership checks + a
   cross-user denial test per repository. Revisit real RLS before the agency /
   multi-channel tier or any compliance requirement.
5. Index every column used in WHERE/ORDER BY at scale
   (`user_id`, `channel_id`, `created_at DESC`, `(niche, language)`,
   partial index on `trending_topics.expires_at`).
6. **Not yet implemented**: `analytics_daily` partitioning. Revisit when row volume warrants.
7. Schema changes only via Alembic migrations — never manual.
8. **Append-only profile versions:** `voice_profiles` rows are never updated in place.
   Refinement inserts version+1. Rollback = repoint `current_voice_profile_id`.
9. **Provenance is NOT NULL:** every `scripts` row records
   `voice_profile_version`, `agent_name`, `agent_version`, `prompt_version`,
   `model_id`, token counts, and cost. A script without full provenance is a defect.
10. **Embedding-ready, embedding-off:** `transcript_segments.embedding` and
    `trending_topics.embedding` are nullable `vector` columns created in the first
    migration and left NULL until the post-MVP embeddings milestone. No retrieval
    code reads them before then.
11. **Cost metering at source:** Whisper minutes and LLM tokens are written to
    `usage` at the moment they are incurred, tagged by feature. Cost-per-script and
    cost-per-onboarding must be answerable by query.

### Core tables (Day 1)
`users`, `channels` (`current_voice_profile_id` pointer), `videos`
(`selected_for_dna` flag), `transcripts` (source: captions|whisper, quality_score),
`transcript_segments` (typed hook|body|transition|cta, nullable embedding),
`voice_profiles` (append-only: version, profile JSONB, confidence JSONB,
excerpt_ids, extraction_prompt_version), `scripts` (hook/body/cta split, nullable
`topic_id` — freeform topics must work, full provenance, rating, rating_detail,
final_text), `thumbnail_briefs` (brief JSONB — text brief only, no images),
`trending_topics` (with expires_at, nullable embedding), `script_outcomes`
(script ↔ published video: matched_by manual|auto, ctr, avg_view_duration, views),
`events` (append-only log), `subscriptions`, `usage`
### Added later without touching the above
`calendar_entries`, `brands`, `deals`, `deal_applications`, `analytics_daily`

## 9. API Contract

- All endpoints under `/api/v1/`. Breaking changes go to `/api/v2/`; v1 keeps working.
- FastAPI auto-docs at `/docs` are the contract; the TypeScript client is generated
  from the OpenAPI schema in CI (§3 boundary rule 3).
- Responses use Pydantic schemas only — never raw dicts.
- Feature flags (DB table + Redis cache) gate unreleased features per user/percentage.
- Script generation is SSE (`text/event-stream`) from FastAPI to the browser directly.

## 10. Caching Strategy

| Data | TTL | Key pattern |
|---|---|---|
| Trending topics | 24h | `trending:{niche}:{lang}` |
| Channel data | 24h | `channel:{channel_id}` |
| Voice profile prompt block | until `voice_profile.updated` | `promptblock:{channel_id}` |
| Rate limits | 1min windows | `ratelimit:{user_id}:{action}` |
| Feature flags | 5min | `flags:{flag_name}` |

Never call external APIs on page load. Background jobs pre-fetch; requests read cache/DB.
Redis is always a cache or broker — nothing correct lives only in Redis.

## 11. Background Jobs (Celery)

| Job | Trigger |
|---|---|
| ingest_channel (fan-out chain) | event: channel.connected |
| transcribe_video (per-video, captions → Whisper fallback) | fan-out from ingest_channel |
| extract_voice_profile | event: transcripts.completed |
| refresh_trending_topics | Celery Beat, every 6h, per-source isolation |
| refine_voice_profile | Celery Beat, weekly per active creator (skip if no new signal) |
| sync_analytics | Celery Beat, daily per connected account |
| link_script_outcome | event: script.published / analytics.synced |
| send_weekly_briefing | Celery Beat, Monday mornings |
| handle_payment_webhook | Razorpay webhook → task |

Job rules:
- Jobs are idempotent (safe to retry); acks-late + dead-letter queue configured.
- **Fan-out isolation:** one failed video task never fails channel onboarding; the
  chain completes with the minimum viable corpus (as few as 2–3 usable transcripts
  → low-confidence profile). Progressive status exposed via onboarding status endpoint.
- YouTube quota handling is first-class: per-key throttling, degrade to fewer
  videos, never hard-fail onboarding.
- Agno agent calls inside jobs get automatic retries.
- Alert on queue depth (Flower + Sentry).

## 12. Repository Layout

```
repo/
├── ARCHITECTURE.md              ← this file (Claude Code: read first)
├── VOXA_TECHNICAL_DESIGN.md     ← build plan: milestones, exit criteria, blockers
├── docker-compose.yml           ← full local stack in one command
├── frontend/                    ← Next.js app (owner: frontend dev)
│   └── src/{app, components, lib, stores, types}
│       └── lib/api/             ← GENERATED TypeScript client (do not hand-edit)
├── backend/
│   ├── app/
│   │   ├── main.py              ← FastAPI entry, mounts module routers
│   │   ├── config.py            ← pydantic-settings, all env vars
│   │   ├── modules/             ← §4 vertical slices
│   │   ├── shared/
│   │   │   ├── events.py        ← event bus
│   │   │   ├── ports/           ← §6 interfaces
│   │   │   └── database.py      ← async engine/session
│   │   ├── adapters/            ← §6 implementations
│   │   ├── ai/
│   │   │   ├── registry.py      ← §7 agent registry
│   │   │   ├── agents/          ← one file per agent (each with a version)
│   │   │   └── prompts/         ← versioned prompt templates
│   │   └── tasks/               ← Celery app + task modules
│   ├── migrations/              ← Alembic
│   └── tests/                   ← mirrors modules/ structure (+ cross-user denial tests)
└── .github/workflows/           ← CI/CD (incl. OpenAPI → TS client generation)
```

## 13. Checklist for Adding Any New Feature

1. Create `modules/<feature>/` with the standard files.
2. New tables via Alembic migration (never alter unrelated tables).
3. Subscribe to existing events for integration; emit new events for your own.
4. External service needed? Define/extend a port, write an adapter.
5. New AI capability? Add agent file (with version) + registry entry + versioned prompts.
6. Gate behind a feature flag; expose endpoints under `/api/v1/<feature>/`.
7. Repository methods filter by owner; add the cross-user denial test.
8. If output feeds generation or refinement, record provenance versions on every row.
9. Add tests mirroring the module path. Existing tests must not need changes.
10. If step 9 fails (existing tests change), the design violates §4 rules — redesign.

## 14. Explicit Non-Goals (Do NOT do these)

- No microservices split until a module has a proven, measured scaling bottleneck.
- No direct module-to-module service imports.
- No business logic in routers, adapters, or Celery task bodies (tasks call services).
- No raw SQL outside repositories; no DB access outside repositories.
- No parsing free-text LLM output — structured outputs only.
- No calling YouTube/Trends/Reddit/Whisper APIs in request handlers — cache or background only.
- No updating `voice_profiles` rows in place — versions are append-only.
- No script rows without full provenance (profile/agent/prompt/model versions).
- No embeddings/retrieval code before the post-MVP embeddings milestone (columns exist, stay NULL).
- No image generation in the thumbnails module for MVP — text brief JSONB only.
- No enabling `script_team` without a measured quality win over the single agent.
- No hand-written frontend API types — the TypeScript client is generated.
