# Part 4 — v1.0 Reproducible Feasibility Release: implementation specification

> **Revision 1.** This milestone packages the implemented correlation engine and operational
> workflow into a falsifiable release. It does not add a fourth tool, another agent, a scheduler, or
> a new evidence path. Its product claim is that an independent operator can reproduce the existing
> five-scenario demonstration and inspect how every displayed citation entered each run.

Parts 0 and 1 are committed. Part 2 is still being implemented. Part 3 is specified concurrently and
defines the operational persistence seam assumed here. Part 4 may not weaken the gates or silently
substitute fixture-only evaluations for the live five-scenario observation.

---

## Falsifiable claim

> **An independent operator reproduces the five-scenario feasibility demonstration from an empty
> database using only the repository, the documented prerequisites, and the published commands.**

## The single observation that would falsify it

On a machine that has never run the project, start with a fresh clone and an empty PostgreSQL
database, follow the README literally, and run `make demo`.

The claim is falsified if the operator cannot obtain all of the following without undocumented
intervention:

1. the schema migrated to Alembic head with pgvector available;
2. the committed corpus ingested twice without changing its content digest;
3. the real gates passing:
   `uv run ruff check .`, `uv run mypy --strict src`, and `uv run pytest -q`;
4. all five live agent evaluations executing rather than skipping;
5. five provenance-valid summaries satisfying their scenario contracts;
6. a distinct persisted `run_id` and inspectable tool trace for every displayed summary.

A green unit suite, improved documentation, or a successful run against a database containing state
from an earlier attempt does not establish this claim.

## Why this milestone exists separately

v0.3 proves healthy-process alert deduplication and best-effort delivery. It does not prove that a
new operator can install the dependencies, create the database, migrate it, ingest every source,
propagate credentials into the MCP child process, execute the paid evaluations, or inspect the
result without knowledge held only by the author.

Clean-room reproducibility is therefore the entire proposition of v1.0. Hardening and documentation
are means of reaching that observation, not a substitute for it.

## Scope

**In:** a strict `make demo` release path · fresh-database verification · locked dependency setup ·
full gate execution · five-scenario evaluation packaging · persisted evaluation traces · README
prerequisites and scope language · the dated design document · `POST /webhooks/github` · raw-body
GitHub HMAC verification · a documented tunnel workflow · `make demo-live` · stored trace
inspection.

**Out:** production reliability · a task queue · crash-durable investigation scheduling · exactly-once
Slack or GitHub delivery · cron, polling, filesystem watching, or Kubernetes watches · live log,
Terraform, or cloud integrations · a swarm baseline · matched-budget comparisons · repeated trials ·
a held-out corpus · semantic entailment checking between claims and cited rows.

## Global constraints

- Python `>=3.11`; the release environment is installed with `uv`.
- SQLAlchemy and Alembic over PostgreSQL with pgvector.
- FastMCP over spawned stdio.
- FastAPI for HTTP.
- Pydantic at all external and stored-JSON boundaries.
- One agent using `anthropic[mcp]` with `claude-opus-5`.
- OpenAI `text-embedding-3-small` with `dimensions=1024`.
- Exactly three MCP tools and aggregates-only agent access.
- `investigate(request, run_context)` keeps its signature.
- The current citation grammar and evidence identities are unchanged:
  `commit:<full-40-character-sha>` · `deploy:<uid>` · `infra:<uid>` ·
  `rollup:<service>/<iso8601>/<status_class>/<level>/<template_hash>` · `log:<uid>` ·
  `postmortem:<slug>@<content_sha8>#<ordinal>`.
- The only project gates are `uv run ruff check .`, `uv run mypy --strict src`, and
  `uv run pytest -q`. `ruff format` and `mypy --strict src tests` are not added as release gates.

---

## Part 3 seam assumed by this specification

Part 4 assumes Part 3 lands the following exact behavior.

1. `create_app(settings)` owns a lifespan-created SQLAlchemy `Engine`.
2. `POST /webhooks/alert`, `/healthz`, `run_incident`, and `aegis serve-api` exist.
3. `app/records.py` owns this `incidents.summary_json` envelope:

   ```json
   {
     "run_id": "e3f1c9a24b7d4e0fa1b2c3d4e5f60718",
     "summary": null,
     "trace": [],
     "delivery": null
   }
   ```

4. On success, `summary` contains `IncidentSummary.model_dump(mode="json")`; on failure it remains
   `null`.
5. `trace` preserves `RunContext` event order and contains the terminal event emitted by
   `investigate`.
6. `summary_json.run_id` is written before the agent starts.
7. `DatabaseSink` and `run_incident` persist that envelope without changing
   `investigate(request, run_context)`.
8. `app/render.py` exposes the renderer moved from the CLI.

The trace view in §7 depends on this stored shape. Part 4 does not introduce a second trace schema.

---

## 0. Deliberate contract changes

### 0.1 No database, citation, MCP, or agent-interface change

Part 4 adds no migration. It does not change evidence identity, citation syntax, time-window
semantics, response ordering, `IncidentSummary`, provenance validation, or the number of tools.

The GitHub endpoint adapts external payloads into the existing `GitExport`/`GitCommit` ingest path.
It does not give the agent direct GitHub access.

### 0.2 Ollama is not a v1.0 dependency

Revision 3 of Part 2 replaced Ollama embeddings with OpenAI embeddings. An Ollama reachability check
would make a correctly configured clean machine unhealthy because of a service no request uses.

`/healthz` must therefore perform the Part 3 contract:

- PostgreSQL is probed with `SELECT 1`.
- Embedding readiness is configuration-only: a non-empty OpenAI key, the expected model family, and
  `embedding_dim == 1024`.
- It does not call OpenAI on every health request.
- It does not probe Ollama.

`ollama_base_url` is removed from `Settings` and `.env.example` in v1.0. Configuration fields were
explicitly unfrozen in Part 0. A legacy `AEGIS_OLLAMA_BASE_URL` remains harmless because settings
ignore unrelated environment values, but it is not documented and cannot affect health.

The live embedding acceptance in `make demo` is the provider-reachability observation; `/healthz`
must not spend money or turn an upstream rate limit into local unhealthiness.

### 0.3 Webhook absence takes precedence over hunk budgets

A GitHub push provides changed paths but no patch content. Every file originating from that payload
must therefore carry:

```json
{"hunks": null, "hunks_omitted": "webhook"}
```

This remains true when the push contains more than `hunk_max_files`. The content is missing because
the source did not send it, not because Aegis discarded it for budget.

`files_changed_for_commit()` changes its precedence accordingly:

```python
if file.hunks is None:
    hunks = None
    omitted = "webhook"
elif file.path not in hunk_paths or _exceeds_hunk_budget(file.hunks, settings):
    hunks = None
    omitted = "budget"
else:
    hunks = file.hunks
    omitted = None
```

A test with more than `hunk_max_files` webhook paths is required. A small fixture would not expose
the existing opposite precedence.

### 0.4 Pull-request metadata is limited enrichment

`pull_request` payloads do not contain the changed-path list or patch text required to construct a
truthful new `Commit` row. Part 4 therefore does not fabricate one.

A signed `pull_request` event may set `pr_number` on an already-ingested head or merge commit
belonging to the same registered repository. It never overwrites a different non-null PR number and
never inserts a commit with empty or invented file evidence.

This is a limited enrichment of nullable metadata. It does not change the commit SHA, timestamps,
message, author, file evidence, or citation.

---

## 1. `README.md` — prerequisites, commands, and claim scope

The README is the operator contract for v1.0. Internal implementation specifications do not satisfy
this requirement.

Its top-level sections, in order, are:

1. `What Aegis Context does`
2. `What v1.0 demonstrates`
3. `What v1.0 does not demonstrate`
4. `Prerequisites`
5. `Run the reproducible demo`
6. `Inspect a run`
7. `Run the API`
8. `Run the GitHub live demo`
9. `Development gates`
10. `Operational limits`

### 1.1 Required feasibility wording

The README contains this paragraph without qualification elsewhere:

> Aegis Context v1.0 is a feasibility demonstration. Five planted scenarios show that one agent can
> correlate pre-ingested deploy, infrastructure, telemetry, and postmortem evidence through three
> aggregate MCP tools. They do not establish that Aegis Context is more accurate, cheaper, faster,
> or more reliable than a multi-agent system. No swarm baseline, matched-budget comparison,
> repeated-trial analysis, or held-out external corpus is included.

No user-facing file may say or imply that v1.0 beats, outperforms, replaces, or is superior to a
multi-agent system.

### 1.2 Required provenance wording

The README also contains this paragraph:

> Provenance validation proves only that each citation in a displayed claim was returned by one of
> the three MCP tools during that investigation. It rejects malformed, fabricated, and unseen
> identifiers. It does not prove that the cited row semantically supports the sentence attached to
> it. Semantic support is evaluated by the planted scenario contracts or by a human reviewer.

No user-facing file may describe provenance as fact checking, entailment checking, truth
verification, or proof that the diagnosis is correct.

### 1.3 Mechanical and human claim checks

`tests/docs/test_claim_scope.py` performs two checks.

First, it asserts both canonical paragraphs are present in `README.md`.

Second, it extracts normalized paragraphs containing any of these terms from `README.md`,
`docs/**/*.md`, `src/**/*.py`, and `pyproject.toml`:

```text
beat · better · outperform · superior · superiority · prove · proven · provenance
support · supported · guarantee · accurate · accuracy · correct
```

The extracted paragraphs are compared with a committed reviewed snapshot in
`tests/docs/claim_scope_approved.txt`. A new or changed claim-bearing paragraph fails until a human
reviews and deliberately updates the snapshot.

The release checklist also runs:

```bash
rg -n -i \
  '(beat|better|outperform|superior|prove|provenance|support|guarantee|accurate|correct)' \
  README.md docs src pyproject.toml
```

The snapshot is a change detector, not a semantic proof. The clean-room reviewer must inspect every
match. Paraphrases remain a human-review problem and are stated as such.

### 1.4 Prerequisites

The README names every prerequisite before the first command:

- Git.
- `make`.
- `uv`.
- outbound HTTPS access to Anthropic and OpenAI.
- a funded `ANTHROPIC_API_KEY`.
- a funded `OPENAI_API_KEY`.
- Docker with Docker Compose for the authoritative release observation; or PostgreSQL 14+ with
  pgvector for the local external-database rehearsal.
- `cloudflared` or ngrok only for `make demo-live`.

The README explicitly warns that `make demo` performs five paid agent runs and live embedding calls.
The command itself is the operator's explicit request to incur them; it must not pause for an
interactive confirmation.

### 1.5 Homebrew PostgreSQL path

Docker is the authoritative clean-room path, but it is not usable on the current development
machine. The README therefore provides an honest second path using a separate disposable database,
never the existing `aegis` database:

```bash
createdb -O aegis aegis_demo

AEGIS_DEMO_DB_MODE=external \
AEGIS_DATABASE_URL=postgresql+psycopg://aegis:aegis@127.0.0.1:5432/aegis_demo \
make demo
```

If the role cannot create the `vector` extension, the README gives the administrator step:

```bash
psql -d aegis_demo -c 'CREATE EXTENSION IF NOT EXISTS vector'
```

The external run is a local rehearsal of the same commands and contracts. It is not recorded as the
fresh-Docker release observation, because PostgreSQL 14 on an already-configured Homebrew server is
not a new machine or the release's PostgreSQL 17 container.

---

## 2. `Makefile` and `release/demo.py` — the reproducible command

**Files:** create `Makefile`, `src/aegis/release/__init__.py`,
`src/aegis/release/demo.py`; modify `docker-compose.yml`.

The Makefile remains a thin published interface. Python owns branching, diagnostics, subprocess
environment construction, database inspection, and cleanup advice.

```make
SHELL := /bin/sh
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

.PHONY: demo demo-live

demo:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required; install uv and rerun make demo" >&2; exit 2; \
	}
	@cd "$(ROOT)" && uv sync --frozen --all-groups
	@cd "$(ROOT)" && uv run python -m aegis.release.demo offline

demo-live:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required; install uv and rerun make demo-live" >&2; exit 2; \
	}
	@cd "$(ROOT)" && uv sync --frozen --all-groups
	@cd "$(ROOT)" && uv run python -m aegis.release.demo live
```

No target invokes `ruff format` or `mypy --strict src tests`.

### 2.1 Release coordinator surface

```python
DemoDatabaseMode = Literal["compose", "external"]

@dataclass(frozen=True)
class DemoOptions:
    root: Path
    database_mode: DemoDatabaseMode
    database_url: str
    compose_project: str = "aegis-context-demo"

@dataclass(frozen=True)
class DemoResult:
    corpus_digest: str
    scenario_results: tuple["ScenarioResult", ...]
    clean_start: bool

def preflight(options: DemoOptions) -> None: ...
def run_offline_demo(options: DemoOptions) -> DemoResult: ...
def run_live_demo(settings: Settings, options: "LiveDemoOptions") -> None: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Every child command is executed with `subprocess.run(argv, cwd=root, env=environment, check=True)`.
No `shell=True`, interpolated secret, or ambient working directory is permitted.

The coordinator constructs one explicit child environment containing at least:

```text
AEGIS_DATABASE_URL
AEGIS_CORPUS_DIR=<absolute repository>/corpus
AEGIS_OPENAI_BASE_URL
AEGIS_EMBEDDING_MODEL
OPENAI_API_KEY
ANTHROPIC_API_KEY
AEGIS_DEMO_MODE=1
AEGIS_REQUIRE_POSTGRES=1
AEGIS_REQUIRE_LIVE_EVAL=1
```

It passes the same environment to the CLI, pytest, and every spawned MCP server. It never prints
secret values. Database URLs are rendered with their passwords replaced by `***`.

This explicit propagation is required because `StdioServerParameters` does not inherit the parent
environment. A parent process reaching the demo database while its MCP child silently reads the
default database is a release-falsifying defect.

### 2.2 Preflight

Preflight runs before starting Docker or mutating a database and fails on the first unmet
prerequisite.

It verifies:

1. `uv.lock`, `alembic.ini`, `corpus/services.yaml`, and exactly five scenario manifests exist.
2. `OPENAI_API_KEY` is present after stripping whitespace.
3. `ANTHROPIC_API_KEY` is present after stripping whitespace.
4. neither key equals an empty or documented placeholder value;
5. the database mode is exactly `compose` or `external`;
6. Docker and `docker compose version` work in compose mode;
7. the external database URL is explicitly supplied in external mode;
8. every committed corpus file named by its manifest exists;
9. no scenario name or dedup key is duplicated;
10. `claude-opus-5`, `text-embedding-3-small`, and `embedding_dim=1024` are the effective settings.

Missing credentials produce, for example:

```text
ERROR [prerequisites]: ANTHROPIC_API_KEY is unset.
The five agent evaluations cannot run and skipped evaluations do not satisfy make demo.
```

An empty `OPENAI_API_KEY=` is treated as missing rather than configured.

### 2.3 Database modes and empty-state proof

In `compose` mode the coordinator starts only the repository's PostgreSQL service:

```bash
docker compose -p aegis-context-demo up -d postgres
```

`docker-compose.yml` continues to use PostgreSQL 17 with pgvector and exposes the port through
`${AEGIS_POSTGRES_PORT:-5433}`. The compose project name gives the demo its own named volume instead
of reusing an unrelated developer project.

The coordinator waits for both container health and a successful SQLAlchemy `SELECT 1`. Container
health alone is insufficient: a mapped port conflict, wrong credentials, or connection to another
local server can leave the container healthy while the application reaches something else.

In `external` mode it never creates, drops, truncates, or resets a database. The supplied database
must be disposable and empty.

Before migration, both modes execute:

```sql
SELECT tablename
FROM pg_catalog.pg_tables
WHERE schemaname = current_schema()
  AND tablename IN (
    'alembic_version', 'services', 'commits', 'deployments',
    'infra_changes', 'log_events', 'unresolved_events',
    'error_rollups', 'postmortems', 'postmortem_chunks',
    'incidents', 'ingest_watermarks'
  )
ORDER BY tablename;
```

Any returned row fails the clean-room run:

```text
ERROR [database]: demo database is not empty; found: alembic_version, services, ...
Use a new disposable database. No rows were deleted.
```

For an old compose demo volume, the diagnostic may print the explicit recoverable cleanup command:

```bash
docker compose -p aegis-context-demo down -v
```

The command is never run automatically. On failure or success the database remains available for
trace inspection.

### 2.4 `make demo` execution order

The coordinator prints and runs these stages in this order:

```text
[1/10] prerequisites and corpus manifest
[2/10] database provisioning and SQL readiness
[3/10] Alembic migration and pgvector verification
[4/10] ruff gate
[5/10] mypy gate
[6/10] corpus ingest pass 1
[7/10] corpus ingest pass 2 and replay digest
[8/10] full pytest gate, including five live evaluations
[9/10] persisted trace and evaluation inspection
[10/10] scope and completion report
```

The concrete commands are:

```bash
uv run aegis db upgrade
uv run ruff check .
uv run mypy --strict src
uv run aegis ingest all
uv run aegis ingest all
uv run pytest -q
```

After migration, the coordinator verifies:

```sql
SELECT version_num FROM alembic_version;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

Missing `alembic_version`, a revision other than head, or no `vector` row is a failure before any
paid model call.

### 2.5 Replay digest

After each `aegis ingest all`, the coordinator records:

- exact counts for every evidence table;
- exact non-zero counts per configured source family;
- a SHA-256 digest over canonical, natural-key-ordered content.

The digest excludes sequence primary keys and `created_at`, which legitimately depend on database
allocation and wall time. It includes evidence identities, source offsets, timestamps, content
fields, rollup keys and counts, postmortem content hashes, and model fingerprints.

The second ingest must produce:

- no additional immutable evidence rows;
- no changed rollup count or exemplar identity;
- the same content digest;
- unchanged postmortem chunks when both `content_sha` and `model_fingerprint` match.

Equality of two zero-count runs is not enough. Each source declared by the complete Part 2 corpus
must have its exact expected non-zero count asserted by the corpus-contract suite.

### 2.6 Successful output

The final output contains one line per scenario:

```text
PASS checkout-5xx-spike confidence=high incident_id=41 run_id=<id>
     inspect: uv run aegis trace --run-id <id>
```

It then prints:

```text
PASS 5/5 live evaluations
PASS corpus replay digest <sha256>
PASS clean_start=true
SCOPE feasibility, not superiority; provenance, not semantic support
DEMO COMPLETE
```

Any subprocess non-zero exit stops the run immediately, preserves its output, names the failed stage,
and exits non-zero. Later stages must not run after an earlier failure.

---

## 3. `tests/eval/` — hardened five-scenario packaging

Part 2 creates the five-scenario evaluation suite. Part 4 changes its execution packaging so a single
`pytest` invocation cannot succeed while the milestone's defining observations are skipped.

### 3.1 Exactly one paid agent run per scenario

The suite exposes one parameterized test case per sorted scenario manifest:

```python
@dataclass(frozen=True)
class EvaluationCase:
    name: str
    path: Path
    request: InvestigationRequest
    expect: Mapping[str, Any]

@dataclass(frozen=True)
class EvaluationResult:
    case: EvaluationCase
    incident_id: int
    run_id: str
    summary: IncidentSummary
    captured_cites: frozenset[str]

def load_evaluation_cases(directory: Path) -> tuple[EvaluationCase, ...]: ...
def evaluate_case(
    case: EvaluationCase,
    *,
    settings: Settings,
    engine: Engine,
    persist: bool,
) -> EvaluationResult: ...
```

All semantic assertions for one scenario run against that one `EvaluationResult`. Eight assertion
functions must not each invoke the model through a function-scoped summary fixture. That multiplies
cost, introduces cross-assertion nondeterminism, and can let one scenario produce several mutually
inconsistent summaries.

There are exactly five model runs, no automatic retry, and no "best of N" selection.

### 3.2 Strict demo mode

Ordinary developer runs may continue to skip live evaluations when credentials are absent.
`make demo` sets `AEGIS_REQUIRE_LIVE_EVAL=1`.

Under that setting:

- missing OpenAI or Anthropic credentials cause collection to fail, not skip;
- an unavailable PostgreSQL instance fails the session fixture, not `pytest.skip`;
- a live-provider test cannot replace OpenAI with `FixtureEmbeddings`;
- exactly five evaluation cases must be collected;
- any skipped evaluation fails the session in `pytest_sessionfinish`;
- the suite records the five observed run ids for the release report.

This closes the false-green path where `pytest` exits zero with every paid assertion skipped.

### 3.3 Evaluation incident persistence

When `AEGIS_DEMO_MODE=1`, each evaluation creates a real incident row before calling Part 3's
`run_incident`:

```sql
INSERT INTO incidents (
    dedup_key, service_id, opened_at, window_start, window_end,
    alert_payload, status, created_at
) VALUES (
    :dedup_key, :service_id, :opened_at, :window_start, :window_end,
    CAST(:alert_payload AS jsonb), 'open', now()
)
RETURNING id;
```

The dedup key is `demo-eval:<scenario-name>` and is unique inside the empty demo database.
`opened_at`, `window_start`, `window_end`, service, and payload come from the scenario's alert
allowlist.

The test then calls:

```python
run_incident(
    incident_id,
    case.request,
    settings.model_copy(update={"slack_webhook_url": None}),
    engine,
)
```

Slack is explicitly disabled even if the author's `.env` contains a webhook URL. `make demo` must
not send external Slack messages as an accidental side effect.

`run_incident` persists the production `summary_json` envelope and swallows agent failures by
contract. The evaluation therefore re-reads the incident and fails unless:

- `status == "summarized"`;
- `summary_json.summary` validates as `IncidentSummary`;
- `summary_json.run_id` is non-empty;
- `summary_json.trace` contains tool results and one completed terminal event;
- every displayed citation occurs in a captured tool result from that trace.

The rows commit only in demo mode so they remain available to `aegis trace`. Normal test execution
uses rollback-isolated or in-memory traces and does not pollute a developer database.

### 3.4 Per-scenario assertions

Each scenario asserts:

1. the expected service;
2. all required root-cause terms;
3. the required citation subset, with globs allowed only where the scenario contract declares them;
4. every forbidden root-cause condition;
5. every required ruled-out candidate;
6. the minimum confidence;
7. provenance validation against citations captured during that run;
8. the ground-truth canary absent from the brief, model trace, and summary;
9. primary evidence reachable with Tool 3 absent through the reduced spawned-stdio server;
10. no scenario horizon overlapping another scenario's baseline or Tool 1 lookback horizon.

The evaluator does not require byte-identical natural language or an exact citation set. The agent
is nondeterministic; the falsifiable contract is the required semantic subset plus explicit
forbidden conditions.

### 3.5 Live embedding acceptance

The full suite contains a live-provider test that:

1. embeds the committed postmortem corpus with OpenAI;
2. asserts every response index is accounted for and every vector is finite, non-zero, and 1024
   dimensions;
3. queries each known error signature;
4. verifies its intended postmortem is retrieved above the configured similarity floor.

`AEGIS_REQUIRE_LIVE_EVAL=1` prohibits this test from using fixture vectors or skipping after an
authentication failure.

---

## 4. `docs/2026-08-19-aegis-context-design.md`

This document explains the product that exists at v1.0. It is not a second implementation plan.

Its required sections are:

1. `Problem`
2. `Product boundary`
3. `Architecture`
4. `Evidence identity and time`
5. `Data model`
6. `Interfaces`
7. `Operational workflow`
8. `Known limits`

### 4.1 Required content

**Problem:** incident evidence is fragmented across deploy history, infrastructure changes,
telemetry, and postmortems; reparsing prose across multiple agent hops loses shared identity,
ordering, and provenance.

**Architecture:** correlation occurs at ingest into one PostgreSQL schema using shared immutable
service identifiers and one UTC clock. One agent receives three aggregate tools over spawned stdio.
The document names the fixed Anthropic and OpenAI models and distinguishes the offline corpus from
the single GitHub live-demo path.

**Evidence identity and time:** stable content-derived uids, full commit SHAs, half-open windows,
minute snapping, deterministic ordering, and the current citation grammar.

**Data model:** services, commits, deployments, infra changes, log events, rollups, postmortems and
chunks, incidents, unresolved events, and watermarks, including their important relationships.

**Interfaces:**

- `aegis ingest all`
- `aegis investigate`
- `aegis serve-mcp`
- `aegis serve-api`
- `aegis trace`
- `POST /webhooks/alert`
- `POST /webhooks/github`
- `GET /healthz`
- exactly three MCP tools:
  `get_incident_diff`, `get_error_telemetry`, and `search_similar_postmortems`.

**Known limits:** the two README scope statements, manual ingest through v1.0, BackgroundTasks crash
window, best-effort Slack, remote-provider dependence, GitHub paths without patches, and no live log
or infrastructure ingestion.

### 4.2 Forbidden content

The design document contains no:

- build order;
- acceptance criteria;
- exit criteria;
- numbered implementation tasks;
- release checklist;
- file-by-file change list;
- test inventory.

`tests/docs/test_design_document.py` parses headings and requires the four core subjects—problem,
architecture, data model, interfaces—while rejecting headings matching:

```text
implementation · build order · acceptance · exit criteria · task list · checklist
```

Commands and prerequisites live in `README.md`; implementation details live in the versioned Part
specifications.

---

## 5. `api/webhooks.py` — `POST /webhooks/github`

Part 4 extends the Part 3 router. The endpoint is the only path where genuinely live evidence enters
the v1.0 system.

### 5.1 Handler and adapters

```python
@dataclass(frozen=True)
class GitHubPushResult:
    delivery_id: str | None
    repo: str
    inserted: int
    unchanged: int

@dataclass(frozen=True)
class GitHubPullRequestResult:
    delivery_id: str | None
    repo: str
    pr_number: int
    updated_shas: tuple[str, ...]
    deferred_shas: tuple[str, ...]

async def github_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    engine: Engine = Depends(get_engine),
) -> Response: ...

def verify_github_signature(*, body: bytes, supplied: str | None, secret: str) -> bool: ...
def github_push_export(payload: Mapping[str, Any]) -> GitExport: ...
def enrich_pull_request(
    session: Session,
    payload: Mapping[str, Any],
    registry: ServiceRegistry,
) -> GitHubPullRequestResult: ...
```

### 5.2 Verification order

The endpoint performs these operations in this exact order:

1. Read the configured secret.
2. If it is absent or blank, return `503`.
3. Reject an advertised or actual body above 2 MiB with `413`.
4. Read `body = await request.body()` once.
5. Compute the expected HMAC over those exact bytes.
6. Compare it with `X-Hub-Signature-256` using `hmac.compare_digest`.
7. On mismatch or a missing signature, return `401`.
8. Read `X-GitHub-Event`.
9. For events other than `push` or `pull_request`, return `202` without ingesting.
10. Only now parse the authenticated bytes as JSON.

The verification implementation is:

```python
digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
expected = f"sha256={digest}"
return hmac.compare_digest(expected, supplied or "")
```

The code must not parse and re-serialize JSON before verification. Whitespace, escape spellings, and
object-key order are part of the signed byte sequence.

The endpoint never logs the secret, signature, or raw body. Structured logs may include delivery id,
event name, normalized repository, and inserted/unchanged counts.

### 5.3 Responses

| Condition | Status |
| --- | --- |
| Secret absent or blank | `503` |
| Signature absent or invalid | `401` |
| Body too large | `413` |
| Signed payload is invalid JSON | `400` |
| Missing or malformed required GitHub fields | `422` |
| Repository is not registered to exactly one persisted service | `422` |
| Signed unsupported event, including `ping` | `202` |
| Valid push committed | `202` |
| Valid pull-request enrichment or deferral | `202` |

An unregistered repository is rejected loudly. It is not placed in `unresolved_events`, whose
identity and columns are log-event-specific. Returning success while inserting nothing would make
the documented live demo silently fail at the exact attribution seam it is intended to prove.

### 5.4 Push mapping

The adapter normalizes `repository.full_name` with the existing repo-normalization function and
maps every payload commit into `GitCommit`.

| GitHub field | Aegis field |
| --- | --- |
| `id` | `sha` |
| `timestamp` | `authored_at` and `committed_at` |
| `message` | `message` |
| `author.name` | `author` |
| `added[]` | `status="added"` |
| `modified[]` | `status="modified"` |
| `removed[]` | `status="removed"` |
| unavailable | `pr_number=None` |
| unavailable line counts | `additions=0`, `deletions=0` |
| unavailable patches | `hunks=None` |

Paths must be non-empty and unique across all three arrays. A path appearing in more than one array
rejects the complete payload; the transaction must not partially insert earlier commits.

`deploys=[]`. A GitHub push is commit evidence, not deployment evidence.

If a payload declares more commits than its `commits` array contains, it is rejected as truncated
rather than silently recording partial history. The v1.0 live demo deliberately pushes one commit.

Persistence goes through the existing function:

```python
with Session(engine) as session, session.begin():
    registry = registry_from_session(session)
    result = upsert_commits(
        session,
        github_push_export(payload),
        registry,
        settings=settings,
    )
```

Its effective SQL remains:

```sql
INSERT INTO commits (
    sha, service_id, authored_at, committed_at, message,
    author, pr_number, files_changed, additions, deletions
) VALUES (
    :sha, :service_id, :authored_at, :committed_at, :message,
    :author, NULL, CAST(:files_changed AS jsonb), 0, 0
)
ON CONFLICT (sha) DO NOTHING
RETURNING sha;
```

The response is not returned until the transaction commits. Otherwise `make demo-live` can receive
`202`, immediately query through MCP, and fail to see a commit that is still uncommitted.

GitHub retrying the same delivery is idempotent at the commit SHA. The endpoint does not claim
exactly-once webhook processing.

### 5.5 Pull-request mapping

For a signed `pull_request` event, normalize `repository.full_name`, validate the PR number, and
collect the valid 40-character lowercase SHAs from:

- `pull_request.head.sha`;
- `pull_request.merge_commit_sha`, when non-null.

For each SHA, execute:

```sql
UPDATE commits AS c
   SET pr_number = :pr_number
  FROM services AS s
 WHERE c.sha = :sha
   AND c.service_id = s.id
   AND s.repo = :normalized_repo
   AND (c.pr_number IS NULL OR c.pr_number = :pr_number)
RETURNING c.sha;
```

Outcomes:

- existing commit with null PR number: updated;
- existing commit already carrying the same number: unchanged success;
- existing commit carrying a different number: `409`, with no updates committed;
- missing commit: reported in `deferred_shas`, with no fabricated insert.

Deferral is best-effort and not crash-durable. A later push does not retroactively replay the PR
event. The v1.0 live claim depends only on push ingestion, and the README states this ordering limit.

### 5.6 GitHub endpoint tests

Tests include:

- missing secret → `503`, with no parsing or database access;
- missing signature → `401`;
- bad signature → `401`;
- signature of a re-serialized object rejected when it differs from the raw bytes;
- signature of the original whitespace-sensitive body accepted;
- signed malformed JSON → `400`;
- signed `ping` → `202`, no rows;
- unknown repo → `422`, no rows;
- malformed second commit causes zero inserts for the whole payload;
- replay returns unchanged counts and one commit row;
- more than `hunk_max_files` still yields `"hunks_omitted": "webhook"` for every path;
- PR enrichment is idempotent;
- PR-before-push reports deferral and inserts nothing;
- conflicting PR metadata rolls back the whole enrichment;
- a subsequent spawned-stdio `get_incident_diff` contains the new commit and citation.

---

## 6. `make demo-live` — the tunnelled push observation

`make demo-live` is a verifier and coordinator around a real manual push. It does not create a
repository, configure GitHub, fabricate a webhook call, commit, or push on the operator's behalf.

### 6.1 Required environment

```text
AEGIS_GITHUB_WEBHOOK_SECRET
AEGIS_DATABASE_URL
AEGIS_CORPUS_DIR
GITHUB_REPO
PUBLIC_BASE_URL
AEGIS_LIVE_DEMO_TIMEOUT_SECONDS   # default 300
```

`GITHUB_REPO` is normalized to `owner/name`. `PUBLIC_BASE_URL` is the HTTPS tunnel origin without
`/webhooks/github`.

### 6.2 README sequence

The README gives the complete sequence.

1. Create or select a disposable scratch repository.
2. Add its exact normalized `owner/name` to `corpus/services.yaml`.
3. Load the registry:

   ```bash
   uv run aegis ingest services
   ```

4. Set the same strong secret in the local process and the GitHub webhook settings.
5. Start the API:

   ```bash
   uv run aegis serve-api --host 127.0.0.1 --port 8000
   ```

6. Start either tunnel:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

   or:

   ```bash
   ngrok http 8000
   ```

7. Configure the scratch repository webhook:

   ```text
   Payload URL: <public-origin>/webhooks/github
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
10. Verify GitHub reports a `202` delivery and retain its delivery id with the release observation.

### 6.3 Verification performed by the target

Before prompting for the push, the target:

1. verifies the secret is non-empty;
2. verifies `GITHUB_REPO` resolves to exactly one entry in `corpus/services.yaml`;
3. verifies that same repo is loaded into the database;
4. calls local `http://127.0.0.1:8000/healthz`;
5. calls `${PUBLIC_BASE_URL}/healthz`;
6. fails if either response is not `200`;
7. snapshots the service's existing commit SHAs.

The public health request distinguishes "the tunnel hostname resolves" from "the tunnel routes to
this API." A DNS-successful tunnel returning 404, 502, or another application's health response is a
failure before the operator pushes.

After `READY`, the target polls the database until timeout for a new commit belonging to the
registered service. For the first new SHA, it asserts:

- at least one changed path;
- every path is non-empty;
- every `hunks` value is null;
- every `hunks_omitted` value is `"webhook"`;
- no deployment row was fabricated.

It then invokes `get_incident_diff` through the spawned stdio MCP server using a window derived from
the new commit's `committed_at`. The returned focus block must contain `commit:<new-sha>` and the
same changed paths.

Success prints:

```text
LIVE DEMO PASS
repo=owner/scratch
commit=<full-sha>
citation=commit:<full-sha>
hunks_omitted=webhook
```

A direct synthetic POST can test HMAC plumbing but cannot satisfy this exit observation. The
release path is incomplete until GitHub has actually delivered a real push through the tunnel.

---

## 7. `agent/trace_view.py` — stored run inspection

**Files:** create `src/aegis/agent/trace_view.py`; modify `src/aegis/cli.py`;
tests in `tests/unit/test_trace_view.py` and `tests/integration/test_trace_view_db.py`.

### 7.1 Public surface

```python
class TraceIntegrityError(ValueError):
    """Stored trace and stored summary disagree or do not match the v0.3 envelope."""

@dataclass(frozen=True)
class StoredRun:
    incident_id: int
    dedup_key: str
    incident_status: str
    record: IncidentRecord

def load_stored_run(session: Session, *, run_id: str) -> StoredRun: ...
def render_trace(run: StoredRun, *, include_payloads: bool = False) -> str: ...
def validate_trace_integrity(run: StoredRun) -> None: ...
```

CLI:

```python
@app.command("trace")
def trace_command(
    run_id: str = typer.Option(..., "--run-id"),
    json_output: bool = typer.Option(False, "--json"),
    full: bool = typer.Option(False, "--full"),
) -> None: ...
```

`--json` emits the validated stored `IncidentRecord` with sorted keys. `--full` includes complete
tool arguments and result payloads in the text view. Default text remains compact.

### 7.2 Lookup SQL

```sql
SELECT id, dedup_key, status, summary_json
FROM incidents
WHERE summary_json->>'run_id' = :run_id
ORDER BY id
LIMIT 2;
```

Zero rows produces a not-found error and exit code 1. Two rows produce
`TraceIntegrityError("run_id is not unique")` and exit code 2. The command is read-only and never
changes incident state.

No JSONB index is added in v1.0. The corpus and incident set are small, and adding a migration solely
for an interactive release view is unnecessary.

### 7.3 Text rendering

The renderer preserves stored event order and numbers events by list position:

```text
Run <run_id>
Incident 41 · demo-eval:checkout-5xx-spike · summarized
Delivery: not attempted

000 agent_turn turn=1 stop_reason=tool_use
001 tool_result get_incident_diff cites=3
002 tool_result get_error_telemetry cites=18
003 agent_turn turn=2 stop_reason=end_turn
004 terminal status=completed

Root cause: ...
  commit:...                         captured at event 001
  rollup:checkout-api/...            captured at event 002
```

For known event kinds it extracts stable fields. Unknown kinds are rendered generically rather than
dropped, so an additive future trace event remains visible.

Citation harvesting uses the same named fields as `RunContext`:
`cite`, `source_cites`, `baseline_cites`, and `resolution_cite`, walking nested lists and models.

### 7.4 Integrity checks

The viewer validates:

1. `summary_json` as Part 3's `IncidentRecord`;
2. requested `run_id == record.run_id`;
3. at most one terminal event;
4. a summarized incident has a non-null summary and a completed terminal event;
5. a failed incident has a null summary and a failed terminal event;
6. every citation in the stored summary occurs in an earlier `tool_result`;
7. delivery fields form a valid `DeliveryOutcome` when present.

A missing citation is rendered as `MISSING FROM TRACE` and causes exit code 2 after rendering the
rest of the trace.

The viewer does not query current evidence rows. A rollup can legitimately change after late ingest,
and the stored tool result is what the agent actually saw. It also does not claim the cited value
supports the sentence; it exposes the trail for a human to inspect.

---

## 8. Tests

### 8.1 Release-coordinator unit tests

- Empty and whitespace-only keys fail before any database or Docker call.
- Secret values never appear in diagnostics, command lines, or result objects.
- Passwords in database URLs are redacted.
- `compose` without Docker names the external-mode alternative.
- `external` without an explicit URL fails.
- a non-empty project schema fails without deleting anything;
- an empty schema containing only the pre-created `vector` extension is accepted;
- a missing or wrong Alembic revision fails;
- corpus digest ignores sequence ids and `created_at`;
- digest changes when a rollup count, exemplar uid, source offset, or postmortem fingerprint changes;
- second ingest with equal zero source counts fails the non-zero-source assertion;
- subprocess failure stops later stages and returns the same non-zero status;
- commands run from repository root even when `make -C` is invoked elsewhere.

### 8.2 Documentation tests

- canonical feasibility paragraph present;
- canonical provenance paragraph present;
- claim-bearing paragraph snapshot unchanged;
- README lists both database paths and both required API keys;
- README contains the exact `make demo` and `make demo-live` commands;
- README does not list Ollama as a prerequisite;
- design document contains its required subjects;
- design document contains no planning or acceptance headings;
- README and design doc name exactly three MCP tools.

### 8.3 Evaluation tests

- exactly five scenario cases collected;
- one `investigate`/`run_incident` call per case;
- no automatic retry;
- strict mode turns missing credentials and PostgreSQL into failures;
- strict mode fails if any live evaluation skips;
- each persisted summary has one distinct run id;
- each summary's citations are trace-reachable;
- an intentionally poisoned stored summary fails trace integrity;
- the five scenario-specific semantic and forbidden assertions all run against their one result.

### 8.4 GitHub tests

The endpoint tests in §5.6 include the app, database transaction, registry resolution, existing Git
ingest adapter, and spawned MCP tool. Unit-testing the HMAC helper and adapter separately is not
enough.

### 8.5 Clean-room release test

The release acceptance run is performed on a Docker-capable machine or disposable CI worker with:

- a fresh clone;
- no existing `aegis-context-demo` compose project or volume;
- no project virtual environment;
- only the documented keys exported;
- no Ollama installation;
- no pre-existing project database.

The operator runs exactly:

```bash
make demo
```

The release record retains:

- repository commit;
- operating system and architecture;
- Python version selected by `uv`;
- PostgreSQL server version;
- pgvector extension version;
- Alembic revision;
- corpus digest;
- the five scenario names, incident ids, and run ids;
- the final `DEMO COMPLETE` output.

This is a release observation requiring credentials and paid calls. It is not silently replaced by
the ordinary offline CI suite.

---

## 9. Composition and clean-room risks

| Seam | What specifically breaks | Required detection |
| --- | --- | --- |
| Makefile → repository root | Relative `corpus/` or `alembic.ini` resolves against the caller's directory | Run the target through `make -C` from another directory |
| `uv.lock` → installed environment | A missing Part 3/4 dependency works only because it exists globally | `uv sync --frozen --all-groups` in a fresh environment |
| `.env` → settings | An empty key is mistaken for a configured key | Strip and validate secrets before mutation |
| pytest skip policy → release result | All paid evaluations or PostgreSQL tests skip and pytest exits zero | `AEGIS_REQUIRE_*` converts skip conditions to failures |
| Parent process → MCP child | The child reads the default database or lacks OpenAI configuration | Pass and assert the complete effective child environment |
| Docker health → application connection | Container is healthy while port 5433 reaches another server or wrong credentials | SQLAlchemy `SELECT 1` against the exact redacted URL |
| PostgreSQL package → pgvector extension | PostgreSQL accepts connections but migration fails because the control file or privileges are missing | Migrate, then query `pg_extension` |
| Fresh database → hidden author state | Existing schema or corpus makes migration and ingest appear reproducible | Refuse any project table before migration |
| Migration → CLI ingest | Tables exist but one new Part 2 loader is never called | Exact non-zero per-source counts through `aegis ingest all` |
| Ingest pass 1 → pass 2 | Duplicate rows or changed exemplars survive a row-count-only comparison | Natural-key content digest and exact rollup comparison |
| OpenAI response → postmortem chunks | Fixture embeddings or positional response pairing hides live retrieval defects | Strict live test, response-index validation, known-neighbour retrieval |
| Anthropic key → evaluation fixture | Function-scoped assertions run the model eight times or not at all | One parameterized paid call per scenario |
| Scenario file → model brief | `expect`, canary, or root-cause terms leak into the prompt | Outbound-request and persisted-trace canary checks |
| Evaluation → Part 3 persistence | In-memory tests pass but displayed summaries have no stored evidence trail | Demo-mode evaluation uses a real incident and `run_incident` |
| `DatabaseSink.flush` → release report | The sink swallows a database error by design and the demo still prints a summary | Re-read and validate all five persisted envelopes |
| Part 3 JSON envelope → trace view | A handler stores `RunContext.to_json()` nested under another key or overwrites delivery/trace | Validate the exact `IncidentRecord` shape |
| Summary → captured tool results | Provenance passed in memory but stored trace lost or reordered results | Re-harvest citations from stored tool-result events |
| `/healthz` → stale Ollama setting | A correct OpenAI installation reports unhealthy because no Ollama daemon exists | Clean-room run with no Ollama and a healthy endpoint |
| GitHub proxy → raw HMAC | Parsing or re-serialization changes signed bytes | Whitespace-sensitive raw-body signature test |
| Missing GitHub secret → public tunnel | Endpoint accepts unauthenticated payloads when configuration is absent | Missing secret always returns `503` |
| Tunnel hostname → local API | DNS resolves while the tunnel routes to 404, 502, or another service | Probe local and public `/healthz` before push |
| GitHub repo → service registry | Webhook returns success but the commit is unattributed and absent from Tool 1 | Preflight exact repo mapping and endpoint `422` |
| Push payload → hunk omission | More than 15 files are incorrectly labelled `"budget"` | Oversized path-list test requiring `"webhook"` throughout |
| Push transaction → MCP read | Endpoint returns before commit and immediate Tool 1 cannot see the row | Commit before `202`, then query through spawned stdio |
| GitHub retry → duplicate commit | Replayed delivery inserts duplicate evidence | SHA conflict yields unchanged and one row |
| Pull request → commit creation | Payload lacks paths, so inserting a row fabricates or permanently loses evidence | Enrichment-only contract; missing commit is deferred |
| Push size → partial history | GitHub truncates the commit array and Aegis silently claims full ingest | Compare declared count with array length and reject truncation |
| Ambient Slack config → offline demo | `make demo` posts five summaries to a real channel | Explicitly set `slack_webhook_url=None` for evaluations |
| Local PostgreSQL 14 → release PostgreSQL 17 | Author rehearsal passes on a preconfigured server but release image fails | Separate Docker clean-room observation remains mandatory |
| Remote providers → repeatability | Rate limits, model service changes, or network failures make live results non-bit-reproducible | Scope to semantic contracts; record model names and fail without retry selection |
| Live push → clock/window | A wall-clock-derived query misses a commit whose payload timestamp differs | Derive verification window from persisted `committed_at` |
| Error handling → misleading completion | A failed early command is followed by later PASS output | `check=True`, immediate stop, stage-labelled non-zero exit |

---

## 10. Exit criteria

Part 4 is complete only when all observations below exist.

1. On a Docker-capable clean machine, a fresh clone with no existing demo volume reaches
   `DEMO COMPLETE` through `make demo`.
2. The demo begins with no Aegis tables, migrates to Alembic head, and verifies pgvector.
3. `aegis ingest all` runs twice with identical content digests and exact non-zero counts for every
   Part 2 source family.
4. The exact gates pass:
   `uv run ruff check .`, `uv run mypy --strict src`, `uv run pytest -q`.
5. The pytest run contains five executed live evaluations and no skipped required evaluation.
6. All five scenarios satisfy their semantic, forbidden, confidence, reachability, leakage, and
   provenance contracts.
7. Five persisted incident envelopes contain five distinct run ids, summaries, tool-result traces,
   and completed terminal events.
8. `uv run aegis trace --run-id <id>` renders every displayed summary citation back to the tool event
   that returned it.
9. `make demo` succeeds without Ollama installed or running.
10. The Homebrew PostgreSQL 14 external-database path is rehearsed locally against a separately
    created empty `aegis_demo` database.
11. Missing Anthropic or OpenAI credentials fail informatively before database mutation; they never
    turn the release evaluation into a green skip.
12. Invalid GitHub signature → `401`; missing secret → `503`; signed unsupported event → `202`.
13. A valid GitHub push records changed paths with `hunks_omitted="webhook"` and no fabricated
    deployment.
14. `make demo-live` is executed once against a real scratch repository through cloudflared or ngrok;
    GitHub reports `202`, and the new commit appears in a subsequent spawned-stdio
    `get_incident_diff`.
15. README and design documentation contain the feasibility-not-superiority and
    provenance-not-support boundaries, and the repository claim review finds no contradiction.
16. The design document describes the product but contains no build order or acceptance criteria.

## 11. Deliberately does not prove

- Production reliability or high availability.
- Crash-durable background scheduling.
- Exactly-once Slack or webhook delivery.
- Robustness on external log distributions.
- Live logs, Terraform applies, Kubernetes watches, or cloud telemetry.
- That five planted scenarios represent real incident prevalence.
- That the agent's natural-language explanation is semantically entailed by each cited row.
- Any advantage over a swarm or other multi-agent architecture.
- Bit-for-bit repeatability of remote model output.
- Safe exposure of the unauthenticated generic alert endpoint beyond its documented loopback use.

---

## 12. Build order

1. Write the README scope paragraphs and dated design document first.
2. Add the claim-scope snapshot and design-document tests.
3. Harden `tests/eval/` to one paid run per scenario, strict no-skip demo mode, and persisted demo
   incidents.
4. Implement the release coordinator, empty-database probe, child environment construction, replay
   digest, and stage-labelled errors.
5. Add `make demo` and the isolated compose-project behavior.
6. Rehearse the full path against a new local Homebrew `aegis_demo` database; fix every dependency on
   ambient state.
7. Implement the trace loader, integrity validator, renderer, and CLI command.
8. Run `make demo` on a fresh Docker-capable machine and fix every undocumented prerequisite.
9. Correct the hunk-omission precedence, then implement raw-body GitHub verification and push
   ingestion.
10. Add pull-request enrichment and the full GitHub composition tests.
11. Implement the `make demo-live` verifier and README tunnel sequence.
12. Fire one real scratch-repository push through the tunnel and retain the delivery id, commit SHA,
    citation, and successful verifier output.

---

## 13. Resolved questions

1. **What makes v1.0 distinct?** The fresh-machine `make demo` observation. Documentation and
   hardening without it do not establish a new product claim.

2. **Does `make demo` reset a database?** No. It refuses a non-empty database and never deletes
   operator data. The authoritative release run uses its own compose project and fresh volume.

3. **How is this reconciled with Docker being unavailable on the development machine?** The local
   author rehearses against a new Homebrew `aegis_demo` database. Final acceptance still requires a
   separate Docker-capable clean machine.

4. **Are API keys optional?** They are optional for ordinary offline development, but mandatory for
   `make demo`. Missing keys fail before mutation; skipped live evaluations cannot satisfy v1.0.

5. **Does `/healthz` probe Ollama?** No. Nothing uses Ollama after Part 2 revision 3. The stale config
   field is removed.

6. **Does `/healthz` call OpenAI?** No. It checks configuration only. Live provider reachability is
   exercised by `make demo`, where a paid call measures the dependency that matters.

7. **Why persist evaluation runs?** A displayed summary without a stored trace cannot satisfy the
   inspectable-evidence portion of the release claim. The Part 3 incident envelope is reused rather
   than introducing an evaluation-only trace format.

8. **Why not run each eval assertion separately?** That spends multiple model calls on one scenario
   and compares assertions against different stochastic summaries. One scenario produces one result.

9. **Does the evaluator retry a failed scenario?** No. Retrying until one answer passes would turn the
   milestone into best-of-N selection and conceal the observed failure rate.

10. **What happens when a GitHub repository is absent from `services.yaml`?** The endpoint returns
    `422` and inserts nothing. Silent unattributed success would make the live demonstration
    misleading.

11. **Why not put GitHub commits in `unresolved_events`?** That table and its uid contract describe
    log-like source records. Treating a commit payload as a log event would produce the wrong
    identity and citation kind.

12. **Why is a pull-request event enrichment-only?** Its webhook payload does not contain the path
    and patch evidence needed for a truthful new commit row. Existing nullable PR metadata can be
    enriched without inventing evidence.

13. **Why is `"webhook"` stronger than `"budget"` for missing GitHub hunks?** The source never
    supplied patch text. The configured hunk cap did not cause the omission, even when many paths
    are present.

14. **Does `make demo-live` push automatically?** No. External repository mutation remains an
    explicit operator action. The target verifies the path before and after a real manual push.

15. **Does provenance inspection resolve current database rows?** No. It renders what the tools
    returned during the run. Current rollups may have changed legitimately.

16. **How is contradictory marketing prevented?** Exact README paragraphs, a repository-wide
    claim-bearing paragraph snapshot, a release-time `rg` review, and human inspection. The
    mechanical check is explicitly not treated as proof against paraphrase.

17. **Is ingest scheduled in v1.0?** No. Corpus ingestion remains manual through `aegis ingest all`.
    The GitHub webhook is the sole push-based live exception.

18. **Why leave the demo database running?** The operator must be able to run `aegis trace` after
    completion. Cleanup is explicit and separate.
