# Part 1 — v0.1 Vertical Slice: implementation specification

> **Revision 2**, after review by `gpt-5.6-sol` (xhigh). Ten blocking defects in revision 1, all
> verified against the built Part 0 code and fixed here; §14 lists them. The most important: the
> agent loop as originally written **could not populate `captured_cites`**, so provenance validation
> would have rejected every citation and this milestone's defining claim would have failed no matter
> how correct the SQL was.

Part 0 is committed and gated (109 tests against real PostgreSQL, `mypy --strict` clean). Its
contracts are frozen. Part 1 makes the loop close.

## Falsifiable claim

> **A single agent, using only the two final-shaped aggregate MCP tools over a real stdio boundary,
> correlates a deployment hunk with a statistically visible telemetry change and produces the correct
> structured claim using only citations returned during that run.**

**The single test that falsifies it:** from a fresh database, ingest the fixture **twice**, then run
the agent against a scenario containing a neutral commit message and a plausible distractor deploy.
Falsified by a wrong cause, a missing evidence class, an invalid citation, a result that depends on
the duplicate ingest, or any path reaching the database without going through MCP.

## Scope

**In:** one service (`checkout-api`) · JSON-lines logs only · commits and deployments from a
committed fixture · the ingest pipeline transaction · two MCP tools over stdio · the agent loop ·
`investigate()` completed · CLI · one scenario with an executed reachability check and a semantic
assertion.

**Out:** Terraform · k8s · postmortems · pgvector · Tool 3 · the other three log formats · FastAPI ·
webhooks · Slack · the other four scenarios · **`other_services` population** (the envelope block
ships empty; Part 2 fills it).

## Contracts Part 1 must not change

| Frozen | Where |
| --- | --- |
| `uid()` inputs, `UID_VERSION` | `ingest/identity.py` |
| Masking branches, `template_hash` | `ingest/templates.py` |
| Five-column rollup PK, delete-and-recompute | `db/models.py`, `aggregate/rollups.py` |
| Citation grammar | `mcp_server/citations.py` |
| Response envelopes and ordering | `mcp_server/schemas.py` |
| `Claim`-based `IncidentSummary`, provenance traversal | `agent/summary.py` |
| `investigate(request, run_context)` signature | `app/investigate.py` |
| Half-open windows, snapping, baseline construction | `ingest/timewindow.py` |

**Correction to revision 1:** "every returned row carries a `cite`" is false against the real API.
`SeriesPoint` and `StatusBreakdownEntry` carry `source_cites`; `TemplateAnomaly` carries
`source_cites`, `baseline_cites`, and `exemplar.cite`, and has **no top-level `cite`**. Only
`CommitRef`, `DeploymentRef`, `InfraChange`, and `Exemplar` carry a singular `cite`.

---

## 1. `ingest/pipeline.py` — the transaction boundary

Makes Part 0 §8.5 real: base-row mutation, rollup recomputation, and watermark advancement in **one**
transaction.

```python
@dataclass(frozen=True)
class IngestReport:
    source: str
    inserted: int          # new rows in log_events
    duplicates: int        # same uid, byte-identical content -> no-op
    promoted: int          # unresolved_events -> log_events
    unresolved: int        # new rows in unresolved_events
    rollup: RollupReport
```

`inserted`, `duplicates`, `promoted`, and `unresolved` are **mutually exclusive** classifications of
each input record; they sum to the record count.

```python
def ingest_source(session: Session, *, source: str,
                  records: Iterable[LogRecord], cursor: int) -> IngestReport
```

**Transaction ownership:** the caller enters and exits `session.begin()`. `ingest_source` neither
commits nor rolls back; it raises on conflict and lets the caller's context manager roll back. This
is what makes "one transaction" testable rather than ceremonial.

Order inside the transaction:

1. `SELECT pg_advisory_xact_lock(hashtext('ingest:' || :source))`.
2. Read the stored watermark. **`cursor` is the exclusive next-unread byte offset.** A cursor lower
   than the stored one raises `CursorRegression` — silently re-reading is how duplicate-but-changed
   evidence enters. A cursor higher than stored + consumed bytes raises `CursorGap`.
3. Apply the per-record conflict policy (below), collecting `(service_id, minute)` for every row
   inserted or promoted.
4. `capture_dirty_set(session, changed=<collected>)` then `recompute(session, dirty=...)`.
5. Upsert `ingest_watermarks(source, last_cursor=str(cursor))`.

**This pipeline is append-only.** Nothing here deletes log rows, so capture-after-insert is safe.
Deletion, when it arrives, must follow Part 0 §8.4's order: capture → delete rollups → delete rows →
recompute. Stated so a later author does not copy this order into a deleting path.

### Per-record conflict policy — not a blanket `DO NOTHING`

Revision 1 specified `on_conflict_do_nothing` everywhere, which contradicts Part 0 §5.2 and silently
discards conflicting evidence. The four outcomes:

| Situation | Outcome |
| --- | --- |
| uid absent from both tables | insert; count `inserted` |
| uid in `log_events`, content-bearing columns **identical** | no-op; count `duplicates` |
| uid in `log_events`, content **differs** | raise `EvidenceConflict(uid, differing_columns)` |
| uid in `unresolved_events`, record now resolves | **promotion**: insert into `log_events` with the same uid and delete the unresolved row, same transaction; count `promoted` |
| uid in `log_events`, record now fails to resolve | raise `EvidenceRegression(uid)` |

Promotion preserving the uid is Part 0 §14.2 — any citation issued earlier still resolves.

## 2. `ingest/logs.py` — reader, registry, and the JSON-lines parser

### The reader produces true byte offsets

`uid` is computed from the byte offset (invariant 2), and a text-mode loop gives a character index.
One `é` on the first line and every later offset is one byte short; CRLF and invalid UTF-8 diverge
further. The reader is therefore **binary**:

```python
def iter_raw_lines(path: Path) -> Iterator[tuple[int, bytes]]:
    """Yield (byte_offset_of_line_start, raw_line_including_terminator)."""
    with path.open("rb") as fh:
        offset = 0
        for raw in fh:                      # binary readline
            yield offset, raw
            offset += len(raw)              # original bytes, terminator included
```

Decoding happens after the offset is fixed: UTF-8 with `errors="replace"`. `log_uid(file=...,
offset=..., raw=...)` receives the **bytes**, matching Part 0's `raw: bytes | str` signature.

### Registry

```python
class LogFormat(Protocol):
    name: str
    def sniff(self, sample: Sequence[str]) -> float: ...
    def parse(self, line: str, offset: int, ctx: ParseContext) -> Draft: ...

FORMATS: tuple[LogFormat, ...] = (JsonLinesFormat(),)
```

Detection is per file from the first 20 non-blank decoded lines; highest confidence wins, ties broken
by registry order. Part 2 appends three formats and changes no caller.

```python
@dataclass(frozen=True)
class ParseContext:
    registry: ServiceRegistry
    source_file: str                 # normalized, relative to corpus_dir
    default_log_timezone: str        # used when the record's service cannot resolve
```

### Drafts are a discriminated union

Revision 1 used one dataclass with a non-optional `ts`, which cannot represent a record that has no
timestamp or is not JSON at all without fabricating values.

```python
@dataclass(frozen=True)
class ResolvedDraft:
    uid: str; ts: datetime; service_id: int; level: str
    status_code: int | None; trace_id: str | None
    message: str; template_hash: str; raw: str
    attrs: dict[str, Any]; source_file: str; source_offset: int

@dataclass(frozen=True)
class UnresolvedDraft:
    uid: str; raw: str; reason: str
    source_file: str; source_offset: int
    ts: datetime | None = None       # present only when parsed successfully

Draft = ResolvedDraft | UnresolvedDraft
```

### JSON-lines rules

Keys: `ts`, `level`, `service`, `msg`; optional `trace_id`, `status`, `upstream`, `duration_ms`.

**Reason precedence**, evaluated in this order so one record has exactly one reason:
`unparseable` (not JSON, or not an object) → `no_timestamp` (absent or unparseable `ts`) →
`ambiguous_service` → `no_service_match`.

Type coercion, stated so implementations cannot differ: `status` accepts int or an all-digit string,
otherwise it is dropped and recorded in `attrs.coercion_dropped`; `duration_ms` accepts int or float,
otherwise dropped; `msg` must be a string, otherwise the record is `unparseable`; duplicate JSON keys
take the **last** occurrence (Python's `json` default), and that choice is documented rather than
incidental.

A naive `ts` is attached with `attach_local_time()` using the **resolved service's** `log_timezone`;
when the service does not resolve, the record is unresolved anyway and its `ts` is left as parsed
without zone attachment. `tz_ambiguous` / `tz_nonexistent` flags go into `attrs`.

`level` goes through `canonical_level(level, status_code)`. `status_class` is derived by the rollup,
never stored on the row.

## 3. `ingest/git.py`

```python
def load_git_export(path: Path) -> GitExport
def upsert_commits(session: Session, export: GitExport, registry: ServiceRegistry) -> UpsertCounts
def upsert_deployments(session: Session, export: GitExport, registry: ServiceRegistry,
                       settings: Settings) -> UpsertCounts
```

`UpsertCounts` is `{inserted, updated, unchanged}` — revision 1 returned a bare `int` whose meaning
was undefined.

### `GitExport` schema

```json
{
  "repo": "acme/checkout",
  "commits": [{"sha": "<40 hex>", "author": "...", "authored_at": "<RFC3339>",
               "committed_at": "<RFC3339>", "message": "...", "pr_number": null,
               "files_changed": [{"path": "...", "status": "modified",
                                  "additions": 3, "deletions": 3, "hunks": "@@ ..."}]}],
  "deploys": [{"commit_sha": "<40 hex>", "environment": "production",
               "started_at": "<RFC3339>", "finished_at": "<RFC3339>|null",
               "status": "success"}]
}
```

`load_git_export` **rejects duplicate `path` values within one commit** — `files_changed` sorts by
`path` alone, so duplicates leave the order underdetermined and break byte-identical output.

Hunk budgets come from `settings` (`hunk_max_files`, `hunk_max_hunks_per_file`,
`hunk_max_lines_per_file`), applied with Part 0 §3.2 precedence: sort by `(additions + deletions)
DESC, path ASC`; the first `hunk_max_files` keep hunks; the rest get `hunks_omitted="budget"`; a kept
file exceeding per-file caps is marked `"budget"` **whole**, never truncated.

### Deployment lifecycle — three outcomes, and rejection must raise

Revision 1 said "every other transition is rejected", which rejects `success → success` and so breaks
the required second fixture ingest. It also relied on `ON CONFLICT DO UPDATE ... WHERE`, which
**returns no row without raising** when the predicate is false — PostgreSQL locks the row and moves
on, so an illegal transition would pass silently.

| Situation | Outcome |
| --- | --- |
| uid absent | insert |
| uid present, `status` and `finished_at` **identical** | no-op (`unchanged`) — this is what makes replay work |
| uid present, transition in `{in_progress → success|failed|rolled_back, success → rolled_back}` | update those two columns |
| uid present, any other differing lifecycle state | raise `IllegalDeploymentTransition(uid, from, to)` |

Implement by reading the existing row inside the transaction and branching explicitly. An empty
`RETURNING` result must never be treated as successful rejection.

## 4. `mcp_server/queries.py`

Pure functions over a `Session`, returning Part 0 envelopes. Internally they use
`timewindow.ResolvedWindow` (a dataclass) and convert to `schemas.ResolvedWindow` (Pydantic) at the
envelope boundary — the two types are distinct and the conversion point is here.

**Every time predicate is half-open `[start, end)`.** Revision 1 wrote an inclusive `window_end` for
the diff tool, contradicting Part 0 §12.

### `get_incident_diff`

```python
def get_incident_diff(session: Session, *, service: str,
                      window_start: datetime, window_end: datetime,
                      lookback_minutes: int = 60,
                      include_other_services: bool = True) -> IncidentDiff
```

`resolved = resolve_window(window_start, window_end)`; the queried range is
`[resolved.start - lookback_minutes, resolved.end)`. The returned `window` describes **that queried
range**, not the caller's request, and says so in the tool docstring.

- Deployments: `started_at` in range, each **joined to its commit**, so a commit authored days
  earlier but deployed in-window arrives with its diff.
- Standalone commits: selected by **`committed_at`** in range only. Using `authored_at` as well would
  pull in rebased history that never shipped.
- `other_services`: **empty in v0.1.** The parameter and block exist; population is Part 2.
  `include_other_services=False` also yields empty, and both are asserted.
- `unattributed`: `infra_changes` with `service_id IS NULL`, ordered `applied_at DESC, uid ASC`.
  Empty in the committed corpus; exercised by a dedicated fixture (§13 answer 4).
- `DiffCounts` counts **focus only**, and the field docs say so.

Errors: unknown `service` → `ToolError`; `lookback_minutes < 0` → `ToolError`; `start >= end` is
already rejected by `resolve_window`.

### `get_error_telemetry`

```python
def get_error_telemetry(session: Session, *, service: str,
                        window_start: datetime, window_end: datetime,
                        top_n: int = 10,
                        baseline_sparse_threshold: int) -> ErrorTelemetry
```

`baseline_sparse_threshold` is **passed in** from the server's `Settings`; revision 1 left the query
to instantiate its own, so a configured 100 would silently behave as 50.

1. `effective = resolve_window(...)`; `baseline = baseline_window(effective)`. Both on the envelope.
2. `series`: rollups in the effective window grouped by `(bucket_start, status_class)`, each point
   carrying `source_cites` for every row aggregated.
3. **`top_templates` identity universe is the UNION of current-window and baseline-window
   identities**, left-joined to both aggregates with missing counts coalesced to 0. Revision 1 built
   only from current-window rollups, so a template that existed in the baseline and vanished — count
   0, baseline 20, delta −20 — could never appear, contradicting the stated retention of negative
   deltas.
4. **Window-level exemplar selection.** One anomaly identity spans several minute rollups, each with
   its own `exemplar_log_event_id`; a plain point join is either ungrouped-invalid or picks an
   arbitrary bucket. Re-score the candidate exemplars with the same non-null richness expression Part
   0 uses, tie-break on `log_events.uid ASC` (stable across databases, unlike `id`), and prefer
   current-window candidates; use baseline candidates only when current count is 0.
5. `status_breakdown`: counts per `status_class` over the **effective** window, one entry per class
   present, each with `source_cites`, ordered `status_class ASC`.
6. `sample_trace_ids`: distinct non-empty `trace_id` values from the effective window's exemplars,
   **deduplicated**, ordered `(ts ASC, uid ASC)`, capped at 5.
7. `baseline_sparse` is true when total baseline events for the service fall below the passed
   threshold.
8. Rollup citations need the service **name**, so every rollup query joins `services`.

Two pools per Part 0 §10.4 — error pool is `status_class = '5xx'` **or** `level in
('error','fatal')` — ranked independently, error pool first, each truncated to `top_n`.
`top_n <= 0` → `ToolError`.

## 5. `mcp_server/server.py`

FastMCP, stdio, entry point `python -m aegis.mcp_server`. **Exactly two tools registered** in v0.1,
and a test asserts the count.

- **Errors raise `ToolError`**, not a success-shaped `{"error": ...}` dict. A structured error dict is
  a *successful* MCP result: the runner would treat it as evidence, and capture logic would have to
  special-case it. `ToolError` is marked as a tool error and does not kill the server.
- **stdout carries protocol frames only.** No banner, no `print`, no SQLAlchemy echo — all
  diagnostics to stderr. A stray write corrupts the stream; a test asserts stdout is clean across a
  tool call.
- Timestamps cross as RFC-3339 strings, parsed to aware datetimes at the edge. A naive string is a
  `ToolError`, never a coercion.
- Each call opens and closes its own session.

## 6. `agent/loop.py` — an explicit state machine

**This is the section revision 1 got wrong, and it was fatal.** The runner yields the assistant
response *before* executing requested tools, and `async_mcp_tool` hands back Anthropic content blocks
derived from an MCP `CallToolResult` — while Part 0's `capture_tool_result(tool, args, result)`
requires a Pydantic `BaseModel`. So a normal telemetry result would reach the model but never enter
`captured_cites`; the extraction call would cite it; and `validate_provenance` would reject a
perfectly good citation. Every run would fail.

The loop therefore owns its history explicitly and uses only public API:

```python
async def run_agent(brief: str, run_context: RunContext,
                    settings: Settings) -> AgentResult
```

```
history = [user(brief)]
turns_used = 0
budget = settings.agent_max_turns - 1        # one turn reserved for extraction

for message in runner:                        # each yielded message is one API response
    turns_used += 1
    run_context.emit(TraceEvent(kind="agent_turn", ...))
    if message.stop_reason != "tool_use":
        break                                 # terminal assistant turn
    if turns_used >= budget:
        raise AgentTurnLimitExceeded(turns_used)
    responses = await runner.generate_tool_call_response()
    for block, result in pair(message, responses):
        envelope = ENVELOPE_BY_TOOL[block.name].model_validate_json(result_text(result))
        run_context.capture_tool_result(block.name, block.input, envelope)
    history.append(assistant(message)); history.append(user(responses))
```

`ENVELOPE_BY_TOOL` maps `get_incident_diff → IncidentDiff` and `get_error_telemetry →
ErrorTelemetry`. **Reconstructing the envelope is what makes capture possible**, and it doubles as a
contract check: a tool response that no longer validates fails loudly here rather than silently
degrading the agent.

A tool result marked as an error is traced and appended for the model to see, but is **not** validated
into an envelope and contributes no citations.

**Turn accounting:** one turn is one Messages API response. The extraction call is a turn and is
reserved from the budget up front — revision 1 counted neither, and the SDK's own iteration cap
merely *stops* rather than raising.

**Extraction:** after the loop, one `client.messages.parse(...)` over `history` plus an explicit
user message asking for the structured summary, with `IncidentSummary` as the target and its own
`max_tokens`. It reuses `SYSTEM_PROMPT` so the citation rules still apply. Then
`validate_provenance(summary, run_context.captured_cites)`.

## 7. `app/investigate.py`

Replace `NotImplementedError` with a call into `run_agent`, keeping the signature and failure
contract: MCP subprocess terminated in a `finally` on **every** path including the turn-cap and
provenance failures; a `kind="terminal"` trace event emitted before any exception propagates;
`ProvenanceError`, `AgentTurnLimitExceeded`, and transport errors propagate to the caller.
`investigate` stays synchronous and drives the async loop with `asyncio.run`.

## 8. `cli.py`

| Command | Behaviour |
| --- | --- |
| `aegis db upgrade` | `alembic upgrade head` |
| `aegis ingest services` | load `corpus/services.yaml`; fail on ambiguous mappings |
| `aegis ingest all` | services → git → logs → rollups, printing per-source counts and the unresolved report |
| `aegis serve-mcp` | stdio MCP server |
| `aegis investigate --scenario FILE` | run the agent, render markdown + JSON to stdout |

No `--dry-run` in v0.1 — revision 1 left it undefined, and it does not serve the falsifiable claim.
The CLI builds an `InvestigationRequest` and renders the result; it contains no orchestration.

## 9. Corpus and the brief allowlist

### `corpus/services.yaml`

```yaml
- name: checkout-api
  repo: acme/checkout
  log_keys: [checkout-api, checkout]
  k8s_names: [checkout-api]
  log_timezone: UTC
```

### Scenario file

Revision 1's scenario had no incident window — so the brief could not carry one — and put the answer
in the same file as the alert.

```yaml
name: checkout-5xx-spike
alert:
  service: checkout-api
  alert_name: HighErrorRate
  dedup_key: pd-checkout-5xx-0001
  fired_at: 2026-08-19T14:08:00Z
  window_start: 2026-08-19T13:38:00Z
  window_end: 2026-08-19T14:13:00Z
  payload: {summary: "5xx rate > 5% for 3m on checkout-api", severity: critical}
expect:                       # NEVER enters model context
  service: checkout-api
  root_cause_contains: ["timeout", "payments"]
  must_cite: ["commit:<full sha>", "rollup:checkout-api/2026-08-19T14:03:00Z/5xx/error/*"]
  ruled_out_contains: ["<distractor commit sha>"]
  forbidden_root_cause: ["<distractor commit sha>"]
  min_confidence: medium
  canary: "GROUND-TRUTH-CANARY-DO-NOT-LEAK"
  reachability:
    - fact: "timeout lowered 30s -> 3s"
      tool: get_incident_diff
      field: focus.deployments[].commit.files_changed[].hunks
      value_contains: ["timeout", "3"]
    - fact: "5xx volume rose after the deploy"
      tool: get_error_telemetry
      field: top_templates[].delta
      value_predicate: "> 0"
```

**Brief construction is an allowlist**, not "pass the scenario": exactly `alert.service`,
`alert_name`, `fired_at`, `payload`, `window_start`, `window_end`. `expect` and `reachability` never
reach the model. The `canary` string exists only in `expect`; a test asserts it appears in **no**
outbound model request.

### `corpus/generate.py`

Deterministic (`random.Random(1337)`), emitting `corpus/logs/checkout-api.log` and
`corpus/git/checkout.json`. Committed output; tests never regenerate.

### Six required scenario properties

| Property | Without it |
| --- | --- |
| Baseline period before the spike | Time alignment and anomaly computation untested |
| 5xx spike 90s after the causal deploy | Nothing to detect |
| Causal commit with a **neutral message** | An answer-bearing message tests leakage, not diagnosis |
| A **plausible non-causal deploy** in-window | The agent picks the only commit without reasoning |
| A **200/504 pair sharing one masked template** | The hash-only identity defect passes silently |
| At least one **unresolvable log line** | The `unresolved_events` path goes unexercised |

## 10. Reachability — semantic, not existential

`tests/corpus_contract/test_reachability.py` runs **before any agent**, resolves each entry's field
path against a real tool response built from the seeded database, and asserts the entry's
`value_contains` / `value_predicate` **against the resolved value**. Checking only that `hunks` or
`delta` exists would pass while the hunk lacks the timeout change or the delta describes the wrong
template — which is exactly the class of false-pass this check exists to prevent.

## 11. Tests

**Reader and parser (unit):** byte offsets across multibyte UTF-8, CRLF, blank lines, and invalid
UTF-8, with uids asserted against `log_uid()`; second-line offset exactness; unresolved variants
carrying no fabricated fields; reason precedence; each coercion rule.

**Conflict policy (integration):** exact-duplicate no-op; same uid with differing content raising
`EvidenceConflict`; unresolved→resolved promotion preserving the uid and removing the unresolved row;
resolved→unresolved raising; cursor regression and gap raising.

**Deployments:** exact replay no-op; every permitted forward transition; every forbidden differing
transition raising — asserted as a raise, not as an empty result.

**Pipeline:** report counts summing to the record count; watermark advanced; a rollback leaves rows,
rollups **and** watermark unchanged; replay after an injected mid-transaction failure succeeds and
advances the cursor. Double ingest asserts identical rollup **contents** — counts, cites, exemplars —
not merely the row count, which is unchanged even when the contents rot.

**Tools:** exact half-open boundaries for both tools, including a deployment at exactly `window_end`
being excluded; baseline-only template appearing with `count=0`, negative delta, empty
`source_cites`, populated `baseline_cites`, and a baseline exemplar; multi-minute exemplar selection
with unequal richness, absent JSON keys, and an equal-richness `uid` tie; a non-default
`baseline_sparse_threshold`; exact `status_breakdown`; trace-id dedup and order; the 200/504 pair
asserted as **two exact five-column keys with exact counts**; `include_other_services=False`.

**Determinism:** two fresh databases with shuffled physical insertion order and deliberately consumed
sequence gaps produce identical decoded tool payloads. Comparison is of decoded result content, not
raw JSON-RPC frames (whose request ids vary).

**MCP boundary:** tools invoked through the **spawned stdio server**; exactly two tools registered; a
controlled error arrives marked as an MCP error and a later call on the same session still succeeds;
stdout carries no non-protocol bytes.

**Agent loop (deterministic, no API):** a fake runner yielding multiple and parallel tool-use blocks
proves every envelope is reconstructed as the correct Pydantic type and captured **before**
extraction runs; turn-cap exhaustion raises `AgentTurnLimitExceeded`, emits the terminal trace, and
cleans up the subprocess.

**Isolation:** an agent-path test where only the MCP subprocess receives usable database credentials,
so any in-process database access from the agent fails — proving the tools are the only route.

**Eval** (needs `ANTHROPIC_API_KEY`): required-subset assertion (§13 answer 3) plus the canary
absence check and a fabricated-citation injection that must abort the run.

## 12. Exit criteria

- `aegis ingest all` twice yields identical row counts **and identical rollup contents**; the
  unmappable line is in `unresolved_events`.
- The 200/504 pair remains two distinct rollup identities with exact expected keys.
- Both tools, through the spawned stdio server, return byte-identical decoded payloads across runs
  and across two independently built databases.
- `aegis investigate --scenario checkout-5xx-spike` names `checkout-api`, identifies the timeout
  change, places the distractor in `ruled_out` and never in `root_cause`, and every citation was
  captured during that run.
- A fabricated citation aborts the run.
- The ground-truth canary appears in no outbound model request.

## 13. Resolved questions

1. **Brief construction** — allowlist `service`, `alert_name`, `fired_at`, `payload`, and explicit
   `window_start`/`window_end`; the agent learns what snapping did from the tool's
   `effective_window`. *Trade-off:* tests correlation rather than independent window inference, which
   is the right trade for this claim.
2. **`ingest_source` granularity** — one call per file in the CLI, but the primitive is tested
   directly with two deterministic byte batches and an injected failure. *Trade-off:* the CLI does
   not itself prove crash-resume, but the atomicity contract is genuinely exercised.
3. **Eval determinism** — required semantic subset plus location-specific forbidden conditions, not
   exact citation equality. *Trade-off:* extra valid citations won't fail the eval; exact payload
   regression lives in the tool tests.
4. **`unattributed`** — committed corpus stays empty; a dedicated integration fixture seeds one
   `service_id IS NULL` row and exercises the block over stdio. *Trade-off:* covers serialization
   without pretending Terraform ingest exists.

## 14. What revision 2 changed

| # | Defect in revision 1 | Fix |
| --- | --- | --- |
| 1 | Agent loop could not populate `captured_cites`; every run would fail provenance | §6 explicit state machine reconstructing envelopes before capture |
| 2 | Scenario leaked its answer and carried no window to pass instead | §9 allowlisted brief, `window_start`/`window_end`, canary test |
| 3 | Blanket `ON CONFLICT DO NOTHING` discarded conflicting evidence, no promotion | §1 four-outcome conflict policy |
| 4 | "Every other transition rejected" broke replay; `WHERE` predicate never raises | §3 three outcomes with explicit read-and-branch |
| 5 | Baseline-only templates could not appear despite negative-delta contract | §4 union identity universe with coalesced counts |
| 6 | One anomaly spans many rollups; "point join" ambiguous and nondeterministic | §4 window-level exemplar re-scoring with `uid` tiebreak |
| 7 | `LogEventDraft` could not represent `no_timestamp` / `unparseable` | §2 discriminated draft union |
| 8 | No reader contract produced byte offsets | §2 binary `readline` with explicit offset accounting |
| 9 | Turn cap neither passed nor enforceable; extraction turn uncounted | §6 explicit turn accounting with a reserved extraction turn |
| 10 | Inclusive `window_end` contradicted Part 0's half-open contract | §4 `resolve_window` first, `[start, end)` throughout |

**Also fixed:** `baseline_sparse_threshold` passed in rather than re-instantiated; `ToolError`
instead of success-shaped error dicts; stdout reserved for protocol; duplicate `files_changed` paths
rejected; `other_services` population deferred to Part 2 per the plan; `--dry-run` removed;
`UpsertCounts` replacing an undefined `int`; `status_breakdown` and `sample_trace_ids` given real
semantics; transaction ownership, cursor units, and report-count exclusivity all stated.

**Corrected claim:** "every returned row carries a `cite`" was false against the real API — only
`CommitRef`, `DeploymentRef`, `InfraChange`, and `Exemplar` have a singular `cite`.
