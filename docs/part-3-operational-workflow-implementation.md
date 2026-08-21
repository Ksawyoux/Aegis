# Part 3 — v0.3 Operational Workflow — Implementation Specification

> **Revision 1.** This document specifies the milestone that turns a command-line tool into a
> service. It assumes Part 2 is complete: three MCP tools, five scenarios, and the corpus
> contract suite. Where Part 2 is still in flight, the seams it owns are named explicitly so
> this milestone can be built against them rather than around them.

**Spec lineage:** implements Part 3 of the versioned build plan. Parts 0, 1, and 2 are the
frozen substrate. Read `docs/part-0-foundation-implementation.md` for the evidence identity
contract and `docs/part-1-vertical-slice-implementation.md` for the agent path this milestone
adds a second caller to.

---

## Falsifiable claim

> **With healthy dependencies and preloaded data, concurrent duplicate alerts cause exactly one
> observable investigation run and one best-effort Slack delivery attempt carrying a
> provenance-valid summary.**

Every word is load-bearing. *Concurrent* rules out a check-then-insert that only works when
requests arrive apart. *Observable* is why trace persistence is a requirement of this milestone
rather than a nicety of the next — a claim about how many runs happened cannot be evaluated
against a system that records no runs. *Best-effort* is the honest ceiling for an Incoming
Webhook that exposes no idempotency key.

## The single test that would falsify it

Send N simultaneous POSTs bearing the same provider deduplication key, while capturing run
traces and outbound Slack requests. Anything other than exactly one incident row, one run id,
one terminal outcome, and one webhook POST falsifies the claim.

N must be large enough and simultaneous enough to actually contend. A sequential loop of ten
requests proves nothing: the first request commits long before the second begins, so a broken
check-then-insert passes. The test uses a barrier so all requests reach the endpoint together.

## Why this milestone exists separately

A bad diagnosis, a broken deduplication, and a failed Slack post are three independently
falsifiable propositions. Bundled into one milestone they become one indistinguishable failure.
Here a red result means concurrency, delivery, or observability broke — never that the engine
reasoned badly, because reasoning quality was settled in v0.2 and is not re-tested.

## Scope

**In:** FastAPI application and lifespan · `POST /webhooks/alert` with atomic deduplication ·
background execution of the existing `investigate()` · run-trace persistence into
`incidents.summary_json` · Slack Block Kit delivery over an Incoming Webhook · `GET /healthz` ·
`aegis serve-api`.

**Out:** the GitHub webhook and its tunnel (v1.0 — it is a demo path, not an operational one) ·
crash-durable scheduling · exactly-once delivery · authentication beyond webhook signature
verification · any UI · retention or cleanup of old incidents.

## Global constraints

Copied verbatim from the plan and the built code; every task inherits these.

- Python `>=3.11`. PostgreSQL with pgvector. `uv` for dependency management.
- The agent path is `anthropic[mcp]` with `claude-opus-5`; embeddings are OpenAI
  `text-embedding-3-small` at `dimensions=1024`.
- Exactly three MCP tools, aggregates-only agent access.
- All timestamps are `TIMESTAMPTZ` stored in UTC. Windows are half-open `[start, end)`.
- Citation grammar is frozen, and these are the **built** forms — the product plan's shorter
  forms are stale and must not be used: `commit:<sha40>`, `deploy:<uid32>`, `infra:<uid32>`,
  `log:<uid32>`, `rollup:<service>/<iso8601>/<status_class>/<level>/<template_hash>`, and
  `postmortem:<slug>@<content_sha8>#<ordinal>`. `template_hash` is 32 hex characters, not 16.
  Anything rendering or validating a citation — the Slack message, the stored `summary_md`, the
  v1.0 trace view — reads `mcp_server/citations.py`, never a document.
- Gates are `uv run ruff check .`, `uv run mypy --strict src`, and `uv run pytest -q`.
  `ruff format` is not configured and `mypy --strict src tests` has pre-existing failures;
  neither is a gate.
- New runtime dependencies permitted by this milestone: `fastapi`, `uvicorn[standard]`.
  `httpx` is already a dependency and is what Slack delivery uses. Nothing else.

---

## 0. Contracts this milestone must not change, and the one thing it does

### 0.1 What stays frozen

`investigate(request: InvestigationRequest, run_context: RunContext) -> IncidentSummary` keeps
its exact signature. This milestone **adds a caller**; it does not restructure the agent path.
If any change to `investigate`, `agent/loop.py`, `agent/summary.py`, `mcp_server/`, or the
database schema turns out to be necessary, invariant 16 or 17 was violated earlier and the fix
belongs there — not here.

`RunContext(run_id: str, sink: TraceSink)` keeps its constructor. The sink is already injected,
which is the whole reason this milestone can persist traces without touching v0.1 code.

The `TraceSink` protocol keeps its single method `emit(event: TraceEvent) -> None`.

### 0.2 The one addition: `TraceSink` implementations may expose `flush()`

`DatabaseSink` needs a write point that is not tied to an event arriving, because the summary
and the delivery outcome are known *after* the last event. Rather than widen the protocol —
which would force `InMemorySink` and every test double to grow a method they do not need —
`flush()` is a method on the concrete `DatabaseSink` class only. The runner holds the concrete
type, so no structural typing is involved and `TraceSink` stays a one-method protocol.

This is deliberately not a protocol change. A caller holding a `TraceSink` cannot flush, and
must not need to.

### 0.3 Two built-code defects this milestone must fix first

Both were found by checking the spec's claims against the source rather than against the
earlier documents, and both block the design in §7.2.

**`db/session.py` opens an engine at import time.**

```python
engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
```

This runs on import: it constructs `Settings()` eagerly and opens a connection pool as a side
effect of importing the module. `cli.py` and `mcp_server/server.py` both import it, so the API
process would hold the lifespan engine *and* this global one, and `get_session()` would yield
sessions bound to the global — not to the engine the application configured. A test pointing an
app at a scratch database would silently write through the global engine to whatever
`AEGIS_DATABASE_URL` said at import time.

Fix: make the engine lazy behind a function, and have `get_session` resolve it through the
application's dependency rather than a module global. The CLI and MCP server keep working
because they call the same accessor.

**`investigate()` constructs its own `Settings()`.**

```python
result = asyncio.run(_run_with_transport(request.brief(), run_context, Settings()))
```

`app/investigate.py:51` builds settings inline instead of accepting them, so a caller cannot
inject configuration into the agent path — it can only mutate the process environment, which is
not safe under a threadpool serving concurrent requests. The Part 2 specification already
records this as a Part 0 drift to correct; Part 3 is where it starts to hurt, because
`run_incident` receives a `Settings` and cannot pass it through.

Fix: `investigate(request, run_context, settings=None)` defaulting to `Settings()` when omitted,
so the CLI is unchanged and the API injects. This is additive and breaks no caller.

---

## 1. The `summary_json` envelope

`incidents.summary_json` is a nullable `JSONB` column, already in migration 1. This milestone
fixes its shape. Getting this wrong is expensive: v1.0's trace inspection view reads it, and a
reshape later invalidates every stored incident.

```json
{
  "run_id": "e3f1c9a24b7d4e0fa1b2c3d4e5f60718",
  "summary": { "...IncidentSummary.model_dump(mode='json')... " },
  "trace": [ { "kind": "tool_result", "payload": { } } ],
  "delivery": {
    "attempted": true,
    "ok": false,
    "status_code": 500,
    "error": "server error"
  }
}
```

- `run_id` is always present from the first write. It is the correlation key for the falsifying
  test, so it must exist before the agent starts, not after it finishes.
- `summary` is `null` until the agent returns a provenance-valid summary. A failed run keeps it
  `null` and records why in the trace's terminal event.
- `trace` is the deterministic event list `RunContext.to_json()` already produces, key-sorted.
- `delivery` is `null` until a delivery is attempted. `attempted: true, ok: false` is a
  meaningful and expected state — that is what best-effort means.

A Pydantic model `IncidentRecord` in `app/records.py` owns this shape and is the only thing
permitted to construct it. No handler writes a raw dict into the column.

**No CHECK constraint.** The schema is frozen and adding one requires a migration that would
fail against any row written before it existed. Validation lives in Pydantic, where it can be
versioned additively.

---

## 2. `app/run_context.py` — `DatabaseSink`

**Files:** modify `src/aegis/app/run_context.py`; test `tests/integration/test_run_context_db.py`.

```python
@dataclass
class DatabaseSink:
    """Buffer trace events and write them to one incident row on demand."""

    engine: Engine
    incident_id: int
    run_id: str
    events: list[TraceEvent] = field(default_factory=list)

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)

    def flush(
        self,
        *,
        status: str,
        summary: IncidentSummary | None = None,
        delivery: DeliveryOutcome | None = None,
    ) -> None: ...
```

### Why buffered rather than per-event

A per-event write is a read-modify-write on a `JSONB` column: read the array, append, write it
back. That is O(n²) bytes over a run, and — far worse — it holds a row lock across the agent's
think time between tool calls, so the falsifying test's concurrent requests would serialize on
the lock rather than on the deduplication that is actually under test. The test would pass for
the wrong reason.

The cost of buffering is that a hard process kill mid-run loses the buffered events. That is
accepted and stated in §9; it is the same crash window `BackgroundTasks` already has.

### The write

`flush()` performs one statement, and it overwrites rather than appends because the buffer is
authoritative for the whole run:

```sql
UPDATE incidents
   SET status       = :status,
       summary_md   = :summary_md,
       root_cause   = :root_cause,
       summary_json = CAST(:record AS jsonb)
 WHERE id = :incident_id
```

`summary_md` is the rendered markdown from `cli._render_markdown`, which now lives in
`app/render.py` — see §7.1. `root_cause` is `summary.root_cause.statement`, or `NULL` on
failure. Both are `NULL` until a summary exists.

**`flush()` must never raise into the caller's `finally`.** A database failure while recording
a provenance failure would replace the real error with a less useful one. It catches
`SQLAlchemyError`, logs it, and returns. This is the one place in the codebase where swallowing
an exception is correct, and the reason is written in the code as a comment.

---

## 3. `app/runner.py` — the background unit of work

**Files:** create `src/aegis/app/runner.py`; test `tests/integration/test_runner.py`.

This module exists so the webhook handler stays about HTTP and deduplication, and so the thing
scheduled into the background is one named, directly testable function rather than a closure
built inside a request handler.

```python
def run_incident(
    incident_id: int,
    request: InvestigationRequest,
    settings: Settings,
    engine: Engine,
    *,
    deliver: Deliverer | None = None,
) -> None:
    """Investigate one incident, persist its trace, then attempt delivery."""
```

Ordering, which is the substance of this function:

1. Build `run_id = uuid4().hex` and `sink = DatabaseSink(engine, incident_id, run_id)`.
2. `sink.flush(status="investigating")` — **before** the agent starts. This is what makes a run
   observable even if the process dies during it: the row says `investigating` and names a
   `run_id` that no terminal event ever followed.
3. `run_context = RunContext(run_id, sink)`; call `investigate(request, run_context)`.
   `investigate` emits its own terminal event into the sink through its `finally`, so the
   buffer is complete whether it returned or raised.
4. On success: `sink.flush(status="summarized", summary=summary)`. On any exception:
   `sink.flush(status="failed")`, then continue to step 5 without re-raising — see below.
5. Delivery is attempted **only** on success, and its outcome is written by a third
   `sink.flush(status="summarized", summary=summary, delivery=outcome)`.

Three writes per successful run, two per failed one. Deterministic, bounded, and each one is a
state a test can assert against.

**`run_incident` never propagates an exception.** Starlette's `BackgroundTasks` logs and
discards whatever a task raises, so propagating would convert a diagnosable failure into a line
in a log nobody correlates. Instead every failure becomes `status="failed"` plus a terminal
trace event naming the exception type — which is exactly the observable the falsifying test
reads. The function's docstring states this, because a function that swallows everything is a
trap unless the reason is written down.

`deliver` is injected so tests capture the outbound request without patching module globals.
It defaults to `agent.slack.post_summary`.

---

## 4. `api/webhooks.py` — `POST /webhooks/alert`

**Files:** create `src/aegis/api/webhooks.py`; test `tests/integration/test_webhook_alert.py`.

### 4.1 Deduplication key derivation

```python
def derive_dedup_key(payload: Mapping[str, Any]) -> tuple[str, str]:
    """Return (dedup_key, source) where source is 'provider' or 'fingerprint'."""
```

Resolution order, first match wins:

1. `payload["event"]["data"]["id"]` — PagerDuty webhook v3 incident id.
2. `payload["dedup_key"]` — the flat form some providers and all replay fixtures use.
3. A computed fallback: `"fp:" + sha256(f"{service}|{alert_name}|{bucket}").hexdigest()[:32]`,
   where `bucket` is `fired_at` floored to five minutes.

The provider's own key is preferred for a concrete reason: a time-bucketed fingerprint merges
two genuine firings four minutes apart into one incident, and splits a single provider retry
that straddles a bucket boundary into two. Both are wrong, in opposite directions, and neither
is detectable after the fact. The fingerprint exists only because some providers send no key,
and `source` is recorded in `alert_payload` so an operator can tell which regime an incident
was created under.

### 4.2 The atomic insert

```sql
INSERT INTO incidents (
    dedup_key, service_id, opened_at, window_start, window_end,
    alert_payload, status, created_at
) VALUES (
    :dedup_key, :service_id, :opened_at, :window_start, :window_end,
    CAST(:alert_payload AS jsonb), 'open', now()
)
ON CONFLICT (dedup_key) DO NOTHING
RETURNING id
```

**Only the request that receives a row schedules the background run.** That makes "one run per
incident" a property of the `dedup_key` unique index rather than a hope about timing. There is
no `SELECT` before the `INSERT`, because check-then-insert is precisely the race the milestone
claims not to have.

### 4.3 The conflict path, including the case that is easy to get wrong

`ON CONFLICT DO NOTHING` returns no row on conflict, so the loser must `SELECT` to report the
existing incident. That `SELECT` can legitimately find nothing: the winning transaction has
taken the unique-index lock but has not committed, so its row is not yet visible.

The handler therefore does one bounded retry — a single re-`SELECT` after 50 ms — and if the
row is still invisible, responds `202 Accepted` with the `dedup_key` and a `null` id. That is
the honest answer: the incident exists, this request did not create it, and its id is not yet
readable. Blocking until the winner commits would couple an unrelated request's latency to the
agent's startup, and looping would turn a contended burst into a thundering herd.

Responses:

| Case | Status | Body |
| --- | --- | --- |
| Row inserted, run scheduled | `202` | `{"incident_id": N, "dedup_key": ..., "created": true, "run_scheduled": true}` |
| Conflict, existing row read | `200` | `{"incident_id": N, "dedup_key": ..., "created": false, "run_scheduled": false}` |
| Conflict, row not yet visible | `202` | `{"incident_id": null, "dedup_key": ..., "created": false, "run_scheduled": false}` |
| Unparseable payload | `422` | FastAPI validation error |
| Service name resolves to nothing | `202` | inserted with `service_id = NULL`; see below |

An unresolvable service is **not** a rejection. `incidents.service_id` is nullable precisely so
an alert for a service missing from `services.yaml` is still recorded rather than dropped; the
run will produce a low-confidence summary, which is the correct outcome and a far better
operational signal than a 400 nobody sees.

### 4.4 Scheduling

```python
background.add_task(run_incident, incident_id, request, settings, engine)
```

`run_incident` is a synchronous `def`, so Starlette runs it in its threadpool. `investigate`
calls `asyncio.run(...)` internally, which requires no running loop on the calling thread — a
threadpool worker has none, so this composes correctly. It would **not** compose if
`run_incident` were `async def`: `asyncio.run` inside a running loop raises
`RuntimeError: asyncio.run() cannot be called from a running event loop`. §9 makes this a test.

---

## 5. `api/health.py` — `GET /healthz`

**Files:** create `src/aegis/api/health.py`; test `tests/integration/test_health.py`.

```json
{
  "status": "healthy",
  "checks": {
    "database": {"ok": true, "detail": null},
    "embeddings": {"ok": true, "detail": null}
  }
}
```

`200` when every check is `ok`, `503` otherwise. The body is identical in both cases, because a
health endpoint that reports nothing on failure forces an operator to go read logs to learn
which dependency is down.

**Database:** `SELECT 1` on a connection acquired with a short timeout. The timeout matters —
without it a saturated pool makes `/healthz` hang, and a hanging health check is read as a hard
down by every orchestrator.

**Embeddings: configuration only, no network call.** The plan called for an Ollama reachability
probe, and revision 3 of Part 2 replaced Ollama with a paid remote API. Probing that on every
health check would bill per poll and would make an upstream rate limit look like local
unhealthiness. The check therefore asserts that `settings.openai_api_key` is present and that
`settings.embedding_model` is a `text-embedding-3-*` model — the two conditions whose absence
is a genuine local misconfiguration.

This is a deliberate weakening of the plan's `/healthz` and it is recorded as such: the endpoint
no longer proves the embedding provider is reachable, only that this process is configured to
reach it. Reachability is proven by the corpus contract suite, which does make live calls.

`ollama_base_url` stays in `Settings` as an unused field. Removing it is a config-shape change
for no benefit; it costs one line and keeps the door open.

---

## 6. `agent/slack.py` — best-effort Block Kit delivery

**Files:** create `src/aegis/agent/slack.py`; test `tests/unit/test_slack.py`.

```python
class DeliveryOutcome(BaseModel):
    attempted: bool
    ok: bool
    status_code: int | None = None
    error: str | None = None

def build_blocks(summary: IncidentSummary, run_id: str) -> list[dict[str, Any]]: ...

def post_summary(
    summary: IncidentSummary, run_id: str, settings: Settings, *, client: httpx.Client | None = None
) -> DeliveryOutcome: ...
```

### 6.1 The block layout

`header` with the service name · `section` with the root cause and its citations · `section`
with confidence and recommended action · `section` per timeline entry, or one section holding
the whole timeline when it fits · `section` listing ruled-out candidates · `context` footer
carrying the `run_id`.

Citations are rendered in the Slack message for the same reason they are rendered in the CLI
markdown: a claim shown without its evidence is an unsupported assertion, and Slack is where a
human will actually read it.

### 6.2 The limits that will otherwise bite in production and never in tests

Slack rejects a payload whose `section.text.text` exceeds 3000 characters, and rejects more than
50 blocks. A real incident with a long timeline and full rollup citations — which run to ~90
characters each — reaches both. Truncation is therefore part of the contract, not an afterthought:

- Each section's text is truncated to 2900 characters with a trailing `…` before serialization.
- The block list is capped at 45, and when entries are dropped a final `context` block states
  how many, so the reader knows the message is partial.

A test builds a summary with 40 timeline entries and asserts both bounds hold. Without it this
defect ships, because every fixture summary is small.

### 6.3 Failure handling

No retry beyond a single attempt with a 10-second timeout. An Incoming Webhook exposes no
idempotency key, and a webhook that accepted a message can still return a shape that looks
retryable — so retrying risks duplicate posts to trade against a failure mode that is already
declared best-effort. A network error, a non-2xx, and a timeout all produce
`DeliveryOutcome(attempted=True, ok=False, ...)` and are recorded, never raised.

When `settings.slack_webhook_url` is `None`, delivery is skipped and the outcome is
`DeliveryOutcome(attempted=False, ok=False, error="no webhook configured")`. Skipping is not an
error: the CLI path has no Slack and must not start reporting failures because of it.

---

## 7. `api/app.py` and the CLI

**Files:** create `src/aegis/api/app.py`, `src/aegis/api/__init__.py`, `src/aegis/app/render.py`;
modify `src/aegis/cli.py`.

### 7.1 `app/render.py` — moving the renderer out of the CLI

`_render_markdown` currently lives in `cli.py`, and `DatabaseSink` needs it for `summary_md`.
The API importing from `cli.py` would drag Typer into the web process and invert the dependency
direction the plan's invariant 16 established.

Move it to `app/render.py` as public `render_markdown(summary: IncidentSummary) -> str`, and
have `cli.py` import it. The existing renderer tests move with it. This is a relocation, not a
rewrite: the rendered output must be byte-identical, and the existing tests are what proves it.

### 7.2 The application

```python
def create_app(settings: Settings | None = None) -> FastAPI: ...
```

A factory, not a module-level singleton, so tests construct an app against a test database
without mutating process state. The engine is created in the lifespan handler and disposed on
shutdown; handlers reach it through a dependency, never through a global.

### 7.3 `aegis serve-api`

```python
@app.command("serve-api")
def serve_api(host: str = "127.0.0.1", port: int = 8000) -> None: ...
```

Binds to loopback by default. There is no authentication on `/webhooks/alert` in this milestone
— signature verification arrives with the GitHub webhook in v1.0 — so a default of `0.0.0.0`
would publish an unauthenticated endpoint that schedules paid agent runs to anyone who can
reach the port. Binding elsewhere is an explicit act.

---

## 8. Tests

### 8.1 The falsifying test

`tests/integration/test_concurrency.py`

Twenty threads, released by a `threading.Barrier`, POST the same payload to a real app instance
backed by the real database, with `run_incident` replaced by a recording double.

Assertions: exactly one row for the `dedup_key`; exactly one scheduled run; exactly nineteen
non-creating responses; every response is `200` or `202` and none is a `500`.

The double matters. Wiring the real agent here would make the test require an Anthropic key,
cost money per run, and take minutes — and a race is far easier to see when the scheduled work
does nothing. Agent wiring is proven separately in §8.4.

### 8.2 Deduplication semantics

- Two different provider keys create two incidents.
- The same provider key arriving eight minutes apart creates one incident — the provider's key
  wins over any time bucketing.
- Two alerts with no provider key, four minutes apart, create **two** incidents when they fall
  in different five-minute buckets. This test documents the known limitation rather than hiding
  it; if the fallback is ever changed, it should fail.
- The conflict-but-not-yet-visible path returns `202` with a `null` id. Forced by holding the
  winning transaction open in another connection.

### 8.3 Trace persistence

- After a successful run: `status = "summarized"`, `summary_json.run_id` matches the run,
  `summary` is non-null, `trace` contains a `terminal` event with `status: "completed"`.
- After a provenance failure: `status = "failed"`, `summary` is `null`, and the terminal event
  carries `error_type: "ProvenanceError"`.
- A run killed after the first flush leaves `status = "investigating"` with a `run_id` and an
  empty trace — the crash window, asserted rather than assumed.
- `DatabaseSink.flush` against a closed engine does not raise.

### 8.4 Composition

`tests/integration/test_api_composition.py` — the seams no per-package test covers. See §9.

### 8.5 Slack

- Block Kit output validates against the documented block shapes.
- The rendered message contains every citation the summary carries.
- A 40-entry timeline stays under 50 blocks and every section under 3000 characters.
- A non-2xx response yields `attempted=True, ok=False` and does not raise.
- `slack_webhook_url = None` yields `attempted=False` and makes no HTTP call.

### 8.6 Health

- Healthy when the database answers and the key is configured.
- `503` with `database.ok = false` when pointed at a dead database, and the response still
  arrives within the timeout rather than hanging.
- `503` with `embeddings.ok = false` when `openai_api_key` is absent.

---

## 9. Composition and live-database risks

This is the section that matters. Every serious defect in Parts 0 through 2 lived in a seam
that per-package tests could not see: a rollup exemplar score where one NULL nulled the whole
sum, a transport layer where nothing actually spawned the MCP server because one suite injected
fake tools and the other stubbed the agent, and a subprocess that read a different database
because `StdioServerParameters` does not inherit the environment.

**Seam 1 — `BackgroundTasks` → `investigate` → `asyncio.run`.** If `run_incident` is ever made
`async def`, `asyncio.run` raises inside the running loop and every background run dies while
the endpoint still returns `202`. Unit tests of `run_incident` called directly would pass. The
composition test schedules through a real `TestClient` request and asserts the incident reaches
a terminal status.

**Seam 2 — the MCP subprocess under the API process.** `_server_environment` copies
`os.environ` and pins `AEGIS_DATABASE_URL`. Under a process manager that strips the
environment, `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` vanish and every background run fails
with an authentication error that surfaces only as `status = "failed"`. The composition test
asserts the spawned server sees the same database as the API process, using the probe pattern
already in `tests/integration/test_transport.py`.

**Seam 3 — engine sharing across threads, and the import-time global.** The API's engine is
created in the lifespan on the main thread and used by `DatabaseSink` on a threadpool worker. A
`Session` is not thread-safe; an `Engine` is. Every database access inside `run_incident` must
open its own `Session` from the engine and must never capture a request-scoped session.

The sharper risk is the module-level engine in §0.3: until it is removed, an app configured
against database A can write through the global engine to database B, and every assertion in
the test still passes because both are reachable and both have the schema. This is the same
failure shape as the Part 1 transport defect, where the spawned MCP server silently read a
different database. A test asserts that an app built with an explicit `Settings` writes to that
database and that `aegis.db.session` holds no connected engine after import.

**Seam 4 — the flush inside a `finally`.** `investigate` emits its terminal event during
teardown. If the sink's write raised there, the original exception would be replaced by a
database error and the real cause lost. Tested by flushing against a disposed engine while an
exception is in flight.

**Seam 5 — pool exhaustion under the falsifying test.** Twenty concurrent requests each want a
connection, and the threadpool wants more. With SQLAlchemy's default pool of five plus overflow
ten, twenty concurrent handlers plus background workers can exhaust it and block on checkout —
which the test would see as a timeout rather than as a deduplication failure, and which an
impatient reader would call flakiness. Set `pool_size` and `max_overflow` explicitly in
`create_database_engine` for the API path, and assert the concurrency test completes within a
bounded time so exhaustion fails loudly rather than slowly.

**Seam 6 — `TestClient` background execution.** Starlette's `TestClient` runs background tasks
**synchronously before the response returns**. A test that asserts "the response came back
before the run finished" is asserting something that is false under `TestClient` and true in
production. Concurrency tests must therefore use a recording double for `run_incident`, and any
test of real background timing must run against a live `uvicorn` process. Writing this down is
what prevents a day of confusion.

---

## 10. Exit criteria

Observations, not assertions.

1. `aegis serve-api` starts; `GET /healthz` returns `200` against the live database and `503`
   with a populated `database.detail` when it is stopped.
2. Twenty simultaneous duplicate alerts produce exactly one incident row, one `run_id`, and one
   scheduled run.
3. A completed run leaves `status = "summarized"`, a non-null `summary`, a terminal trace event,
   and a `delivery` block.
4. A failed run leaves `status = "failed"`, a null `summary`, and a terminal event naming the
   exception type.
5. The captured Slack request is valid Block Kit and carries the same claims and citations as
   the validated `IncidentSummary`.
6. A 40-entry summary produces a message within Slack's block and character limits.
7. An agent failure and a delivery failure are each attributable to a specific `run_id`.
8. `render_markdown` produces byte-identical output to the pre-move `cli._render_markdown`.
9. Gates green: ruff clean, `mypy --strict src` clean, full suite passing.

## 11. Deliberately does not prove

Crash-durable scheduling — the incident row commits and the process can die before the
background run starts, and deduplication then suppresses the retry, so that incident is never
investigated. A claim/lease column or a real queue fixes it and both are post-v1.0.

Exactly-once delivery · clean-room reproducibility · robustness on external log distributions ·
authentication · any superiority claim.

## 12. Build order

1. **The two §0.3 fixes** — lazy engine, injectable settings — with tests. Everything after this
   depends on being able to point a process at a chosen database.
2. **`app/render.py`** — move the renderer, keep its tests green. Everything else can import it.
3. **`DatabaseSink` and the `IncidentRecord` envelope**, with the persistence tests. The envelope
   is the thing later work is hard to change out from under.
4. **`api/app.py` and `/healthz`** — a running application with a real dependency check, before
   anything depends on it.
5. **`app/runner.py`** with a stubbed `investigate`. Its ordering guarantees are testable without
   an agent, and doing it before the webhook means the webhook has something real to schedule.
6. **`POST /webhooks/alert`** — key derivation, then the atomic insert, then scheduling. Test
   concurrency here, before the agent is wired: a race is easiest to see when the scheduled work
   does nothing.
7. **`agent/slack.py`** with a captured-request double, then wire it into the runner.
8. **The composition tests of §9**, then one manual end-to-end run against a real webhook URL.

The order is chosen so that each step's failure is unambiguous. Wiring the agent before
concurrency is settled would make every red result ambiguous between a race and a bad run.

## 13. Resolved questions

1. **Where does `run_id` live, given the schema is frozen?** In `summary_json.run_id`, written
   before the agent starts. A dedicated column would need a migration, and `RunContext.to_json()`
   already emits `run_id` at the top level, so the column would duplicate it.
2. **Why not a queue?** The plan's no-Celery decision stands. `BackgroundTasks` has a stated
   crash window and this milestone claims only healthy-process execution. A queue is the fix for
   a claim this milestone does not make.
3. **Does `/healthz` probe the embedding API?** No — configuration only. Billing per poll and
   mistaking an upstream rate limit for local unhealthiness both cost more than the check is
   worth. Recorded as a deliberate weakening of the plan.
4. **Is an unresolvable service a rejection?** No. It is recorded with `service_id = NULL`. A
   dropped alert is worse than a low-confidence summary.
5. **Retry on Slack failure?** No. One attempt, ten-second timeout. Without an idempotency key,
   retrying trades a duplicate post against a failure already declared best-effort.

## 14. Open questions for the orchestrator

1. **Should `POST /webhooks/alert` verify a shared-secret signature in v0.3?** The plan puts
   signature verification in v1.0 with the GitHub webhook, which leaves the alert endpoint
   unauthenticated for a milestone. Loopback-only binding is the mitigation here; a shared
   secret would be roughly twenty lines and would close it now.
2. **Should a run that fails before producing a summary still post to Slack?** This spec says
   no. The argument for yes is that a silent failure is the failure mode operators most hate.
