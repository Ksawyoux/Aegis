# Aegis Context

## What Aegis Context does

An alert fires. Aegis Context correlates recent deploys, infrastructure changes, telemetry, and
past postmortems for the affected service, and a single agent produces a root cause with a
citation behind every claim. Correlation happens once, at ingest time, into one PostgreSQL schema
that shares service identifiers and one UTC clock across every evidence source. The agent reaches
that schema through exactly three deterministic, aggregate-only MCP tools:
`get_incident_diff`, `get_error_telemetry`, and `search_similar_postmortems`.

## What v1.0 demonstrates

Aegis Context v1.0 is a feasibility demonstration. Five planted scenarios show that one agent can
correlate pre-ingested deploy, infrastructure, telemetry, and postmortem evidence through three
aggregate MCP tools. They do not establish that Aegis Context is more accurate, cheaper, faster,
or more reliable than a multi-agent system. No swarm baseline, matched-budget comparison,
repeated-trial analysis, or held-out external corpus is included.

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
- Outbound HTTPS access to `api.anthropic.com` and `api.openai.com`.
- A funded `ANTHROPIC_API_KEY`.
- A funded `OPENAI_API_KEY`.
- Docker with Docker Compose for the authoritative release observation described below; or
  PostgreSQL 14+ with the `pgvector` extension for the local external-database rehearsal.
- `cloudflared` or `ngrok`, but only if you intend to run `make demo-live`.

`make demo` performs five paid Anthropic agent runs and live OpenAI embedding calls. Running the
command is your explicit request to incur that cost; it does not pause for confirmation.

## Run the reproducible demo

The authoritative release path uses the repository's own Docker Compose PostgreSQL service, a
named volume scoped to the demo, and a fresh clone with no prior Aegis state:

```bash
git clone <this repository> && cd aegis-context
export ANTHROPIC_API_KEY=...
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
live agent evaluations and the live embedding test when `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
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
