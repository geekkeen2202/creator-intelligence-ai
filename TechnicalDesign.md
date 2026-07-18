# Technical Design Document

> **Scope:** Complete technical design for the Voxa MVP and its immediate extensions.
> Companion to `ARCHITECTURE.md` (which defines the rules); this document defines the
> **build** — components, data model, pipelines, milestone targets, exit criteria, and blockers.
> No calendar time frames: milestones gate on exit criteria, not dates.

---

## 1. System Summary

AI-powered SaaS for Indian content creators. Core loop:

```
trending topics → script in creator's voice (Voice DNA) → teleprompter
→ (publish outside Voxa) → analytics sync → outcome linkage → profile refinement
```

**Architecture style (locked):** Modular Monolith + Event-Driven + Ports & Adapters.

| Concern | Decision |
|---|---|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind, Shadcn/ui, Zustand |
| Backend (all core logic) | FastAPI, Python 3.12, Uvicorn/Gunicorn on Railway |
| AI runtime | Agno SDK / AgentOS → OpenRouter → claude-sonnet-4-6 |
| DB | Supabase Postgres, SQLAlchemy 2.0 async, Alembic |
| Jobs | Celery + Celery Beat, Upstash Redis broker, Flower |
| Streaming | SSE from FastAPI directly to the browser (no Next.js proxy) |
| Auth | Supabase Auth on frontend; python-jose JWT validation middleware in FastAPI |
| Embeddings | **Deferred.** MVP uses fixed-excerpts. Schema is embedding-ready (nullable vector column) |

**Service boundary rules (binding):**
1. FastAPI owns **all writes** to intelligence tables (`transcripts`, `transcript_segments`, `voice_profiles`, `scripts`, `script_outcomes`, `trending_topics`, `events`). Next.js owns writes to product tables (`users` profile fields, UI preferences). Either side may read anything through its own layer.
2. Browser calls FastAPI **directly** for all intelligence endpoints (script generation SSE, onboarding status, trend feed). Next.js API routes exist only where they add value (page-level data assembly, Razorpay webhooks, session logic). No pass-through proxying.
3. The frontend TypeScript client is **generated** from FastAPI's OpenAPI schema. Hand-written duplicate types are a defect.
4. No external API (YouTube, Trends, Reddit, Whisper) is ever called in a request handler. Background jobs pre-fetch; requests read cache/DB.

---

## 2. Component Map

```
Next.js (Vercel)
  └── UI + product CRUD + Razorpay webhook receiver
        │ HTTPS / SSE (direct)
        ▼
FastAPI (Railway) — /api/v1/*
  ├── auth middleware (Supabase JWT) + Redis rate limiting
  ├── modules/  channels · trending · scripts · teleprompter-support ·
  │             thumbnails · analytics · billing · notifications
  ├── shared/   event bus · ports/ · database
  ├── adapters/ youtube · google_trends · reddit · whisper ·
  │             razorpay · resend · openrouter
  ├── ai/       registry + agents (voice_dna, trending, script,
  │             thumbnail_brief; script_team behind flag)
  └── tasks/    Celery app + task modules
        ▼
Supabase Postgres (truth) · Upstash Redis (cache/broker) · Supabase Storage
```

The existing FastAPI trending service (pytrends + YouTube Data API) is absorbed as the
`trending` module + `TrendSourcePort` adapters; its scheduler becomes a Celery Beat entry.
It writes to `trending_topics`; the request path reads only Redis cache or DB.

---

## 3. Data Model

Conventions (all tables): `id uuid PK`, `created_at`, `updated_at`, soft-delete `deleted_at`.
All schema changes via Alembic. New features get new tables — never widen existing tables.

### 3.1 Core tables

| Table | Purpose | Key columns / notes |
|---|---|---|
| `users` | Auth-linked account, tier | mirrors Supabase Auth id; `plan`, `locale` |
| `channels` | YouTube channel per user | `handle`, `niche`, `language`, stats snapshot; `current_voice_profile_id` (pointer cache only — never the storage of the profile) |
| `videos` | Selected source videos per channel | `yt_video_id`, `title`, `published_at`, `view_count`, `selected_for_dna bool` |
| `transcripts` | One per ingested video | `video_id FK`, `source enum(captions, whisper)`, `language_detected`, `quality_score float`, `raw_text`, `clean_text` |
| `transcript_segments` | Typed chunks of transcripts | `transcript_id FK`, `segment_type enum(hook, body, transition, cta)`, `text`, `char_span`, `embedding vector NULL` ← **nullable from day 1, unused until embeddings milestone** |
| `voice_profiles` | **Append-only** profile versions | `channel_id FK`, `version int`, `profile jsonb`, `confidence jsonb` (per-dimension tier), `excerpt_ids uuid[]` (curated segments), `source enum(initial, refinement)`, `extraction_prompt_version` |
| `scripts` | Every generation | `channel_id`, `topic_id FK NULL` (freeform topics allowed), `hook/body/cta text`, `language`, `platform`, `voice_profile_version`, `agent_name`, `agent_version`, `prompt_version`, `model_id`, `input_tokens`, `output_tokens`, `cost`, `rating enum NULL`, `rating_detail jsonb NULL`, `final_text NULL` (creator-edited version) |
| `trending_topics` | Written by trending jobs only | `niche`, `language`, `title`, `virality_score`, `platform_fit`, `source`, `expires_at`, `embedding vector NULL` |
| `thumbnail_briefs` | One per script (MVP: text brief only) | `script_id FK`, `brief jsonb` (text overlay, colour, expression, composition) |
| `script_outcomes` | Flywheel linkage | `script_id FK`, `yt_video_id`, `matched_by enum(manual, auto)`, `ctr`, `avg_view_duration`, `views`, `synced_at` |
| `events` | Append-only domain event log | `name`, `payload jsonb`, `user_id`; feeds refinement + audit |
| `subscriptions`, `usage` | Billing + metering | `usage` rows include per-feature token/cost entries (Whisper minutes, generation tokens) |

### 3.2 Data rules

- `voice_profiles` is never updated in place. Refinement = new row, version+1.
- Every `scripts` row must carry `voice_profile_version`, `agent_version`, `prompt_version`, `model_id`. A script without full provenance is a defect (enforced by NOT NULL).
- `scripts.topic_id` is nullable: freeform typed topics must work so generation survives any trending-source outage.
- Whisper cost per creator is written to `usage` at transcription time (onboarding unit economics must be queryable).
- Indexing baseline: `user_id`, `channel_id`, `created_at DESC` everywhere queried; `(niche, language)` on `trending_topics`; partial index on `trending_topics(expires_at) WHERE deleted_at IS NULL`.
- RLS: not implemented (single shared DB role). Compensating controls are **mandatory** (see §9 Blockers B6): ownership checks live in repositories, and every repository ships a cross-user access-denial test.

---

## 4. Event Catalogue

Payloads versioned, additive-only. Emitters never know subscribers.

```
user.registered            channel.connected          channel.analyzed
transcripts.completed      voice_profile.updated
script.generated           script.rated               script.published
script.outcome_linked      topic.bookmarked           trending.refreshed
subscription.started       subscription.cancelled     subscription.payment_failed
analytics.synced
```

Key subscriptions (initial wiring):

| Event | Subscriber (Celery task) |
|---|---|
| `channel.connected` | `ingest_channel` (fan-out chain, §5) |
| `transcripts.completed` | `extract_voice_profile` |
| `channel.analyzed` | `warm_trending_cache`, notification email |
| `script.rated` | append to feedback store (reads by refinement job) |
| `script.published` / `analytics.synced` | `link_script_outcome` |
| `voice_profile.updated` | invalidate cached prompt blocks in Redis |

---

## 5. Pipelines

### 5.1 Ingestion & Voice DNA (triggered by `channel.connected`)

```
fetch channel + video list
→ select 20–50 source videos (recency + performance weighted)
→ FAN OUT one Celery task per video:
     captions via YouTubeAdapter → quality gate → if fail: WhisperAdapter
     → clean + segment (hook/body/transition/cta) → write transcripts + segments
→ JOIN: when ≥ minimum viable count done (or all attempted) emit transcripts.completed
→ extraction: batches of 5–10 transcripts through voice_dna agent (structured output)
→ consolidation pass merges batch analyses → confidence-tiered profile
→ excerpt curation: agent selects 8–12 most characteristic segments (stored as excerpt_ids)
→ INSERT voice_profiles (version 1) → update channels.current_voice_profile_id
→ emit channel.analyzed
```

Design requirements:
- Every step idempotent; per-video failure isolated (progressive status "32 of 50 analysed" exposed via onboarding status endpoint).
- Quality gate on captions: language-detection + garbage heuristics decide the Whisper fallback; decision and `quality_score` recorded on `transcripts`.
- YouTube API quota handling is first-class: per-key throttling, graceful degradation to "fewer videos analysed", never a hard failure.
- Minimum viable corpus: pipeline completes with as few as 2–3 usable transcripts, producing a low-confidence profile (see §6.2). It never blocks onboarding on corpus size.

### 5.2 Script generation (request path, SSE)

```
POST /api/v1/scripts/generate  { topic | freeform_text, platform, language }
→ rate-limit check (Redis, per-tier)
→ load current voice profile + curated excerpts (Redis-cached prompt block)
→ assemble prompt: [profile block · excerpt few-shots · task spec]
→ script agent (Agno, structured output: hook/body/cta + b-roll + est. duration)
→ stream SSE to browser
→ persist scripts row with full provenance + token/cost accounting
→ emit script.generated
```

- Confidence-aware prompting: low-confidence profile dimensions fall back to neutral defaults; never fabricate catchphrases.
- Model routing by task type via OpenRouter config: full model for long-form; cheaper route for Shorts hooks/captions/hashtags.
- `script_team` (multi-agent) exists behind a feature flag only; it is **not** enabled until measurement shows it beats single-agent on rating + (later) voice-match score. Until then premium differentiates on limits/features.

### 5.3 Refinement (scheduled per creator)

```
Celery Beat weekly per active creator:
  read events since last version (ratings, rating_detail, generated-vs-final diffs)
→ refinement pass: current profile + signals → adjusted profile
→ INSERT voice_profiles version+1 (source=refinement) → emit voice_profile.updated
```

- Skip creators with zero new signal (no empty versions).
- Diffing `scripts.final_text` vs generated text is the richest signal; store the diff summary in the refinement input.

### 5.4 Trending refresh (existing system, absorbed)

```
Celery Beat (6h): for each TrendSourcePort adapter (YouTube, Google Trends, Reddit)
→ per-source isolation (one source failing never empties the feed)
→ lightweight summarization per trend item → write trending_topics (with expires_at)
→ refresh Redis cache trending:{niche}:{lang} → emit trending.refreshed
```

### 5.5 Analytics sync & outcome linkage

```
Celery Beat daily per connected account:
  SocialPlatformPort.pull_stats → analytics rows → emit analytics.synced
Outcome linkage:
  auto-match published videos to scripts (title similarity + publish window)
  or manual "link this script to this video" endpoint
→ script_outcomes row → emit script.outcome_linked
```

Public-data sync ships first; retention/CTR depth requires OAuth-scoped Analytics API (see B3).

---

## 6. Voice DNA Specification

### 6.1 Profile = structured summary + verbatim excerpts (always both)

Descriptions produce imitation; the creator's own passages injected as few-shot examples
produce voice. Every generation uses profile **and** excerpts.

### 6.2 Extraction dimensions & confidence

| Dimension | Captures | Confidence: high after |
|---|---|---|
| language_mix | Hindi:English ratio and *where* switching happens | 1–2 videos |
| audience_address | aap / tum / doston / guys / bhailog | 1 video |
| rhythm | sentence length, energy, rhetorical questions, repetition | 2–3 videos |
| hook_style | question / claim / story / direct; first-15s pattern | 3–5 videos |
| cta_style | pushy / casual / joking / minimal; placement | 3–5 videos |
| catchphrases | recurring openers, closers, transitions, tics (must recur) | 10+ videos |
| personality | humour style, hype handling, energy arc | 10+ videos |

Rules:
- Each dimension stored with `confidence: high|medium|low`. Low-confidence → neutral default at generation. Guessing a catchphrase wrong is worse than omitting it.
- 2–3-video creators get a valid low-confidence profile; UI exposes a "Voice DNA X% trained" meter fed by confidence tiers.
- Voice-note onboarding supplement (2-minute casual recording → transcribed → merged into corpus) is a planned enhancement for small-corpus creators; schema requires no change (a `videos` row with a `source=voice_note` flag).

### 6.3 Provenance & measurability (the moat mechanism)

- Extraction prompt, generation prompt, and each agent definition carry a version identifier; stored on every profile and script row.
- Improvement question "does profile v(n+1) beat v(n)?" must be answerable by query: ratings and (later) outcomes grouped by `voice_profile_version`.
- Agno tracing covers *what happened*; version stamps cover *what to compare*. Both required.

---

## 7. Feature Modules — Technical Scope

| Module | MVP scope | Explicit non-scope (MVP) |
|---|---|---|
| channels | connect by handle, ingestion status endpoint, profile meter | Instagram/FB connect |
| trending | personalized feed read path (cache/DB only), bookmark, freeform topic passthrough | trend prediction, competitor tracking |
| scripts | SSE generation, hook/body/cta editing, save final_text, rating taps (`script.rated` with which-part-felt-off detail) | versioned drafts UI, collaboration |
| teleprompter | frontend-only over `scripts.final_text ?? generated`: full-screen, adjustable speed/font, power-word highlighting (marked spans provided by script agent output schema); mobile-first responsive | mirror mode, dual-device sync |
| thumbnails | **text brief only** (`thumbnail_briefs.brief jsonb`): overlay text, colour direction, expression, composition; generated from script hook + niche conventions | any image generation, Canva API, CTR-pattern analysis (needs analytics history) |
| analytics | public-stats sync + channel dashboard read path | retention/CTR depth (OAuth-gated), Instagram/FB |
| billing | Razorpay via PaymentPort, per-tier Redis rate limits, usage metering | Stripe |
| notifications | Resend transactional (onboarding done, weekly brief) | WhatsApp channel |

---

## 8. Milestones (gate on exit criteria, not dates)

### M0 — Validation (before build)
**Targets**
1. Transcript access test: 20 real channels (≥10 Hindi/Hinglish); captions attempt + Whisper fallback; record success rate, Hinglish quality, per-video cost.
2. Voice fidelity test: 5 Hinglish creators end-to-end by hand (extraction prompt → profile + excerpts → one script each); judged by actual viewers of each creator.
3. Draft v1 of extraction and generation prompts falls out of (2) — commit them as prompt version 1.

**Exit criteria:** ≥80% channels yield usable transcripts; ≥3/5 scripts judged "sounds like him"; Whisper cost per 30-video onboarding within budget.
**Blockers resolved here:** B1, B2 (measured, not assumed).

### M1 — Foundation
**Targets**
1. Repo layout per ARCHITECTURE.md §12; Docker Compose local stack (FastAPI, worker, beat, Redis, Postgres).
2. Alembic migration: all §3 tables incl. nullable vector columns; NOT NULL provenance on `scripts`.
3. Supabase JWT middleware; Redis rate-limit middleware; structlog + Sentry wiring.
4. Event bus + Celery wiring (`emit` → task dispatch); event catalogue constants.
5. Ports defined: `LLMPort`, `TrendSourcePort`, `SocialPlatformPort`, `TranscriptionPort`, `PaymentPort`, `EmailPort`; adapters stubbed with contract tests.
6. OpenAPI → TypeScript client generation in CI.

**Exit criteria:** local stack up in one command; a synthetic event round-trips bus → Celery task; cross-user repository denial tests pass; CI green.

### M2 — Ingestion pipeline
**Targets**
1. YouTubeAdapter: channel fetch, video listing, caption retrieval with quota throttling.
2. WhisperAdapter behind `TranscriptionPort`; caption quality gate decides fallback; costs metered to `usage`.
3. Fan-out/join Celery chain per §5.1 through segment writing; progressive status endpoint.
4. Trending service absorbed: adapters under `TrendSourcePort`, Beat schedule, per-source isolation, cache warm.

**Exit criteria:** one real Hinglish channel flows handle → ≥20 transcripts + typed segments in DB with quality scores; kill one source adapter and the trend feed still serves; kill one video task and onboarding still completes.

### M3 — Voice DNA extraction
**Targets**
1. `voice_dna` agent with structured output matching §6.2 dimensions + confidence.
2. Batch extraction + consolidation + excerpt curation per §5.1 tail; `voice_profiles` v1 written; `channel.analyzed` emitted.
3. Small-corpus path verified: 2–3 transcript channel produces a valid low-confidence profile.

**Exit criteria:** three real channels (one small-corpus) produce inspectable profiles whose stated dimensions match what a human hears in the videos; profile write is append-only under repeated runs (idempotency).

### M4 — Script generation
**Targets**
1. Generation endpoint per §5.2: cached prompt block, SSE streaming, structured hook/body/cta + b-roll + power-word spans + duration estimate.
2. Confidence-aware prompt assembly; freeform-topic path (no `topic_id`).
3. Full provenance + token/cost persistence; per-tier rate limiting active.
4. Model routing config (long-form vs short-form tasks).

**Exit criteria:** M0's judges rate in-system output ≥ manual M0 output; SSE first-token latency acceptable on mobile network profile; cost per script visible in `usage` and within pricing model; generation succeeds with trending tables empty.

### M5 — Creator-facing loop (frontend integration)
**Targets**
1. Onboarding flow consuming status endpoint (progressive analysis screen, DNA meter).
2. Trending feed UI (cache-backed), topic pick + freeform entry.
3. Script screen: streaming render, edit, save `final_text`, rating taps with which-part detail.
4. Teleprompter: full-screen, speed/font controls, power-word highlighting, mobile-first.
5. Thumbnail brief panel rendering `brief jsonb`.

**Exit criteria:** a new user completes handle → script → teleprompter with no operator intervention; the full loop works on a mid-range Android browser; rating taps land as `script.rated` events.

### M6 — Learning loop
**Targets**
1. Feedback store subscriber; generated-vs-final diff capture.
2. Weekly refinement job per §5.3 producing version+1 profiles; cache invalidation on `voice_profile.updated`.
3. Measurement query/dashboard: ratings grouped by profile version and prompt version.

**Exit criteria:** a seeded batch of ratings demonstrably produces a v2 profile whose changes trace to the signals; the version-comparison query answers "v2 vs v1" without manual archaeology.

### M7 — Monetization & hardening
**Targets**
1. Razorpay subscription lifecycle via PaymentPort; webhook → task; `subscription.*` events; tier enforcement in rate limiter.
2. Usage metering surfaces: cost per script, cost per onboarding, per-feature token dashboards.
3. Feature flags table + Redis cache; `script_team` registered behind flag, disabled.
4. Load pass on generation endpoint; Sentry noise triage; backup/restore drill on Postgres.

**Exit criteria:** paid tier upgrade/downgrade/cancel round-trips against Razorpay test mode; a simulated payment failure emits the event and downgrades correctly; restore drill succeeds.

### M8 — Post-MVP extensions (design now, build after M7)
1. **Embeddings activation:** backfill `transcript_segments.embedding`, retrieval step replaces fixed excerpts in §5.2, voice-match scoring gate (auto-regenerate below threshold), trend personalization ranking. Schema already ready; work is additive.
2. **Outcome flywheel:** auto-linkage per §5.5, ROI surfacing ("Voxa scripts vs baseline"), outcome-weighted refinement.
3. **OAuth-scoped analytics depth** (post-B3): retention curves, CTR, traffic sources into refinement and thumbnail-brief inputs.
4. `script_team` evaluation: A/B behind flag, judged on ratings + voice-match; enable only on measured win.
5. Voice-note onboarding supplement; multi-format output (one script → Shorts hook, caption, thread).

---

## 9. Blockers & Risk Register

| # | Blocker / risk | Impact | Handling |
|---|---|---|---|
| B1 | YouTube caption access tightening | Ingestion starves; Voice DNA has no input | Measured in M0; `TranscriptionPort` makes Whisper a config-level fallback; quality gate + cost metering built in M2 |
| B2 | Hinglish auto-caption quality | Garbage-in profiles | M0 measures; Whisper path; `quality_score` excludes bad transcripts from extraction |
| B3 | Google OAuth verification lead time (Analytics/upload scopes; possible CASA) | Blocks analytics depth & future publishing, **not** MVP | Start verification early in parallel; MVP uses API-key public data only |
| B4 | YouTube Data API quota ceilings | Onboarding throughput cap | Per-key throttling, video-count degradation, quota telemetry from M2; multiple keys/project structure if needed |
| B5 | "Unlimited" tier unit economics | Margin inversion at scale | Prompt-block caching, model routing, per-tier Redis fair-use limits, cost-per-script dashboard from M4 — priced from data |
| B6 | No RLS (single shared DB role) | Cross-tenant data exposure = loss of creators' competitive asset (their Voice DNA) | Repository-level ownership checks + mandatory cross-user denial tests (M1 exit gate); revisit real RLS before agency/multi-channel tier or compliance need |
| B7 | Voice quality plateaus below "sounds like me" | Core value prop fails | M0 gate before build; provenance versioning makes quality measurable; refinement loop (M6) is the improvement mechanism |
| B8 | Multi-agent `script_team` adds cost + output variance | Premium tier costs more and sounds *less* like the creator | Feature-flagged off; enablement requires measured win (M8.4) |
| B9 | Trending source outage (any adapter) | Empty feed / broken generation | Per-source isolation (M2 exit test); freeform-topic generation path independent of trending tables (M4 exit test) |
| B10 | Celery/Redis broker instability under Upstash limits | Silent job loss | Idempotent tasks, acks-late, dead-letter queue, Flower monitoring from M1; alert on queue depth |
| B11 | Prompt/profile drift without measurement | "Improvement" becomes vibes | NOT NULL provenance on scripts; version-comparison query is an M6 exit criterion, not a nice-to-have |

---

## 10. Non-Goals (binding, restated)

- No microservices split without a measured bottleneck.
- No embeddings/pgvector activation before M8.1 — but the nullable columns ship in M1.
- No image generation in thumbnails module (brief JSON only) for MVP.
- No parsing free-text LLM output — structured outputs everywhere.
- No external API calls in request handlers.
- No business logic in routers, adapters, or Celery task bodies.
- No enabling `script_team` without a measured quality win.
