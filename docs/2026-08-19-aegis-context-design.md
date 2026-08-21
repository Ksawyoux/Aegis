# Aegis Context — design, as built at v1.0

This document describes the product that exists today. It is not an implementation plan: build
order, acceptance criteria, and file-by-file change lists live in the versioned Part
specifications, not here.

## Problem

Incident evidence is fragmented across deploy history, infrastructure changes, telemetry, and
postmortems. When each source is reached by a different tool, or reparsed as prose across several
agent hops, the responder loses shared identity between records, loses their relative ordering,
and loses the ability to trace a claim back to the row that supports it. An incident summary that
cannot show its evidence is not more trustworthy than a guess with better prose.

## Product boundary

Aegis Context correlates evidence that has already been ingested. It is a feasibility
demonstration, not a production monitoring system: it does not tail live logs, apply Terraform, or
watch a Kubernetes cluster. Five planted scenarios exercise the one supported path — an alert
fires, an agent investigates using the ingested corpus, and a citation-backed summary results. The
product does not claim to be more accurate, cheaper, faster, or more reliable than a multi-agent
system, and includes no swarm baseline, matched-budget comparison, repeated-trial analysis, or
held-out external corpus to support such a claim.

Provenance validation is the other boundary that matters. It proves that every citation in a
displayed claim was returned by one of the three MCP tools during that investigation, and rejects
malformed, fabricated, or unseen identifiers. It does not prove that the cited row semantically
supports the sentence attached to it — that judgment is made by the planted scenario contracts, or
by a human reviewer reading the trace.

## Architecture

Correlation happens once, at ingest, into one PostgreSQL schema. Every evidence source is
attributed to a shared, immutable service identifier, and every timestamp is stored as UTC
`TIMESTAMPTZ`, so a commit, a deployment, an infrastructure change, and a telemetry rollup for the
same service can be joined and ordered without re-deriving identity at query time.

One agent, built on `anthropic[mcp]` with `claude-opus-5`, receives exactly three aggregate MCP
tools over a spawned stdio transport: `get_incident_diff`, `get_error_telemetry`, and
`search_similar_postmortems`. The agent has no other database access and no direct GitHub access —
every fact it can cite was returned by one of those three tools during its own run. Postmortem
retrieval uses OpenAI `text-embedding-3-small` at `dimensions=1024`, stored in a `pgvector` column.

Two ingest paths feed the schema. The offline corpus — Git history, Kubernetes/Terraform infra
changes, logs, and postmortems — is loaded manually with `aegis ingest all` and is the substrate
for the five planted scenarios. `POST /webhooks/github` is the sole live-push exception: a
signed GitHub `push` (and, for enrichment only, `pull_request`) event lands directly in the same
commit table the offline corpus populates, so the same tools reach both without a second code path.

An HTTP layer sits in front of the agent for operational use: `POST /webhooks/alert` deduplicates
concurrent alerts atomically and schedules a background investigation; `GET /healthz` reports
database and embedding-configuration readiness; Slack delivery is a best-effort, single-attempt
Incoming Webhook post. None of this changes the agent's tool surface or its three-tool contract.

## Evidence identity and time

Every evidence record has a stable, content-derived identity so that ingesting the same source
twice produces the same rows rather than duplicates. Commits use their full 40-character lowercase
Git SHA. Deployments, infrastructure changes, and log events use a 32-character hexadecimal UID
derived from a versioned, canonicalized hash of their identifying fields — never a database
sequence number, so the identity survives a schema migration or a re-ingest in a different order.
Error rollups are keyed by service, a minute-snapped UTC bucket, HTTP status class, log level, and
a 32-character template hash. Postmortem chunks are keyed by document slug, an ordinal position,
and an 8-character prefix of the document's content hash, so an edited postmortem produces a new,
distinguishable citation rather than silently changing what an old citation means.

Incident windows are half-open (`[window_start, window_end)`), and every window boundary is
snapped to the minute before it is used to query telemetry or rollups, so two logically identical
requests issued moments apart return identical results. Ordering within a window is deterministic:
ties are broken by identity, never by insertion order or by a database's incidental scan order.

The public citation grammar — the only form any evidence identifier is displayed in — is:

- `commit:<full-40-character-sha>`
- `deploy:<32-character-uid>`
- `infra:<32-character-uid>`
- `log:<32-character-uid>`
- `rollup:<service>/<iso8601-bucket-start>/<status_class>/<level>/<32-character-template-hash>`
- `postmortem:<slug>@<8-character-content-sha>#<ordinal>`

`src/aegis/mcp_server/citations.py` is the single authoritative implementation of this grammar; it
is validated in both directions — formatting and parsing — and every other component that displays
or checks a citation calls into it rather than re-deriving the pattern.

## Data model

- **`services`** — the shared, immutable identity every other table attributes to. Carries the
  repo, log-key aliases, Kubernetes name aliases, and infra tag matchers used for attribution.
- **`commits`** and **`deployments`** — Git history and its deployment lifecycle. A commit is
  immutable once ingested; a deployment's status transitions along a restricted, explicit state
  machine (`in_progress → {success, failed, rolled_back}`, `success → rolled_back`) and any other
  transition is rejected rather than silently applied.
- **`infra_changes`** — attributed and unattributed infrastructure changes, queryable by service
  or by time alone when attribution is unknown.
- **`log_events`** and **`unresolved_events`** — parsed log lines that resolved to a service, and
  those that did not, with the reason recorded rather than the line dropped.
- **`error_rollups`** — minute-bucketed aggregates by service, status class, level, and template,
  each carrying a first-seen/last-seen range and one exemplar log event.
- **`postmortems`** and **`postmortem_chunks`** — the retrieval corpus, chunked and embedded, with
  a content hash that changes whenever the source document changes.
- **`incidents`** — one row per deduplicated alert. Carries the dedup key, the resolved service
  (nullable — an alert for an unregistered service is still recorded), the investigation window,
  status, and the `summary_json` envelope that stores the run id, the validated summary, the
  complete deterministic tool-call trace, and the delivery outcome for that run.
- **`ingest_watermarks`** — the replay cursor per ingest source, so re-running ingest is a no-op
  rather than a duplicate.

## Interfaces

- `aegis ingest all` — load the committed corpus (services, Git, logs, and — where implemented —
  infrastructure and postmortems) into the configured database.
- `aegis investigate --scenario <path>` — run one investigation from the CLI and render its
  markdown and JSON summary.
- `aegis serve-mcp` — run the three-tool MCP server over stdio; this is what the agent spawns.
- `aegis serve-api` — run the FastAPI application exposing the HTTP surface below.
- `aegis trace --run-id <id>` — render a persisted investigation's stored tool-call trace and
  validate that every displayed citation traces back to the tool result that produced it.
- `POST /webhooks/alert` — accept a provider or synthetic alert, deduplicate it atomically, and
  schedule a background investigation.
- `POST /webhooks/github` — accept a signed GitHub `push` or `pull_request` delivery and ingest it
  through the same commit path the offline corpus uses.
- `GET /healthz` — report database reachability and embedding configuration readiness.
- Exactly three MCP tools, and no others, reachable by the agent:
  `get_incident_diff`, `get_error_telemetry`, and `search_similar_postmortems`.

## Operational workflow

An alert arrives at `POST /webhooks/alert` and is deduplicated by an atomic
`INSERT ... ON CONFLICT DO NOTHING` on its dedup key, so concurrent duplicate deliveries produce
exactly one incident row and exactly one scheduled background run. That run builds a fresh run id,
records it before the agent starts, spawns the MCP server as a subprocess with the same effective
database configuration as the parent process, and runs the three-tool investigation. On success the
validated summary, the complete trace, and a best-effort Slack delivery outcome are persisted
together; on failure the trace still records why, and no summary is ever persisted as valid unless
the run itself validated its provenance. A GitHub push reaches the same commit table through
`POST /webhooks/github` instead of through `aegis ingest all`, verified by an HMAC-SHA256 signature
computed over the exact raw request bytes before any JSON parsing occurs.

## Known limits

Aegis Context v1.0 is a feasibility demonstration; it does not establish superiority, and
provenance validation is not semantic entailment checking — see Product boundary above for both.

Beyond that stated scope: ingest is manual for every source except GitHub pushes, so there is no
scheduler, poller, or filesystem watcher. `BackgroundTasks` gives the alert webhook a crash window
between an incident committing and its investigation starting — a process death in that window
means the incident is never investigated, and deduplication suppresses any retry, because there is
no crash-durable queue. Slack delivery is best-effort and single-attempt; an Incoming Webhook
exposes no idempotency key, so exactly-once delivery is not claimed. Investigation quality depends
on two remote providers (Anthropic and OpenAI); an outage or rate limit in either degrades or
blocks a run, and no local fallback model is configured. A GitHub push payload carries changed
paths but never patch content, so every commit ingested that way is recorded with
`hunks_omitted: "webhook"` rather than with hunks Aegis never received. There is no live log
tailing, Terraform apply hook, or Kubernetes watch in v1.0 — all non-GitHub evidence comes from the
committed corpus loaded through `aegis ingest all`.
