# Part 2 — v0.2 Offline Correlation Engine: implementation specification

> **Revision 2**, after review by `gpt-5.6-sol` (xhigh). Eleven blocking defects in revision 1, all
> verified against the built code and fixed here; §16 lists them. Two were milestone-breaking:
> attributed Terraform evidence was reachable through **no field of the frozen response envelope**,
> making two of the five scenarios mathematically unsolvable; and the pgvector `<=>` operator returns
> *distance*, so the specified ranking was exactly inverted.

Parts 0 and 1 are committed and gated: 170 tests against real PostgreSQL, `mypy --strict` clean.

## Falsifiable claim

> **One fixed schema and one three-tool interface make the required primary evidence reachable
> across every source type, and the same CLI agent diagnoses all five corpus incidents without
> source-specific agents.**

**The single test that falsifies it:** load the complete corpus twice into a clean database, run the
corpus-contract suite, then run all five agent evaluations. Falsified by any unreachable expected
fact, nondeterministic tool output, missing primary evidence when Tool 3 is disabled, or any failed
scenario.

## Why this milestone exists

v0.1 proved the mechanism on the easy case. v0.2 proves it is not *relying* on that case. The
load-bearing scenario is `payments-pool-exhaustion`, where **no deploy exists in the window at all**.

## Scope

**In:** three log formats (nginx, Python tracebacks, logfmt) · Terraform ingest · Kubernetes Events
and Pod status · ~15 postmortems with embeddings · Tool 3 · `other_services` and attributed
`infra_changes` · five scenarios · the corpus-contract gate · four prompt rules.

**Out:** FastAPI · alert deduplication · background execution · Slack · `/healthz` · trace
persistence · the GitHub webhook path · clean-room packaging.

---

## 0. Deliberate contract changes

Parts 0 and 1 are frozen **except** for the three changes below. Each is required by a v0.2 scenario
and cannot be worked around; each is listed here so the change is explicit rather than discovered
mid-implementation.

### 0.1 `ServiceChanges` gains `infra_changes` — the milestone depends on it

The built envelope is `ServiceChanges{service, commits, deployments}`, and `IncidentDiff.unattributed`
holds only rows with `service_id IS NULL`. A **successfully attributed** Terraform apply therefore
appears in no field at all, while `DiffCounts.infra_changes` reports that it exists. Both
Terraform-cause scenarios — `payments-pool-exhaustion` and `cdn-cache-miss-storm` — are unsolvable as
the envelope stands, regardless of parser, prompt, or agent quality.

```python
class ServiceChanges(BaseModel):
    service: str
    commits: list[CommitRef]
    deployments: list[DeploymentRef]
    infra_changes: list[InfraChange] = []      # NEW: attributed applies
```

Populated for `focus` and for each entry of `other_services`, ordered `applied_at DESC, uid ASC`
(the existing `UNATTRIBUTED_ORDER_KEY`). `unattributed` keeps its meaning: `service_id IS NULL` only.
A service with **only** infra changes in the window must still appear in `other_services`, or the
same hole reopens one level down.

`DiffCounts` stays focus-only and now genuinely matches what `focus` carries.

### 0.2 `LogFormat` becomes record-oriented

The built protocol is `parse(line, offset, ctx) -> Draft` — one draft per line, with no `finish` or
EOF hook. A Python traceback cannot be emitted under it: the header cannot be returned when it is
read, because later lines change its `raw`, `message`, `attrs`, and therefore its `uid`; and delaying
it leaves no way to emit it at EOF. Revision 1's claim that adding a format changes no caller was
false for any multi-line format.

```python
class LogFormat(Protocol):
    name: str
    def sniff(self, sample: Sequence[str]) -> float: ...
    def feed(self, line: str, offset: int, ctx: ParseContext) -> Iterable[Draft]: ...
    def finish(self) -> Iterable[Draft]: ...
```

`iter_drafts(path, ctx, formats)` keeps its public signature and now drains `finish()` at EOF.
Single-line formats yield exactly one draft per `feed` and nothing from `finish`, so JSON lines,
nginx, and logfmt are mechanical ports.

### 0.3 `ParseContext` gains `declared_service` and `dependency_roots`

nginx lines carry no service field, and the built `ParseContext{registry, source_file,
default_log_timezone}` has nowhere to put one, so every nginx record would be unresolvable.

```python
@dataclass(frozen=True)
class ParseContext:
    registry: ServiceRegistry
    source_file: str
    default_log_timezone: str
    declared_service: str | None = None          # NEW
    dependency_roots: tuple[str, ...] = ()       # NEW: for top_frame
```

`declared_service` comes from the per-file manifest in `corpus/logs/manifest.yaml`, which maps each
log file to its service, format hint, and timezone. It is used **only** when a record carries no
service of its own; a record naming a service still resolves through the registry, and a mismatch
between the two is `ambiguous_service`.

---

## 1. `ingest/logs.py` — three more formats

**One file has one format**, declared in the manifest and confirmed by sniffing. A service emitting
two formats gets two files (§15 answer 2). Per-line format fallback is out of scope: it would make
byte-offset accounting and record assembly ambiguous at once.

### 1.1 nginx combined + upstream timings

```
10.0.3.14 - - [19/Aug/2026:14:03:22 +0000] "POST /api/v1/checkout HTTP/1.1" 504 167 "-" "okhttp/4.12.0" rt=30.001 urt="30.001"
```

`status_code` from the status field; method and path into `attrs`; `duration_ms` from `rt` seconds ×
1000. `level` from `canonical_level(None, status_code)` — nginx carries no level, which is why Part 0
made that derivation non-null. Service comes from `ctx.declared_service`.

`rt=-` and `urt="-"` mean "no upstream" and yield `duration_ms = None`, not 0. Multiple
comma-separated upstream timings take the **last** value and record the full string in
`attrs.upstream_times`. A line that does not match the combined pattern is `unparseable` — never
partially parsed.

### 1.2 logfmt

```
ts=2026-08-19T14:03:22.481Z level=error service=payments-api msg="pool exhausted" pool_size=20
```

Bare `key=value`; double-quoted values may contain spaces and `\"` escapes. **Duplicate keys are
last-wins**, matching the JSON-lines rule from Part 1 §2, and the choice is documented rather than
incidental. Unknown keys go to `attrs`. Coercion rules are Part 1's unchanged.

### 1.3 Python logging with tracebacks

Revision 1's state machine was internally inconsistent and failed common tracebacks. The corrected
design buffers the whole record and uses four substates.

**Header regex** (the only thing that closes a record):

```
^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+
 (?P<level>[A-Z]+)\s+\[(?P<service>[^\]]+)\]\s+
 (?P<logger>[\w.]+):\s(?P<message>.*)$
```

Anything not matching this is a continuation line. **Header precedence is absolute**: a continuation
line that resembles an exception is still a continuation.

**Substates:** `body` → `traceback` → `after_exception` → `traceback` (chained) …

| In state | Line | Action |
| --- | --- | --- |
| any | header | flush the open record for this stream; start a new one in `body` |
| `body` | `Traceback (most recent call last):` | enter `traceback` |
| `traceback` | `^\s+File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.+))?$` | append frame — **`, in <func>` is optional**, because `SyntaxError` frames often omit it |
| `traceback` | `^(?P<exc>[A-Za-z_][\w.]*)(?::[ ]?(?P<detail>.*))?$` on a **non-indented** line | terminate: set `message` and `attrs.exc_type`; enter `after_exception` |
| `after_exception` | chain separator | enter `traceback`, starting a chained traceback in the same record |
| `after_exception` | any other continuation | append to the exception detail (multi-line details are common) |
| any | EOF | flush all open records |

The terminator pattern now accepts `KeyboardInterrupt` (no colon at all) and `RuntimeError:` (colon,
empty detail). Revision 1 required `": "` and matched neither.

**Chain separators** are recognized in `after_exception`, not `traceback` — revision 1 had the first
exception line exit traceback state and then looked for separators in a state it had already left.

**`top_frame` is the LAST frame outside `ctx.dependency_roots`**, not the first: Python frames run
outermost to innermost. Roots are configuration, not a substring test.

**Streams.** The assembler keeps a map of open records keyed by stream identity (`threadName`,
`request_id`, or `trace_id` when the header supplies one). Revision 1 said both "any header closes
any open record" and "state is keyed by stream", which cannot both hold. With no key available and a
different logical stream interleaving, the record flushes with `attrs.assembly = "ambiguous"` and
**publishes no `top_frame`** — a frame attributed to the wrong request sends the agent to an innocent
file.

**Identity and offsets.** A record's `source_offset` is its **header line's** byte offset; `raw` is
the concatenation of its original **bytes**, decoded once after assembly (Part 0 §13 order: bytes →
decode → normalize). `log_uid(file, header_offset, raw)` is therefore stable across re-ingest of an
unchanged file.

**Sealed-file rule.** Multi-line assembly does not compose with an append-resume cursor: ending
ingest mid-traceback would emit a partial record and advance the watermark past it, and appending the
exception line afterwards produces a *different* `raw` and therefore a different uid, leaving both a
partial and a complete row. In v0.2 **Python log artifacts are declared sealed** — the manifest marks
them `sealed: true`, and `ingest_source` refuses to advance the watermark past an open record. Append
support is deferred.

---

## 2. `ingest/terraform.py`

`terraform show -json` **plan** output describes intended actions and carries no execution evidence.
Two committed inputs, joined: `corpus/terraform/plan-<id>.json` and `corpus/terraform/applies.json`
(`[{apply_id, plan_file, status, applied_at}]`). **Only `status: "success"` entries are ingested**,
and `applied_at` comes from the manifest.

Walk `.resource_changes[]`, skipping `["no-op"]`:

| `.change.actions` | `action` |
| --- | --- |
| `["create"]` / `["update"]` / `["delete"]` | same |
| `["delete","create"]` **or** `["create","delete"]` | `replace` |

Both orders occur; Terraform emits `["create","delete"]` under `create_before_destroy`. Any other
array is an ingest error, not a silent skip.

**`resource_name` is Terraform's full `.address`** — `module.payments.aws_db_instance.pool["a"]` —
not the leaf `.name`. Two modules can hold the same type and leaf name, and the frozen
`infra_change_uid(apply_id, resource_type, resource_name, action, provider)` would alias them into
one identity, raising `EvidenceConflict` or losing one silently.

**`attribute_diff`** is a recursive `before`/`after` comparison keeping only changed leaves, skipping
keys present in `.change.after_unknown` (a parent of `true` prunes its whole subtree), and
**recursively redacting any subtree marked in `before_sensitive`/`after_sensitive`** to
`"<redacted>"`. Without that, a credential rotation writes both secrets into an agent-visible diff.

**Attribution** reads `after.tags`, falling back to `before.tags` when `after` is null **or** when
`after.tags` yields no match — a delete has `after: null`, which is exactly
`payments-pool-exhaustion`'s shape.

---

## 3. `ingest/k8s.py`

Two committed inputs, both normalized into `log_events`.

**Byte offsets for JSON arrays.** Part 0 requires a non-null byte-semantic `source_offset`, but
`json.load` over an array gives no per-item offsets. Rule: these files are read with a streaming
parser that records each item's **start byte offset within the file**; `raw` is that item's original
bytes. This keeps `log_uid` semantics identical to line-oriented sources.

### 3.1 `pod-status.json`

`OOMKilled` lives in `status.containerStatuses[].lastState.terminated.reason`, with `restartCount`
and `exitCode` — **not** on Event objects. Each terminated container state becomes one row at
`lastState.terminated.finishedAt`, `level='error'`,
`message = "<reason>: container <name> terminated (exit <code>)"`,
`attrs = {source: "k8s", kind: "PodStatus", reason, restart_count, exit_code}`.

### 3.2 `events.json`

`level` is `warning` when `type == "Warning"` else `info`; `message` is `"<reason>: <message>"`;
`ts` is `lastTimestamp`, falling back to `eventTime`, then `series.lastObservedTime`; a row with none
is `no_timestamp`.

**An Event's `count` is preserved, not expanded** — one row carrying `attrs.occurrence_count`.
Expanding fabricates timestamps the source never gave; collapsing undercounts.

**Snapshot replacement.** `k8s_event_uid(event_uid, count, last_timestamp)` includes `count`, so the
same Event observed at count 1 and later at count 7 produces **two different uids** and both rows
persist — reporting 8 occurrences. Rule: an Event row is keyed for replacement on `event_uid`; a new
observation with a higher `count` **replaces** the prior row in the same transaction, and the dirty
set covers both the old and new minute.

### 3.3 `occurrence_count` data path

Frozen `error_rollups` aggregates `log_events` rows and stores no occurrence total, so
`TemplateAnomaly.occurrence_count` cannot come from rollups.

- **`count` and `delta` remain row counts.** One Event contributes 1.
- `get_error_telemetry` runs a **separate current-window aggregation** over K8s Event base rows
  matching the anomaly identity, summing `attrs.occurrence_count`, and puts the total in
  `occurrence_count`. Non-K8s identities get `None`; mixed identities sum only the K8s rows and set
  `attrs`-derived totals honestly.
- **`occurrence_count` never affects ranking** — there is no baseline occurrence field, and weighting
  only K8s rows would make deltas incomparable across sources. A high-count Event can therefore rank
  below a chatty log template; the tool docstring and the prompt say so explicitly.

---

## 4. `embeddings/`

```python
class EmbeddingProvider(Protocol):
    dim: int
    model_fingerprint: str
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
```

**`OllamaEmbeddings`** posts to `{base_url}/api/embed` with
`{"model": ..., "input": [...], "truncate": false}` — silent truncation would embed a prefix and
report success. Validation on **every** response, not just the first: exactly one vector per input,
each of `dim` components, all finite, and non-zero norm. Batches of 32, 120s timeout, three retries
on connection and 5xx errors only; a malformed 200 is **not** retried, because retrying a
deterministic parse failure just burns time.

**`FixtureEmbeddings`** returns vectors from a committed map keyed by exact text with a declared
neighbourhood structure. Never hash-derived: SHA vectors have arbitrary neighbourhoods, so
"the expected postmortem is nearest" would assert nothing.

**`model_fingerprint`** is stored on each `postmortems` row alongside `content_sha`. Changing
`embedding_model` while content is unchanged would otherwise leave old vectors searched by new-space
queries at the same dimension — silently meaningless similarities with no error. Unchanged content is
treated as unchanged embeddings **only when the fingerprint also matches**.

**Environment.** Ollama is not installed here and `bge-m3` is ~1.2 GB against ~191 MB free. All
fixture-backed gates run locally; Ollama acceptance is **required-but-unexecuted** and must run
elsewhere (§15 answer 5). Do not claim the milestone verified on fixture gates alone.

---

## 5. `ingest/postmortems.py`

Front matter (`title`, `occurred_at`, `services`), `## Resolution` extracted to `resolution_md`,
chunked, embedded, upserted on `slug`.

**Chunking:** split on `##`; any section over the token cap splits again on paragraph boundaries.
Ordinals are **zero-based**. `content_sha` covers front matter and body together, so a metadata-only
edit re-embeds.

**The Resolution section is capped, not split.** The frozen `PostmortemHit` has one `resolution_md`
and one `resolution_cite`; a split resolution would either return text one citation cannot support,
or duplicate every hit once per resolution chunk — destroying the total order. Therefore: a
Resolution section over the cap is an **ingest error**, and **more than one `## Resolution` heading is
an ingest error**. Corpus authoring works within that.

**Edit policy:** a changed `content_sha` **or** `model_fingerprint` deletes all chunks for that
postmortem and re-inserts, in one transaction, with the DELETE **executed and flushed before** the
inserts — ORM relationship replacement can otherwise emit INSERT first and trip
`UNIQUE (postmortem_id, ordinal)`. An embedding failure mid-edit rolls the whole edit back.

**Ingest is prohibited during an investigation** in this offline milestone. Tool 3 could otherwise
return a citation, an edit could commit, and a later tool call in the same run would see a different
corpus — leaving a summary that validates against captured history but no longer re-resolves.

---

## 6. Tool 3 — `search_similar_postmortems`

```python
def search_similar_postmortems(
    session: Session, *, error_signature: str, k: int = 5,
    service: str | None = None, provider: EmbeddingProvider,
) -> list[PostmortemHit]
```

**`<=>` is cosine DISTANCE, not similarity.** Verified against the running server: identical vectors
give `0`, orthogonal `1`, opposite `2`. Revision 1 ordered `similarity DESC` on that value, which
ranks the worst hit first, and dropped `similarity < 0.35`, which discards exact matches.

```sql
SELECT ..., (c.embedding <=> :query) AS distance
FROM postmortem_chunks c JOIN postmortems p ON ...
WHERE (c.embedding <=> :query) <= 0.65
  AND (:service IS NULL OR p.services @> ARRAY[:service])
ORDER BY distance ASC, p.slug ASC, c.ordinal ASC
```

`similarity = 1 - distance` is computed for the response. The floor is expressed as
`distance <= 0.65`, equivalent to the intended `similarity >= 0.35`.

**`k` counts distinct postmortems, not chunks.** Rank the best chunk per slug, then limit — otherwise
five chunks of one document fill every slot in a function named *search_similar_postmortems*.

`resolution_cite` is the resolution chunk's own citation; a hit matched on a Symptoms chunk must not
appear to have cited the resolution text it returns. `k <= 0` and an unknown `service` raise
`QueryError`. `snippet` is the matched chunk truncated to 500 characters on a word boundary.

### 6.1 Capture seam — Tool 3 returns a list

The frozen agent loop reconstructs **one Pydantic `BaseModel`** per tool result, and
`RunContext.capture_tool_result` requires a `BaseModel`. A bare `list[PostmortemHit]` fits neither:
`ENVELOPE_BY_TOOL` would raise `KeyError`, or validation would be attempted on a list. If capture is
skipped, valid postmortem citations fail provenance — the same failure shape Part 1's review caught.

```python
class _PostmortemHits(RootModel[list[PostmortemHit]]): ...
ENVELOPE_BY_TOOL["search_similar_postmortems"] = _PostmortemHits
```

The wire format stays a JSON array. A test must assert **both** `cite` and `resolution_cite` are
harvested into `captured_cites` from a list-root result.

### 6.2 Server binding

`Session` and `EmbeddingProvider` are not model-visible arguments. The server constructs both per
call and binds them; only `error_signature`, `k`, and `service` are tool parameters. The MCP child
process therefore needs `AEGIS_OLLAMA_BASE_URL`, `AEGIS_EMBEDDING_MODEL`, and `AEGIS_EMBEDDING_DIM`
in addition to the database URL — `agent/transport.py` passes the whole environment plus an explicit
database URL, so this works, but a test must prove the child is configured, not just the parent.

A provider timeout must surface as a `ToolError`, not tear down the transport — otherwise nested
anyio task groups re-wrap the failure exactly as Part 1's review found.

---

## 7. `other_services` and attributed infra

`get_incident_diff` returns every **other** service with commits, deployments, **or infra changes** in
the queried window (including the Tool 1 lookback interval), ordered `service ASC`, each carrying the
extended `ServiceChanges` from §0.1.

Commits, deployments, and infra changes are fetched with **separate queries keyed by service**, not
one joined query — a naive join multiplies commits by deployments and produces duplicates that a
one-row fixture never reveals.

`include_other_services=False` still yields an empty list; both paths are asserted.

---

## 8. Agent prompt — four rules

1. Candidates in `other_services` **must be addressed**; a deploy in the window is a candidate.
2. **Cross-service claims cap at `confidence: medium`** when supported only by co-timed series.
3. `baseline_sparse: true` means delta ranking is unreliable — say so, cap at `medium`.
4. Insufficient evidence → `confidence: low`, not a filled gap.

Plus one documentation rule: `occurrence_count` reports observed occurrences of a Kubernetes Event
and **does not participate in ranking**, so a high-count Event may appear below a chatty log
template.

---

## 9. Corpus

Five scenarios as in the plan. **Isolation is asserted over each scenario's full evidence horizon** —
the snapped telemetry baseline **and** the Tool 1 lookback interval — not merely the alert window.
Checking the alert window alone leaves one scenario's evidence inside another's lookback.

~15 postmortems: five near-neighbours, ten distractors.

**Non-substitutability is an authoring rule with a mechanical leakage scan**, not a proof (§15 answer
4). The scan is a denylist generated from each scenario's root-cause facts, identifiers, and exact
changed values (`30s`, `3s`, `max_connections`), asserting none appears verbatim in any postmortem.
It detects copied ground truth; it cannot detect paraphrase. The separate structural guarantee is
that **every scenario surfaces its primary evidence with Tool 3 disabled**.

---

## 10. Tests

Only the additions and the ones whose weak form false-passes are listed; the full inventory follows
the same shape as Part 1.

**Parsers:** each format on well-formed, malformed, boundary input. Traceback suite must include
bare `KeyboardInterrupt`, `RuntimeError:` with empty detail, a multi-line detail, a `SyntaxError`
frame **without `, in`**, a caret line retained in `raw`, a chained traceback, a colon-bearing
continuation line that must **not** become `exc_type`, and two genuinely interleaved keyed streams.
Multi-line records must be tested **through the binary reader, uid generation, pipeline ingest,
watermark, and replay** — a string-only assembler test misses CRLF, replacement characters, and
header-offset identity.

**Terraform:** both replacement orders; zero rows when no successful apply; `after_unknown` pruning
including a `true` parent; `before.tags` fallback on delete **and** on unmatched `after.tags`;
sensitive subtree redacted; **two resources with the same leaf name in different modules** producing
two identities.

**Kubernetes:** `OOMKilled` sourced from pod-status with an explicit assertion it is not from Events;
Event `count=7` verified **all the way through** `error_rollups`, series, status breakdown, anomaly
`count`/`delta`, and `occurrence_count` — an `attrs`-only assertion passes while telemetry reports
`None`; successive snapshots at count 1 then 7 producing one row, not two.

**Embeddings:** response-cardinality mismatch, wrong dimension after the first vector, zero and
non-finite vectors, `truncate:false` sent, retry classification; a fingerprint change forcing
re-embed.

**Tool 3:** real pgvector rows for identical, orthogonal, and low-similarity vectors — proving the
distance/similarity direction is right, since a determinism suite passes perfectly with backward
ranking; `k` counting slugs not chunks; service filtering; equal-score ties; resolution citation
correctness; oversized and duplicate Resolution sections rejected at ingest.

**Composition (§11):** the seams, tested as seams.

**`other_services`:** multiple rows per service, an **infra-only** service, equal timestamps, nested
ordering, focus-only counts, `include_other_services=False`.

---

## 11. Composition tests — the seams this milestone introduces

Part 1's worst defect existed only between packages. These are the equivalents here, and each gets a
test that exercises the seam rather than either side of it.

| Seam | What goes wrong | Test |
| --- | --- | --- |
| CLI → new loaders | A loader is complete but never called by `aegis ingest all`, repeating "nothing spawned the server" | Subprocess `aegis ingest all` against a fresh database asserting **exact non-zero counts per source** |
| K8s loader → pipeline → rollups | Rows inserted without dirty-set capture, so OOM evidence never reaches telemetry | Ingest via `ingest_source`, then assert the K8s identity appears in `get_error_telemetry` |
| Server → provider | Three tools register but Tool 3 fails because no provider is bound | Invoke Tool 3 **through spawned stdio** against a tiny fake local `/api/embed` |
| Transport env → MCP child | Child uses different Ollama/model/dim settings than the parent | Assert the child's effective settings, not the parent's |
| Tool 3 → agent capture | Model sees the list, provenance never captures it, failure appears later as a fabricated citation | Deterministic loop test with a list-root result asserting both citation fields captured before extraction |
| Postmortem edit → flush order | INSERT before DELETE trips the unique constraint | Edit through the real ORM path |
| Double ingest → all sources | Both runs produce identical **zeros** when loaders are unwired | Assert exact non-zero counts, not merely equality |

---

## 12. Exit criteria

- `aegis ingest all` twice yields identical row counts **and identical rollup contents**, with
  **exact non-zero counts asserted for every source**.
- A Terraform plan with no successful apply produces zero `infra_changes` rows.
- An attributed apply is returned through spawned stdio in `focus.infra_changes` with its diff and
  citation.
- `OOMKilled` sourced from pod-status, asserted explicitly.
- All three tools return byte-identical decoded payloads across repeated calls and across two
  independently built databases.
- Tool 3 ranks nearest-first against real pgvector vectors.
- Every reachability entry resolves **and satisfies its predicate**; every scenario surfaces primary
  evidence with Tool 3 disabled; scenario evidence horizons do not overlap.
- All five evaluations pass, including `payments-pool-exhaustion` with no deploy in window.

**Conditional:** Ollama acceptance tests are required and **cannot run in this environment**. The
milestone's claim is not fully verified until they run elsewhere. Report them as
required-but-unexecuted rather than as passing.

## 13. Deliberately does not prove

Alert concurrency · Slack delivery · crash recovery · live-source robustness · clean-machine
reproducibility · superiority over a multi-agent alternative.

---

## 14. Build order

1. §0 contract changes (envelope, protocol, context) with their tests — everything else depends on
   them, and doing them last means rewriting the formats.
2. nginx and logfmt (mechanical ports proving the new protocol).
3. The traceback assembler, through the binary reader and pipeline.
4. Terraform, then Kubernetes.
5. `embeddings/` with `FixtureEmbeddings`; `OllamaEmbeddings` behind it.
6. Postmortem ingest; Tool 3; the capture seam and server binding.
7. `other_services` and attributed infra population.
8. Corpus expansion, the contract gate, prompt rules, evaluations.

## 15. Resolved questions

1. **Disabling Tool 3** — registry injection on an **internal** server builder, with a test-only
   stdio entrypoint starting a two-tool server. Public `create_server(settings)` always registers
   three. The agent runs against that reduced server; calling query functions directly proves nothing
   about tool absence over transport. *Trade-off:* one extra harness entrypoint, no production
   disable flag.
2. **Multi-format services** — two immutable files, one format each. No per-line fallback.
   *Trade-off:* mixed streams must be split before ingest; offsets and identity stay intelligible.
3. **`occurrence_count`** — contributes 1 to `count`/`delta`, surfaced separately, never ranked.
   *Trade-off:* a high-count Event can rank below chatty logs; documented in tool and prompt.
4. **Postmortem non-substitutability** — authoring rule plus a mechanical **leakage scan** (verbatim
   denylist). Described as leakage detection, not proof. *Trade-off:* paraphrase remains judgment.
5. **Ollama** — fixture gates pass locally; Ollama acceptance is required-but-unexecuted and runs
   elsewhere. *Trade-off:* the Part 2 claim stays conditional on an external run.

---

## 16. What revision 2 changed

| # | Defect in revision 1 | Fix |
| --- | --- | --- |
| 1 | Attributed Terraform evidence reachable through no envelope field — both Terraform scenarios unsolvable | §0.1 `ServiceChanges.infra_changes` |
| 2 | Frozen per-line `parse()` cannot emit multi-line records | §0.2 `feed`/`finish` protocol |
| 3 | Traceback machine failed `KeyboardInterrupt`, empty details, multi-line details, `SyntaxError` frames, chained state, and misclassified colon-bearing lines | §1.3 four substates, optional `, in`, header precedence, per-stream records |
| 4 | Tool 3's list result cannot enter the `BaseModel` capture seam | §6.1 `RootModel` reconstruction |
| 5 | `<=>` is distance; ranking and floor were inverted | §6 `distance ASC`, `<= 0.65`, `similarity = 1 - distance` |
| 6 | An oversized Resolution breaks citation correctness and total ordering | §5 cap-and-reject, single Resolution heading |
| 7 | nginx records unattributable — no declared service in the frozen context | §0.3 `declared_service` |
| 8 | `occurrence_count` had no data path from rollups | §3.3 separate aggregation, never ranked |
| 9 | Header-offset identity does not compose with an append cursor | §1.3 sealed-file rule |
| 10 | Terraform leaf `.name` aliases resources across modules | §2 full `.address` |
| 11 | Fixture provider cannot serve agent-invented signatures | §4, §15.5 split acceptance matrix |

**Also fixed:** delete-flush before insert and no ingest during investigation; per-response Ollama
validation with `truncate:false` and retry classification; `model_fingerprint` stored and compared;
Terraform sensitive-value redaction; `k` counting distinct slugs; isolation asserted over baseline and
lookback horizons; K8s JSON byte-offset rule; Event snapshot replacement; separate per-service queries
to avoid join multiplication; registry injection behind an internal builder.
