# Aegis Context

## What Aegis Context does

An alert fires. Aegis Context correlates recent deploys, infrastructure changes, telemetry, and
past postmortems for the affected service, and a single agent produces a root cause with a
citation behind every claim. Correlation happens once, at ingest time, into one PostgreSQL schema
that shares service identifiers and one UTC clock across every evidence source. The agent reaches
that schema through exactly three deterministic, aggregate-only MCP tools:
`get_incident_diff`, `get_error_telemetry`, and `search_similar_postmortems`.

## How it works

Aegis Context is built on one bet: **correlate at ingest, not at query time.**

The reflexive agentic design for this problem is a swarm — a Git agent, a logs agent, a metrics
agent, talking to one another. That reintroduces context loss at the coordination layer, and turns
structured facts into prose that the next agent has to parse back into structure. Aegis Context
instead normalises every source into one PostgreSQL schema that shares service identifiers and one
UTC clock, then hands a single agent three deterministic tools over it.

The consequence is that vector search is used for exactly one thing — finding prose that resembles
other prose, in past postmortems. Live telemetry is never retrieved by similarity. It is fetched by
SQL, because "how many 5xx did this service emit in this minute" has an exact answer and an
approximate one is worse than useless.

### High-level architecture

```mermaid
flowchart TB
  subgraph SRC["Evidence sources — committed corpus"]
    GIT["git export<br/>commits + deployments + hunks"]
    TF["terraform<br/>plan-*.json ∩ applies.json"]
    K8S["kubernetes<br/>pod-status.json + events.json"]
    LOG["logs<br/>json · nginx · python · logfmt"]
    PM["postmortems<br/>*.md"]
  end

  SRC --> ETL["ETL — aegis ingest all"]
  ETL --> PG[("PostgreSQL + pgvector<br/>one schema · one clock · one service id")]

  PG --> MCP["MCP server — stdio subprocess<br/>3 aggregate-only tools"]
  MCP --> AG["single agent<br/>gpt-5.6-luna"]
  AG --> SUM["IncidentSummary<br/>every claim carries citations"]
  SUM --> VAL{"provenance validation"}
  VAL -- "citation not seen this run" --> ABORT["run aborts"]
  VAL -- "all citations captured" --> OUT["CLI · Slack · stored trace"]

  ALERT["alert webhook"] --> API["FastAPI<br/>atomic dedup"] --> AG
```

The agent never touches the database. It cannot issue SQL, and it cannot read individual rows
except through an aggregate that already carries their citations. That is a deliberate constraint:
an agent that can query freely will eventually retrieve something it cannot cite.

### The ETL pipeline

Log and Kubernetes evidence converges on the normalisation below. Git and Terraform share its
identity and service-resolution rules but not its later stages: neither produces `log_events`, so
neither is masked or rolled up.

```mermaid
flowchart LR
  RAW["raw source record"] --> UID["source_uid<br/>content-derived, computed<br/>BEFORE normalisation"]
  UID --> RES["resolve_service()<br/>name → log_keys → k8s_names<br/>→ repo → infra_tags"]
  RES -- "no match" --> UNRES[("unresolved_events")]
  RES --> LVL["canonical_level()<br/>nginx derives level from status"]
  LVL --> MASK["template masking<br/>single pass, sentinel-based<br/>template_hash = sha256[:32]"]
  MASK --> BASE[("log_events · commits · deployments<br/>infra_changes")]
  BASE --> ROLL["delete + recompute<br/>affected minutes only<br/>one transaction, advisory lock"]
  ROLL --> RU[("error_rollups")]
```

Four properties of that pipeline are load-bearing, and each exists because the obvious alternative
is wrong:

- **`source_uid` is computed before normalisation.** This is what makes a crashed batch replayable;
  derive identity after normalisation and a change to normalisation silently changes identity. Log
  events insert `ON CONFLICT DO NOTHING`. The other sources are not append-only and say so:
  deployments update lifecycle state, Kubernetes replaces a snapshot when its Event count rises,
  and a postmortem edit deletes and re-inserts its chunks.
- **Rollups are deleted and recomputed, never upserted.** `ON CONFLICT DO UPDATE SET count =
  EXCLUDED.count` undercounts on any overlapping re-run, and the error is invisible: every number
  the agent saw simply becomes wrong.
- **Masking is a single pass with sentinels, not sequential substitution.** Run the substitutions in
  sequence and a later branch rewrites an earlier branch's output — the quoted-string rule turns
  `"<URL>"` into `<STR>`, and two identical messages get two hashes.
- **A log line that cannot be attributed to a service goes to `unresolved_events`, not to `NULL`.**
  `log_events.service_id` is part of the rollup primary key, so a nullable one fails on the first
  unattributable line rather than the thousandth. The other sources differ deliberately: an
  unattributed Terraform apply is still stored, with `service_id` null, because a destroyed
  resource nobody tagged is still evidence; an unresolvable Kubernetes object is skipped; and
  malformed Git input raises rather than being stored at all.

Terraform is the sharpest case. `terraform show -json` plan output describes *intended* actions and
carries neither execution evidence nor a timestamp. Ingesting it as applied state would let the
agent cite an abandoned plan as a root cause, so only entries with a matching `applies.json` record
whose status is `success` are ingested at all, and `applied_at` comes from the manifest.

### The MCP layer

Three tools, all aggregate-only, all returning a citation on every row they surface.

| Tool | Answers | Returns |
| --- | --- | --- |
| `get_incident_diff` | what changed near this window | `focus`, `other_services`, `unattributed` — commits with hunks, deployments, attributed infra changes |
| `get_error_telemetry` | what the telemetry did | minute-snapped series by status class, top templates ranked by delta against the preceding disjoint window, one richest exemplar each, `baseline_sparse` |
| `search_similar_postmortems` | has this happened before | cosine-nearest chunks above a similarity floor, resolution carrying its own citation |

The server runs as a stdio subprocess with its database URL pinned explicitly, because
`StdioServerParameters` does not inherit the parent environment — a server that silently reads a
different database presents as an empty tool result, not as an error.

### What "context" means here, and what it does not

Every claim in an `IncidentSummary` is a `Claim` object: a statement plus at least one citation.
Citations follow a fixed grammar owned by `src/aegis/mcp_server/citations.py` — `commit:<sha40>`,
`deploy:<uid32>`, `infra:<uid32>`, `log:<uid32>`,
`rollup:<service>/<iso8601>/<status_class>/<level>/<template_hash>`, and
`postmortem:<slug>@<content_sha8>#<ordinal>`.

Before a summary is returned, every citation in it is checked against the set of citations that the
tools actually returned during that run. An identifier that is malformed, or well formed but never
seen, aborts the investigation.

**This proves provenance, not support.** It establishes that the agent is pointing at a row a tool
handed it during this run, which is what makes fabricated evidence impossible. It does not
establish that the row entails the sentence attached to it. Semantic support is checked by the
planted scenario contracts, or by a human.

### Repository layout

| Path | Role |
| --- | --- |
| `src/aegis/config.py` | Runtime settings, `AEGIS_`-prefixed and `.env`-backed, except `OPENAI_API_KEY`, which is read unprefixed because every other tool already exports it. `openai_api_key` is a `SecretStr` so it cannot reach a stored trace; the Slack and GitHub secrets are not, and should be. |
| `src/aegis/db/` | The complete schema and its constraints, plus engine construction. Migration 1 creates every table, including ones only later versions read. |
| `src/aegis/ingest/` | One module per source — `git`, `logs`, `terraform`, `k8s`, `postmortems` — over shared `identity`, `normalize`, `templates`, and `pipeline` primitives. |
| `src/aegis/aggregate/` | Rollup computation: the delete-and-recompute transaction and its dirty-set capture. |
| `src/aegis/embeddings/` | The `EmbeddingProvider` protocol, the OpenAI provider, and a fixture provider that serves vectors supplied by the caller, so retrieval logic is testable offline. |
| `src/aegis/mcp_server/` | The three tools, their SQL, their response envelopes, and the citation grammar. |
| `src/aegis/agent/` | The agent loop, its prompt, the `IncidentSummary` model, provenance validation, MCP transport, Slack delivery, and the stored-trace view. |
| `src/aegis/app/` | The application boundary — `investigate()`, the run context and its trace sinks, the background runner, and rendering. The CLI and the API are both callers of it. |
| `src/aegis/api/` | FastAPI: the alert webhook with atomic deduplication, the GitHub webhook, and `/healthz`. |
| `src/aegis/release/` | The `make demo` coordinator: preflight, empty-database probe, replay digest, stage-labelled failures. |
| `corpus/` | The committed evidence: `services.yaml`, `git/`, `logs/` with its `manifest.yaml`, `terraform/`, `k8s/`, `postmortems/`, and `scenarios/`. |
| `tests/unit/` | Pure logic — masking, parsing, redaction, citation grammar. No database. |
| `tests/integration/` | Real PostgreSQL: rollups, ingest replay, the MCP stdio boundary, webhook concurrency. |
| `tests/corpus_contract/` | Runs **before** any agent: every expected fact must resolve to a concrete tool field, or the corpus is wrong rather than the agent. |
| `tests/eval/` | The five scenario evaluations. Skipped without `OPENAI_API_KEY`; hard failures under `AEGIS_REQUIRE_LIVE_EVAL=1`. |
| `tests/docs/` | Guards the claim-scope language in this file and the design document against silent drift. |

Full architecture, data model, and interface detail live in
[`docs/2026-08-19-aegis-context-design.md`](docs/2026-08-19-aegis-context-design.md). The
per-milestone implementation specifications are `docs/part-0` through `docs/part-4`.

## What v1.0 demonstrates

Aegis Context v1.0 is a feasibility demonstration. Five planted scenarios show that one agent can
correlate pre-ingested deploy, infrastructure, and telemetry evidence through aggregate MCP tools.
They do not establish that Aegis Context is more accurate, cheaper, faster, or more reliable than
a multi-agent system. No swarm baseline, matched-budget comparison, repeated-trial analysis, or
held-out external corpus is included.

**The scenario contracts do not cover postmortem retrieval.** Every scenario's `must_cite` and
reachability entries name `get_incident_diff` and `get_error_telemetry` only, so the five
evaluations would still pass with `search_similar_postmortems` disabled. That is deliberate — a
scenario must be solvable from primary evidence, or retrieval becomes the thing that passes the
suite — but it means the third tool is exercised by its own tests rather than by the
demonstration.

## What v1.0 does not demonstrate

- Production reliability or high availability.
- Crash-durable background scheduling or exactly-once Slack / webhook delivery.
- Robustness on external, non-corpus log distributions.
- Live log, Terraform, or Kubernetes ingestion, or any cloud telemetry integration.
- That five planted scenarios represent real incident prevalence.
- That the agent's natural-language explanation is semantically entailed by each cited row.
- Any advantage over a swarm or other multi-agent architecture.
- Bit-for-bit repeatability of remote model output.
- Safe exposure of the unauthenticated generic alert endpoint beyond its documented loopback use.

Provenance validation proves only that each citation in a displayed claim was returned by one of
the three MCP tools during that investigation. It rejects malformed, fabricated, and unseen
identifiers. It does not prove that the cited row semantically supports the sentence attached to
it. Semantic support is evaluated by the planted scenario contracts or by a human reviewer.

## Prerequisites

Before running any command below, you need:

- Git.
- [`make`](https://www.gnu.org/software/make/).
- [`uv`](https://docs.astral.sh/uv/).
- Outbound HTTPS access to `api.openai.com`.
- A funded `OPENAI_API_KEY`.
- Docker with Docker Compose for the authoritative release observation described below; or
  PostgreSQL 14+ with the `pgvector` extension for the local external-database rehearsal.
- `cloudflared` or `ngrok`, but only if you intend to run `make demo-live`.

`make demo` performs five paid agent runs and live OpenAI embedding calls. Running the
command is your explicit request to incur that cost; it does not pause for confirmation.

## Run the reproducible demo

The authoritative release path uses the repository's own Docker Compose PostgreSQL service, a
named volume scoped to the demo, and a fresh clone with no prior Aegis state:

```bash
git clone <this repository> && cd aegis-context
export OPENAI_API_KEY=...
make demo
```

`make demo` refuses to run against a database that already has Aegis tables in it — it never
deletes existing data. If you have run it before, tear down the demo's own compose project first:

```bash
docker compose -p aegis-context-demo down -v
```

### Local rehearsal without Docker

Docker is the authoritative clean-room path, but it is not available on every development
machine. `make demo` also accepts a disposable external PostgreSQL 14+ database — never the
default development `aegis` database:

```bash
createdb -O aegis aegis_demo

AEGIS_DEMO_DB_MODE=external \
AEGIS_DATABASE_URL=postgresql+psycopg://aegis:aegis@127.0.0.1:5432/aegis_demo \
make demo
```

If the `aegis` role cannot create the `vector` extension itself, create it once as an
administrator before running the command above:

```bash
psql -d aegis_demo -c 'CREATE EXTENSION IF NOT EXISTS vector'
```

This external run rehearses the same commands and contracts locally. It is **not** a substitute
for the Docker clean-room observation: PostgreSQL 14 on an already-configured Homebrew server is
not a new machine, and it is not the release's PostgreSQL 17 container.

## Inspect a run

`make demo` prints one `run_id` per scenario. Every displayed summary is backed by a persisted,
inspectable tool-call trace:

```bash
uv run aegis trace --run-id <run_id>
```

Add `--json` for the validated, machine-readable envelope, or `--full` to include complete tool
arguments and result payloads in the text view. The command is read-only: it renders exactly the
tool results the agent saw during that run, including which stored citation was returned by which
tool call. It does not resolve the citation against current evidence, because a rollup can
legitimately change after later ingest and the point of the view is what the agent actually saw.

## Run the API

```bash
uv run aegis db upgrade
uv run aegis ingest all
uv run aegis serve-api --host 127.0.0.1 --port 8000
```

`serve-api` binds to loopback by default. `POST /webhooks/alert` has no authentication in v1.0, so
binding it to a non-loopback address is an explicit, deliberate act — do not do this on a
network you do not control.

## Run the GitHub live demo

`make demo-live` verifies a real GitHub push delivered through a tunnel into a running Aegis API
process. It does not create a repository, configure GitHub, or push on your behalf.

1. Create or select a disposable scratch repository you control.
2. Add its exact normalized `owner/name` to `corpus/services.yaml` for one of your services.
3. Load the registry: `uv run aegis ingest services`.
4. Choose a strong secret and set it as `AEGIS_GITHUB_WEBHOOK_SECRET` locally and as the scratch
   repository's webhook secret.
5. Start the API: `uv run aegis serve-api --host 127.0.0.1 --port 8000`.
6. Start a tunnel:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8000
   # or
   ngrok http 8000
   ```

7. In the scratch repository's webhook settings, configure:

   ```text
   Payload URL: <public-tunnel-origin>/webhooks/github
   Content type: application/json
   Secret: exactly AEGIS_GITHUB_WEBHOOK_SECRET
   Events: push and pull_request
   ```

8. Start the verifier:

   ```bash
   GITHUB_REPO=owner/scratch \
   PUBLIC_BASE_URL=https://the-tunnel.example \
   make demo-live
   ```

9. When it prints `READY`, make and push one harmless commit to the scratch repository.
10. Verify GitHub reports a `202` delivery, and retain the delivery id with your release record.

A synthetic, hand-crafted POST to `/webhooks/github` can test HMAC plumbing, but it cannot satisfy
this observation. The live-demo claim is only established once GitHub has actually delivered a
real push through the tunnel and the verifier confirms the new commit is reachable through
`get_incident_diff`.

## Development gates

```bash
uv run ruff check .
uv run mypy --strict src
uv run pytest -q
```

`ruff format` is not configured for this project and is not a gate. `mypy --strict src tests` has
pre-existing failures in test files and is not a gate either. Ordinary `pytest` runs skip the five
live agent evaluations and the live embedding test when `OPENAI_API_KEY`
is absent; `make demo` sets `AEGIS_REQUIRE_LIVE_EVAL=1`, which turns those skips into failures so a
green `make demo` run cannot silently omit the observations the release claim depends on.

## Operational limits

- Ingest is manual (`aegis ingest all`) for every source except GitHub pushes in v1.0. There is no
  scheduler, poller, or filesystem watcher.
- `POST /webhooks/alert` deduplicates concurrently, but a process crash between the incident row
  committing and the background run starting means that incident is never investigated and
  deduplication suppresses any retry. There is no crash-durable queue.
- Slack delivery is best-effort, single-attempt, and not exactly-once. An Incoming Webhook exposes
  no idempotency key.
- `POST /webhooks/github` ingests changed paths from a push but never patch content — every file
  from that source is recorded with `hunks_omitted: "webhook"`. A `pull_request` event only
  enriches an already-ingested commit's PR number; it never fabricates a commit from PR metadata
  alone, because the payload lacks the path and patch evidence needed for a truthful row.
- There is no live log tailing, Terraform apply hook, or Kubernetes watch. All non-GitHub evidence
  is ingested from the committed corpus.
