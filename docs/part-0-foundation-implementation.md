# Part 0 — Foundation: implementation specification

> **Revision 2**, after review by `gpt-5.6-sol` (xhigh). Eleven blocking defects were found in
> revision 1 and all are fixed here; §17 lists them. The root cause of most of them was a single
> mistake — revision 1 used five different identity schemes for evidence (sequence ids, 7-character
> SHAs, delimiter-joined hashes, mutable service names, unconstrained composites), so "stable
> citation" was never actually a property of the system. §0 replaces all of them with one contract.

This is the build spec for the invariants in the versioned plan: persistent identity, key
correctness, and public interface shape. Everything here ships in v0.1 and is never revisited.

**Acceptance gate:** every test in §15 passes; `alembic upgrade head` then `downgrade base`
round-trips on a clean volume; and ingesting the same corpus into two fresh databases produces
byte-identical tool output, including every citation.

---

## 0. The evidence identity contract

Every fact the agent can cite must have an identifier that is **derived from content, stable across
replay, unique, and independent of database state**. This is the contract; everything else in this
document serves it.

### 0.1 Why sequence ids are banned from public surfaces

PostgreSQL sequences are not transactional: a rolled-back insert and an
`ON CONFLICT DO NOTHING` both consume their allocated value. Ingest the same corpus into two fresh
databases, or crash and replay one, and `log_events.id` differs. Any citation containing that id
differs too — so `must_cite` fixtures break, byte-identical tool output is unachievable, and the
eval baseline is meaningless.

Sequence ids remain as internal primary keys and join targets. They never appear in a tool response
field or a citation.

### 0.2 `uid` — the canonical evidence identifier

```python
UID_VERSION = 1

def uid(kind: str, **fields: Any) -> str:
    """Content-derived, type-preserving, collision-resistant evidence identity."""
    payload = json.dumps(
        {"v": UID_VERSION, "kind": kind, **fields},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
```

Canonical JSON, not delimiter concatenation. Revision 1 used `"|".join(...)`, under which
`("a", 1, "2|raw")` and `("a|1", 2, "raw")` both serialize to `a|1|2|raw` and collide — and with
`ON CONFLICT DO NOTHING` the second, legitimate row is silently discarded. JSON also preserves types
(`1` ≠ `"1"`) and distinguishes `null` from `""`, both of which the delimiter form aliased.

`_json_default` accepts only `datetime` (rendered per §12.3) and raises on anything else, so no
object's `repr` can leak into an identity.

`UID_VERSION` is embedded in the hashed payload. Changing any input normalization rule (§13) requires
bumping it, which makes the break explicit and greppable instead of silent.

### 0.3 The citation grammar

| Kind | Format | Identity source |
| --- | --- | --- |
| Commit | `commit:<full 40-char sha>` | git's own content hash |
| Deployment | `deploy:<uid>` | §0.2 |
| Infra change | `infra:<uid>` | §0.2 |
| Log event | `log:<uid>` | §0.2 |
| Rollup row | `rollup:<service>/<iso>/<status_class>/<level>/<template_hash>` | full five-column PK |
| Postmortem chunk | `postmortem:<slug>@<content_sha8>#<ordinal>` | slug + content hash + position |

Four deliberate changes from revision 1:

- **Full commit SHA, not `sha7`.** Seven hex characters is 28 bits; prefix collisions are a normal
  occurrence in real repositories, and a colliding citation resolves to the wrong commit.
- **`deploy`, `infra`, and `log` carry `uid`**, not a sequence id.
- **`postmortem` includes `content_sha8`.** Editing a postmortem re-chunks it, so
  `postmortem:foo#3` would silently come to mean different text. Including the content hash makes an
  edited document produce different citations rather than quietly redefining old ones.
- **Rollup citations name the service by `name`, which is therefore an immutable identifier.** The
  registry loader (§7.1) rejects reuse of a retired name; a rename is a new service. Stated as a rule
  because the alternative — mutable names inside citations — makes citations unstable by
  construction.

**Component charset.** Each component matches `[A-Za-z0-9._@-]+` except the rollup timestamp (`:` and
`Z` permitted). Slugs and service names are validated against that charset at load time, so no
component can contain a delimiter and parsing is unambiguous left to right.

### 0.4 What a citation guarantees, and what it does not

A citation identifies **evidence a tool returned during a specific run**. For a fixed corpus it is
stable and re-resolvable. It is **not** a durable database pointer in a live system: late-arriving
log data legitimately changes a rollup bucket's `count`, so a rollup citation may later resolve to a
row with different numbers.

Therefore any claim's supporting values are carried **in the summary itself** (`count`,
`baseline_count`, `delta`), not left as a promise about what the database will say later. The
citation says *where this came from*; the summary says *what it said at the time*.

---

## 1. Repository scaffold

```
aegis-context/
├── pyproject.toml · docker-compose.yml · Makefile · alembic.ini · .env.example
├── docs/ · corpus/
└── src/aegis/
    ├── config.py · cli.py
    ├── db/{models.py,session.py}
    ├── migrations/{env.py,versions/}
    ├── ingest/{identity.py,templates.py,normalize.py,timewindow.py,textnorm.py}
    ├── aggregate/rollups.py
    ├── mcp_server/{citations.py,schemas.py}
    ├── agent/summary.py
    └── app/{investigate.py,run_context.py}
```

`requires-python = ">=3.11"`. Runtime dependencies: `sqlalchemy`, `alembic`, `psycopg[binary]`,
`pgvector`, `pydantic`, `pydantic-settings`, `typer`, `rich`, `pyyaml`, `python-dateutil`,
**`fastmcp`**, **`anthropic[mcp]`**, `httpx`. The last three were missing from revision 1, which
listed an MCP server and an agent loop whose imports would fail in a clean install.

Dev: `pytest`, `pytest-asyncio`, `testcontainers[postgres]`, `ruff`, `mypy`, `freezegun`.

`docker-compose.yml` — `pgvector/pgvector:pg17`, credentials `aegis`, **host port 5433**, named
volume, `pg_isready` healthcheck. The database is **dedicated to this project** (see §4).

---

## 2. `config.py`

`BaseSettings`, `env_prefix="AEGIS_"`, `frozen=True`.

Two fields are `Literal`, not free integers, because the schema and SQL hard-code them:

| Field | Type | Default | Note |
| --- | --- | --- | --- |
| `embedding_dim` | `Literal[1024]` | `1024` | the column is permanently `Vector(1024)`; a settable 768 would pass startup and fail at insert |
| `rollup_bucket_seconds` | `Literal[60]` | `60` | `date_trunc('minute')`, the rollup PK, window snapping, and citations are all minute-based |

Remaining fields: `database_url`, `ollama_base_url`, `embedding_model`, `anthropic_model`,
`agent_effort`, `agent_max_turns`, `slack_webhook_url`, `github_webhook_secret`, `corpus_dir`,
`baseline_sparse_threshold` (default 50), `hunk_max_files` (15), `hunk_max_hunks_per_file` (3),
`hunk_max_lines_per_file` (60).

Ollama's live vector length is asserted against `embedding_dim` on first use, and a mismatch is a
startup error rather than an insert-time one.

---

## 3. Schema — `db/models.py`

All timestamps `TIMESTAMPTZ` (§12). All tables carry `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
**Every FK declares an explicit `ondelete`/`onupdate`; every column declares explicit nullability.**
Revision 1 left both to defaults, which is how the rollup/exemplar deadlock in §8.4 arose.

### 3.1 `services`

`id Integer PK` · `name Text UNIQUE NOT NULL` (immutable identifier, charset per §0.3) ·
`repo Text NULL` · `log_keys ARRAY(Text) NOT NULL server_default '{}'` ·
`k8s_names ARRAY(Text) NOT NULL server_default '{}'` ·
`infra_tags JSONB NOT NULL server_default '{}'` · `log_timezone Text NOT NULL server_default 'UTC'`.

Defaults are **server** defaults, so a raw SQL insert cannot bypass them.

### 3.2 `commits`

`sha Text PK` (full 40 chars, `CHECK (sha ~ '^[0-9a-f]{40}$')`) ·
`service_id Integer FK→services.id ON DELETE RESTRICT NOT NULL` ·
`authored_at`, `committed_at` NOT NULL · `message Text NOT NULL` · `author Text NULL` ·
`pr_number Integer NULL` · `files_changed JSONB NOT NULL` · `additions`, `deletions` Integer NOT NULL.
Index `(service_id, committed_at DESC)`.

`files_changed` element shape, enforced by both a Pydantic writer model **and** a database CHECK:

```json
{"path": "…", "status": "added|modified|removed|renamed",
 "additions": 3, "deletions": 3,
 "hunks": "@@ …" ,          // string or null
 "hunks_omitted": null}     // null | "budget" | "webhook"
```

```sql
CONSTRAINT ck_files_changed_shape CHECK (
  jsonb_typeof(files_changed) = 'array'
  AND NOT jsonb_path_exists(files_changed,
    '$[*] ? (!(@.status == "added" || @.status == "modified"
           || @.status == "removed" || @.status == "renamed"))')
  AND NOT jsonb_path_exists(files_changed,
    '$[*].hunks_omitted ? (@ != null && @ != "budget" && @ != "webhook")')
)
```

Revision 1 left this to application code on the grounds that a CHECK "cannot portably reach inside
JSONB". This project is PostgreSQL-only and PG17 has JSONPath, so the excuse did not apply, and any
bulk-load path bypassing the writer model could insert an invalid reason.

**Cap precedence** when a commit exceeds the budgets in §2: files are sorted by
`(additions + deletions) DESC, path ASC`; the first `hunk_max_files` receive hunks; the rest carry
`hunks: null, hunks_omitted: "budget"`. Within a kept file, if the diff exceeds
`hunk_max_hunks_per_file` or `hunk_max_lines_per_file`, **no partial hunks are stored** — the whole
file is marked `"budget"`. A truncated diff is worse than an absent one, because it looks complete.

### 3.3 `deployments`

`id Integer PK` · `uid Text UNIQUE NOT NULL` · `service_id FK ON DELETE RESTRICT NOT NULL` ·
`commit_sha Text FK→commits.sha ON DELETE RESTRICT NOT NULL` · `environment Text NOT NULL` ·
`started_at NOT NULL` · `finished_at NULL` ·
`status Text NOT NULL CHECK (status IN ('in_progress','success','failed','rolled_back'))`.
Index `(service_id, started_at DESC)`.

Deployment time is never derived from commit time. `uid = uid("deploy", commit_sha=…,
environment=…, started_at=…)` — deliberately excluding `status` and `finished_at`, which are the
mutable lifecycle fields (§5.2).

### 3.4 `infra_changes`

`id Integer PK` · `uid Text UNIQUE NOT NULL` · `provider Text NOT NULL` ·
`resource_type Text NOT NULL` · `resource_name Text NOT NULL` · `resource_id Text NULL` ·
`action Text NOT NULL CHECK (action IN ('create','update','delete','replace'))` ·
`attribute_diff JSONB NOT NULL` · `applied_at NOT NULL` · `apply_id Text NOT NULL` ·
`source_ref Text NULL` · `service_id FK ON DELETE RESTRICT NULL`.

`service_id` is nullable: shared infrastructure has no owning service, and `get_incident_diff`
surfaces those rows in `unattributed`.

### 3.5 `log_events`

`id BigInteger PK` · `uid Text UNIQUE NOT NULL` · `ts TIMESTAMPTZ NOT NULL` ·
`service_id FK ON DELETE RESTRICT NOT NULL` ·
`level Text NOT NULL CHECK (level IN ('debug','info','warning','error','fatal'))` ·
`status_code Integer NULL` · `trace_id Text NULL` · `message Text NOT NULL` ·
`template_hash CHAR(32) NOT NULL` · `raw Text NOT NULL` · `attrs JSONB NOT NULL server_default '{}'` ·
`source_file Text NOT NULL` · `source_offset BigInteger NOT NULL`.
Indexes `(service_id, ts DESC)`, `(template_hash)`, `(trace_id) WHERE trace_id IS NOT NULL`.

`template_hash` is **32 hex characters (128 bits)**, widened from revision 1's 16. A permanent
grouping identity should not carry a birthday bound this cheap to raise; collisions silently merge
unrelated evidence and are undetectable after the fact.

### 3.6 `unresolved_events`

`id BigInteger PK` · `uid Text UNIQUE NOT NULL` · `ts NULL` · `raw Text NOT NULL` ·
`reason Text NOT NULL CHECK (reason IN ('no_service_match','ambiguous_service','no_timestamp','unparseable'))` ·
`source_file Text NOT NULL` · `source_offset BigInteger NOT NULL`.

`uid` is computed identically to a `log_events` uid, so promotion (§14.2) preserves identity.

### 3.7 `error_rollups`

`service_id FK ON DELETE RESTRICT NOT NULL` · `bucket_start NOT NULL` ·
`status_class Text NOT NULL CHECK (status_class IN ('2xx','3xx','4xx','5xx','none'))` ·
`level Text NOT NULL CHECK (level IN ('debug','info','warning','error','fatal'))` ·
`template_hash CHAR(32) NOT NULL` · `count Integer NOT NULL CHECK (count > 0)` ·
`first_seen NOT NULL` · `last_seen NOT NULL` ·
`exemplar_log_event_id BigInteger FK→log_events.id ON DELETE RESTRICT NOT NULL`.

`PrimaryKeyConstraint(service_id, bucket_start, status_class, level, template_hash)`.
Index `(service_id, bucket_start DESC)`.

The `level` CHECK was missing in revision 1 — the plan requires every enumerated domain to be a
constraint, and this one was described as such while not being written.

`ON DELETE RESTRICT` on the exemplar is deliberate and pairs with the deletion order in §8.4:
deleting a log event that a rollup cites must fail loudly rather than cascade a rollup row away or
leave a dangling reference.

### 3.8 `postmortems` / `postmortem_chunks`

`postmortems`: `id PK` · `slug Text UNIQUE NOT NULL` (charset per §0.3) · `title Text NOT NULL` ·
`occurred_at NULL` · `services ARRAY(Text) NOT NULL` · `body_md Text NOT NULL` ·
`resolution_md Text NULL` · `content_sha Text NOT NULL`.

`postmortem_chunks`: `id PK` · `postmortem_id FK ON DELETE CASCADE NOT NULL` ·
`ordinal Integer NOT NULL` · `kind Text NOT NULL CHECK (kind IN ('section','resolution'))` ·
`content Text NOT NULL` · `embedding Vector(1024) NOT NULL` · `UNIQUE (postmortem_id, ordinal)`.

**Edit policy** (§14.3): a changed `content_sha` deletes all chunks for that `postmortem_id` and
re-inserts, in one transaction. Ordinals are not preserved across edits, which is exactly why
citations embed `content_sha8`.

### 3.9 `incidents`

`id PK` · `dedup_key Text UNIQUE NOT NULL` · `service_id FK NULL` · `opened_at NOT NULL` ·
`window_start`, `window_end` NOT NULL · `alert_payload JSONB NOT NULL` · `summary_md Text NULL` ·
`summary_json JSONB NULL` · `root_cause Text NULL` ·
`status Text NOT NULL CHECK (status IN ('open','investigating','summarized','failed'))`.

### 3.10 `ingest_watermarks`

`source Text PK` · `last_cursor Text NOT NULL` · `updated_at NOT NULL`.

---

## 4. Migration `0001_initial`

Statement order:

1. `CREATE EXTENSION IF NOT EXISTS vector`.
2. `services` → `commits` → `deployments` (FK to `commits.sha`, so **commits must precede it**) →
   `infra_changes` → `log_events` → `unresolved_events` → `error_rollups` (FK to `log_events`) →
   `postmortems` → `postmortem_chunks` → `incidents` → `ingest_watermarks`.
3. Indexes and non-inline CHECK constraints.

`downgrade()` drops in reverse and **does not drop the extension**. Revision 1 paired
`CREATE EXTENSION IF NOT EXISTS` with an unconditional `DROP EXTENSION`, claiming ownership of an
object it may not have created. Extension lifecycle belongs to provisioning; the compose file
supplies a database dedicated to this project.

`migrations/env.py` imports `pgvector.sqlalchemy` before `target_metadata` is read.

---

## 5. `ingest/identity.py`

### 5.1 `uid` inputs per source

All string inputs are normalized per §13 **before** hashing.

| Source | Call |
| --- | --- |
| Log line | `uid("log", file=rel_path, offset=byte_offset, raw=normalized_raw)` |
| Deployment | `uid("deploy", commit_sha=…, environment=…, started_at=…)` |
| Infra change | `uid("infra", apply_id=…, resource_type=…, resource_name=…, action=…, provider=…)` |
| k8s Event | `uid("k8s-event", event_uid=…, count=…, last_timestamp=…)` |
| k8s Pod status | `uid("k8s-pod", pod_uid=…, container=…, restart_count=…, finished_at=…)` |

The infra form now includes `action` and `provider`; revision 1's citation format omitted them and
was not backed by a uniqueness constraint, so a single apply touching one resource with two actions
produced one identity.

### 5.2 Conflict policy — per kind, never a blanket `DO NOTHING`

Revision 1 specified unconditional `ON CONFLICT DO NOTHING` everywhere. That is wrong for
`deployments`, whose lifecycle the schema explicitly models: a deployment first ingested as
`in_progress` and later re-ingested as `success` has the same uid (§3.3), so the update was
discarded and the row stayed permanently stale.

| Kind | Policy |
| --- | --- |
| `log_events`, `unresolved_events`, `infra_changes` | **Immutable.** On conflict, compare the incoming row's content-bearing columns with the stored ones. Equal → no-op. Different → raise `EvidenceConflict` naming the uid and the differing columns. Silent retention would hide corruption in the identity function itself. |
| `deployments` | **Mutable lifecycle.** `ON CONFLICT (uid) DO UPDATE` on `status` and `finished_at` only, guarded by a monotonic transition rule: `in_progress → {success, failed, rolled_back}` and `success → rolled_back` are permitted; every other transition is rejected. `WHERE` clause encodes it so the guard is in the database, not the loader. |
| `commits` | Keyed on `sha`; content is immutable by construction. `DO NOTHING`. |
| `postmortems` | Keyed on `slug`; §3.8 edit policy. |

### 5.3 Batching and locking

See §8.5 — batch insertion, rollup recomputation, and watermark advancement are **one** transaction.

---

## 6. `ingest/templates.py`

### 6.1 Mechanism

One compiled alternation, one `re.sub` with a callback:

```python
def mask(message: str) -> str:
    escaped = _PLACEHOLDER_RE.sub(lambda m: f"<ESC{m.group(1)}>", message)
    masked = _MASK_RE.sub(lambda m: f"<{m.lastgroup}>", escaped)
    return _WS_RE.sub(" ", masked).strip()

def template_hash(message: str) -> str:
    return hashlib.sha256(mask(message).encode("utf-8")).hexdigest()[:32]
```

`re.sub` does not rescan its own replacement text, so no branch can consume another branch's
placeholder. That is the property sequential `re.sub` calls fail to provide.

**Placeholder escaping is a new pre-pass.** Without it, a log line containing the literal text
`error code <NUM>` hashes identically to `error code 123` — a permanent grouping collision between
unrelated messages. Any literal `<NAME>` matching a placeholder name is rewritten to `<ESCNAME>`
first.

### 6.2 Branches, in order

| Name | Pattern (sketch) |
| --- | --- |
| `TS` | `\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z\|[+-]\d{2}:?\d{2})?` |
| `URL` | `[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s"'<>]+` |
| `EMAIL` | `[\w.+\-]+@[\w\-]+\.[\w.\-]+` |
| `UUID` | `[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}` |
| `HEX` | `0x[0-9a-fA-F]+\b` **or** `\b(?=[0-9a-fA-F]{8,}\b)[0-9a-fA-F]*[a-fA-F][0-9a-fA-F]*\b` |
| `IP` | IPv4 `\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b`; IPv6 only in bracketed `[…]:port` form |
| `HOSTPORT` | `\b[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+:\d+\b` |
| `PATH` | `(?:/[\w.\-]+){2,}/?` |
| `STR` | `"(?:[^"\\]\|\\.)*"` or `'(?:[^'\\]\|\\.)*'` |
| `DUR` | `-?\b\d+(?:\.\d+)?(?:ms\|us\|µs\|ns\|s\|m\|h)\b` |
| `SIZE` | `-?\b\d+(?:\.\d+)?(?:[KMGT]i?B)\b` |
| `NUM` | `-?\b\d+(?:\.\d+)?\b` |

**`HEX` is case-insensitive** in both forms. Revision 1's unprefixed branch accepted only `[0-9a-f]`,
so `deadbeef` masked while `DEADBEEF` and `deadBEEF` did not — the same identifier producing
different templates depending on the emitting library's casing.

**`DUR` and `SIZE` absorb their own sign**, so `-10ms` masks to `<DUR>` rather than `-<DUR>`.

The `a–f` requirement on unprefixed hex stands: without it `10000000` masks as `<HEX>` while
`9999999` masks as `<NUM>`, changing a template on digit count alone.

### 6.3 Documented masking behaviours

The general rule is **leftmost span wins**; branch order decides only among branches matching at the
same start position. Consequences, all frozen by tests:

- Every recognized token **inside a quoted string** is absorbed into `<STR>`, because the quote
  begins earlier. This is not URL-specific.
- `abc0xdead` masks to `abc<HEX>` — the `0x` form is not anchored to a word start.
- Status codes mask to `<NUM>`, so an nginx 200 and 504 with identical text **share a
  `template_hash`**. The distinction lives in `status_class`, which is a rollup PK column and part
  of the anomaly identity (§10). This is by design, not a defect.

**`HOSTPORT`'s actual purpose**, corrected from revision 1: without the branch,
`payments.internal:443` and `payments.internal:8443` both mask to `payments.internal:<NUM>` — they
were never split across ports. What the branch does is collapse *different hostnames* to one token,
so template identity does not fragment across hosts in a fleet.

### 6.4 Scope

Applied to `message` only — never to the raw record or to structured field names, which would make
the hash sensitive to key ordering.

---

## 7. `ingest/normalize.py`

### 7.1 `resolve_service`

Order: `name` → `log_keys` → `k8s_names` → `repo` → `infra_tags`.

**An explicit `name` that does not match is a miss, not a fall-through.** A record asserting its own
service identity must not be silently re-attributed by a weaker signal.

**Ambiguity detection, corrected.** Revision 1 rejected any pair where one service's `infra_tags`
was a subset of another's. With the `{}` default, every empty map is a subset of everything, so a
normal registry failed to load; and the test missed the real hazard, which is *compatible*
non-subset maps — `{team: payments}` and `{environment: prod}` are not subsets of either direction,
yet an event carrying both matches both.

`ServiceRegistry.load()` raises `AmbiguousServiceMapping` when:

- any `log_key`, `k8s_name`, or `repo` appears under more than one service; or
- two services have **non-empty** `infra_tags` maps that are *simultaneously satisfiable* — that is,
  they share no key with conflicting values. Empty maps are ignored for matching entirely, since a
  service with no tag rules participates in no tag-based resolution.

A runtime tag match against two services is also `no_service_match` with reason
`ambiguous_service`, never an arbitrary pick.

**Pod name normalization** is explicit rather than a regex guess: strip a trailing
`-<10-alnum>` ReplicaSet hash followed by `-<5-alnum>` pod suffix (Deployment), or a trailing
`-<ordinal digits>` (StatefulSet). Matching is attempted against `k8s_names` with the full name
first, then each successive stripping, so a service legitimately named `worker-7` is not mangled.

### 7.2 `canonical_level`

Returns `debug|info|warning|error|fatal`, never `None`.

String map (case-insensitive): `trace|debug → debug` · `info|notice|information → info` ·
`warn|warning → warning` · `err|error|severe → error` · `fatal|critical|crit|panic|emerg|alert →
fatal`.

With no level string, derivation from status runs **only after validating `100 ≤ code ≤ 599`**:
≥500 → `error`, 400–499 → `warning`, else `info`. An out-of-range status yields `info` and is
recorded per §7.3. Revision 1 gave status 600 `error` here while §7.3 called it `none` — the two
rules contradicted each other.

### 7.3 `status_class`

`None` → `none`. `100–599` → `f"{code // 100}xx"`. Anything else → `none`, with
`attrs.anomalous_status = <raw value>` retained for inspection.

---

## 8. `aggregate/rollups.py`

### 8.1 Signature and the meaning of `since`

```python
def recompute(session: Session, *, dirty: DirtySet) -> RollupReport
```

Revision 1 took `since: datetime`, which was ambiguous between "minimum event time in this batch",
"wall-clock", and "rebuild horizon" — and could not express deletion at all. The caller now passes
an explicit **dirty set** of `(service_id, minute)` pairs, collected during the mutation that
produced them.

### 8.2 The dirty set must be captured before mutation

Building the affected set from surviving `log_events` cannot see a minute whose rows were all
deleted, so a stale rollup for that minute would live forever. `DirtySet` is therefore the **union**
of:

1. `(service_id, date_trunc('minute', ts))` for every row inserted, updated, or deleted in this
   batch — recorded by the loader as it writes; and
2. every existing `error_rollups` pair for those same `(service_id, minute)` keys.

### 8.3 The transaction

1. `SELECT pg_advisory_xact_lock(hashtext('rollup:' || service_id))` for each affected service, taken
   in ascending `service_id` order to avoid deadlock.
2. `DELETE FROM error_rollups` for every pair in the dirty set.
3. `INSERT … SELECT` recomputing those pairs from all matching `log_events`.

The shared rollup lock is keyed on **service**, not on ingest source. Revision 1's per-source
advisory lock did not serialize two different sources writing the same service-minute: both would
delete zero rows, then both insert the same primary key, and one would fail on a unique violation.

Exemplar selection scores **type-valid, non-null, non-empty** values, not key presence:

```sql
(array_agg(e.id ORDER BY
   ( (e.trace_id IS NOT NULL AND e.trace_id <> '')::int
   + (jsonb_typeof(e.attrs->'stack')     = 'array' )::int
   + (jsonb_typeof(e.attrs->'upstream')  = 'string')::int
   + (jsonb_typeof(e.attrs->'duration_ms') = 'number')::int
   + (jsonb_typeof(e.attrs->'exc_type')  = 'string')::int ) DESC,
   e.id ASC))[1]
```

Revision 1 used `attrs ? 'stack'`, which is true for `"stack": null` — a row with five null evidence
keys outscored a row with one real stack trace, so "richest" selected the emptiest.

### 8.4 Deletion order

`error_rollups.exemplar_log_event_id` is `ON DELETE RESTRICT`, so a caller deleting log events must
delete the citing rollups first. The documented order for any base-row deletion is: capture the dirty
set → delete affected rollups → delete base rows → recompute. A direct `DELETE FROM log_events` that
skips step 2 raises a foreign key violation, which is the intended loud failure.

### 8.5 Atomicity with ingest

Base-row mutation, rollup recomputation, and watermark advancement are **one transaction**. Revision
1 made the first two separate: a crash after the watermark commit but before recompute left the
batch permanently un-rolled-up, because replay skips a batch the watermark already covers.

Note the isolation caveat: under Read Committed, successive statements in one transaction see
different committed snapshots, so transaction boundaries alone do not serialize concurrent writers.
The service-level advisory lock in §8.3 is what provides that.

---

## 9. `mcp_server/citations.py`

```python
def format_log(uid: str) -> str
def format_commit(sha: str) -> str            # rejects anything not 40 hex chars
def format_rollup(service: str, bucket_start: datetime, status_class: str,
                  level: str, template_hash: str) -> str
def format_postmortem(slug: str, content_sha: str, ordinal: int) -> str
def parse(cite: str) -> Citation              # raises MalformedCitation
def is_wellformed(cite: str) -> bool
```

Formatters **reject naive datetimes** and convert aware ones to UTC before rendering (§12.3), so one
instant has exactly one spelling. Every component is validated against the §0.3 charset; a value
containing a delimiter raises rather than producing an ambiguous citation.

---

## 10. `mcp_server/schemas.py`

Pydantic models, final in v0.1 (fields that stay empty until v0.2 are present).

### 10.1 Provenance completeness

**Every aggregate carries `source_cites`; every row-bearing model carries `cite`.** Revision 1 gave
`TemplateAnomaly` a single `cite` although it aggregates a current window and a baseline window
spanning many buckets — and the model did not even carry a `bucket_start` from which to build one.
A multi-minute delta could therefore be "supported" by one arbitrary bucket citation.

```python
class Exemplar(BaseModel):
    cite: str                       # log:<uid>
    sample_message: str
    sample_raw: str
    exc_type: str | None
    top_frame: Frame | None
    upstream: str | None
    duration_ms: float | None
    trace_id: str | None

class TemplateAnomaly(BaseModel):
    template_hash: str
    status_class: str
    level: str                      # identity is all three
    count: int
    baseline_count: int
    delta: int
    source_cites: list[str]         # every rollup row in the current window
    baseline_cites: list[str]       # every rollup row in the baseline window
    occurrence_count: int | None = None      # k8s only, from v0.2
    exemplar: Exemplar

class SeriesPoint(BaseModel):
    bucket_start: datetime
    status_class: str
    count: int
    source_cites: list[str]

class StatusBreakdownEntry(BaseModel):
    status_class: str
    count: int
    source_cites: list[str]
```

### 10.2 Envelopes

```python
class ResolvedWindow(BaseModel):
    start: datetime; end: datetime; snapped: bool     # half-open [start, end)

class IncidentDiff(BaseModel):
    window: ResolvedWindow
    focus: ServiceChanges
    other_services: list[ServiceChanges] = []
    unattributed: list[InfraChange] = []
    counts: DiffCounts

class ErrorTelemetry(BaseModel):
    effective_window: ResolvedWindow
    baseline_window: ResolvedWindow
    baseline_sparse: bool
    series: list[SeriesPoint]
    top_templates: list[TemplateAnomaly]
    status_breakdown: list[StatusBreakdownEntry]
    sample_trace_ids: list[str]

class PostmortemHit(BaseModel):                        # registered from v0.2
    cite: str; resolution_cite: str | None
    slug: str; title: str; occurred_at: datetime | None
    snippet: str; resolution_md: str | None; similarity: float
```

`ServiceChanges` = `{service: str, commits: list[CommitRef], deployments: list[DeploymentRef]}`;
`CommitRef` carries `cite`, `sha`, `message`, `authored_at`, `committed_at`, `files_changed`;
`DeploymentRef` carries `cite`, `environment`, `started_at`, `status`, and its **joined** `commit:
CommitRef`. `InfraChange` carries `cite`, `provider`, `resource_type`, `resource_name`, `action`,
`attribute_diff`, `applied_at`, `service: str | None`. `Frame` = `{file, line, func}`.
`DiffCounts` = `{commits, deployments, infra_changes}` per block.

### 10.3 Determinism

Byte-identical output is an exit criterion, so every list has a total order:

| List | Order |
| --- | --- |
| `top_templates` | `delta DESC, count DESC, template_hash ASC, status_class ASC, level ASC` |
| `series` | `bucket_start ASC, status_class ASC` |
| `status_breakdown` | `status_class ASC` |
| `commits` | `committed_at DESC, sha ASC` |
| `deployments` | `started_at DESC, uid ASC` |
| `other_services` | `service ASC` |
| `unattributed` | `applied_at DESC, uid ASC` |
| `files_changed` | `path ASC` |
| `sample_trace_ids` | first 5 by `(ts ASC, uid ASC)` |
| any `*_cites` | lexicographic ASC |

`status_breakdown` is a **list**, not a dict, so serialization cannot depend on mapping order.

### 10.4 Ranking rules

`top_templates` splits into two pools — **error pool** (`status_class = '5xx'` or
`level in ('error','fatal')`) and **other** — ranked independently, with the error pool emitted
first and each truncated to `top_n`. A noisy 4xx baseline therefore cannot bury a new 504.

`delta = count - baseline_count`, and may be negative; negative-delta entries are retained but sort
last within their pool by the §10.3 key.

The baseline window is `[start - (end - start), start)` — equal length, disjoint, immediately
preceding. `baseline_sparse` is true when the total baseline event count for that service is below
`baseline_sparse_threshold`.

---

## 11. Summary, provenance, and the orchestration seam

### 11.1 Models

```python
class Claim(BaseModel):
    statement: str
    cites: list[str] = Field(min_length=1)

class TimelineEntry(BaseModel):
    at: datetime; what: str
    cites: list[str] = Field(min_length=1)

class IncidentSummary(BaseModel):
    service: str
    root_cause: Claim
    confidence: Literal["high", "medium", "low"]
    timeline: list[TimelineEntry]
    ruled_out: list[Claim] = []
    similar_incidents: list[Claim] = []
    recommended_action: str
```

### 11.2 Provenance validation

```python
CLAIM_BEARING_FIELDS = ("root_cause", "timeline", "ruled_out", "similar_incidents")

def validate_provenance(summary: IncidentSummary, captured: set[str]) -> None
```

Traversal is **explicit over the named fields**, not a generic walk of arbitrary nested models — a
generic walk silently stops covering a field the day someone adds one. A test asserts the tuple
matches the model's claim-bearing fields, so adding a field without listing it fails the suite.

Raises `ProvenanceError` when any cite is malformed, or well-formed but absent from `captured`.

Docstring states the boundary: this proves a citation was returned by a tool during **this run**, so
a fabricated id aborts the run. It does **not** prove the cited row supports the sentence. Semantic
support comes only from scenario evaluation or a human.

### 11.3 `RunContext`

```python
class RunContext:
    def __init__(self, run_id: str, sink: TraceSink) -> None
    @property
    def captured_cites(self) -> set[str]
    def emit(self, event: TraceEvent) -> None
    def capture_tool_result(self, tool: str, args: dict, result: BaseModel) -> None
    def to_json(self) -> dict
```

`run_id` is a `uuid4` hex string supplied by the caller — never generated internally, so a caller can
correlate it with an external identifier. `TraceSink` is a protocol with one method; v0.1 injects
`InMemorySink`, v0.3 injects `PostgresSink` writing to `incidents.summary_json.trace`.

`capture_tool_result` harvests `cite`, `source_cites`, `baseline_cites`, and `resolution_cite` from
the response model by walking Pydantic fields — including nested models and lists. Event payloads are
`model_dump(mode="json")` with sorted keys, so traces are deterministic and JSON-safe.

The validator consults only `captured_cites`. It never queries the database, because "this row
exists" is a weaker property than "this run saw it".

### 11.4 `investigate`

```python
def investigate(request: InvestigationRequest, run_context: RunContext) -> IncidentSummary
```

Synchronous by contract; internal async is an implementation detail. Owns: spawning the MCP
subprocess, running the agent loop, capturing tool results, extracting the summary, validating
provenance.

Failure contract: the MCP subprocess is terminated in a `finally` block on every path.
`ProvenanceError`, `AgentTurnLimitExceeded`, and transport failures all propagate after a
`kind="terminal"` trace event is emitted — the caller decides what a failure means. v0.1's CLI prints
and exits non-zero; v0.3's webhook marks the incident `failed`.

---

## 12. Canonical time semantics

Unretrofittable: a timestamp stored under the wrong rule cannot be distinguished afterwards from one
stored correctly.

1. **Storage.** All `TIMESTAMPTZ`, all UTC. Naive datetimes are rejected at every write boundary.
2. **Local-time attachment.** A parser producing a naive timestamp attaches `services.log_timezone`
   via `zoneinfo`. DST fall-back ambiguity resolves to `fold=0` and sets `attrs.tz_ambiguous = true`;
   a nonexistent spring-forward local time shifts forward by the gap and sets
   `attrs.tz_nonexistent = true`. Neither raises and neither drops the line.
3. **Wire spelling.** ISO-8601, UTC, literal `Z`, microsecond precision in uids
   (`2026-08-19T14:03:22.481000Z`) and second precision in rollup citations
   (`2026-08-19T14:03:00Z`). One instant, one spelling.
4. **Windows are half-open** `[start, end)` everywhere.
5. **Snapping** (`ingest/timewindow.py`): `start` floors to its minute; `end` ceilings to its minute.
   An `end` already on a boundary is unchanged — half-open semantics mean it excludes that minute
   already, so ceiling it would silently widen the window by 60 seconds. `snapped` reports whether
   either endpoint moved.
6. **Baseline** is `[start - (end - start), start)`, computed from the **snapped** window so the two
   are exactly adjacent and non-overlapping.

---

## 13. Canonical text and identity normalization

Every input to `uid()` (§0.2) passes through these first. Changing any of them later changes every
identity in the database, which is why `UID_VERSION` exists.

| Input | Rule |
| --- | --- |
| `source_file` | Path relative to `corpus_dir`, POSIX separators, NFC-normalized |
| `source_offset` | **Byte** offset from file start, not character index |
| `raw` | Decoded UTF-8 with `errors="replace"`, trailing `\r\n` or `\n` stripped, NFC-normalized |
| Timestamps | §12.3 wire spelling |
| Service, slug, environment | NFC, case-sensitive, validated against the §0.3 charset |
| Repo | Lower-cased, `.git` suffix stripped, `owner/name` form |

Case sensitivity is deliberate: silently case-folding service names would make two distinct registry
entries collide at load time rather than being reported.

---

## 14. Evidence correction semantics

1. **Deletion** follows §8.4. There is no supported path that deletes a cited log event without
   first removing the citing rollups.
2. **Promotion.** A row in `unresolved_events` whose service later becomes resolvable is promoted by
   inserting into `log_events` with the **same uid** and deleting the unresolved row, in one
   transaction, adding its `(service_id, minute)` to the dirty set. Identity is preserved, so any
   citation issued earlier still resolves.
3. **Postmortem edits** replace all chunks (§3.8). Old `postmortem:<slug>@<sha8>#<n>` citations
   become unresolvable rather than silently pointing at different text — a resolution failure is a
   detectable event; a silent redefinition is not.
4. **Conflicting immutable payload** raises `EvidenceConflict` (§5.2). It indicates a defect in the
   identity function or a corrupted source, and both warrant stopping.

---

## 15. Tests that gate Part 0

Revision 1's list contained eleven tests that would pass while the defect they targeted was present;
each is replaced below by one that actually discriminates.

**`test_identity.py`** — `test_uid_delimiter_collision_impossible` (the `("a",1,"2|raw")` vs
`("a|1",2,"raw")` pair) · `test_uid_distinguishes_none_from_empty_string` ·
`test_uid_distinguishes_int_from_str` · `test_uid_rejects_unknown_type` ·
`test_uid_stable_across_process_restarts` · `test_uid_changes_with_version_bump`

**`test_templates.py`** — `test_uppercase_hex_masks_like_lowercase` ·
`test_eight_digit_decimal_is_num_not_hex` · `test_literal_placeholder_is_escaped` ·
`test_negative_duration_absorbs_sign` · `test_embedded_0x_masks` ·
`test_quoted_string_absorbs_every_inner_token` · `test_200_and_504_share_template_hash` (documents
the collision `status_class` resolves) · `test_hash_is_128_bits` · `test_hash_stable_in_subprocess`

**`test_normalize.py`** — `test_two_services_with_empty_infra_tags_load_fine` ·
`test_compatible_non_subset_tag_maps_rejected` · `test_explicit_name_miss_does_not_fall_through` ·
`test_statefulset_ordinal_and_replicaset_hash_stripped` ·
`test_service_named_worker_7_not_mangled` · `test_status_600_consistent_between_level_and_class`

**`test_citations.py`** — `test_full_sha_required` · `test_seven_char_prefix_collision_rejected` ·
`test_rollup_cite_differs_on_level_only` · `test_naive_datetime_rejected` ·
`test_non_utc_aware_datetime_normalized` · `test_delimiter_in_component_rejected` ·
`test_postmortem_cite_changes_with_content_sha`

**`test_summary.py`** — `test_fabricated_cite_in_timeline_rejected` ·
`test_fabricated_cite_in_ruled_out_rejected` · `test_fabricated_cite_in_similar_incidents_rejected` ·
`test_claim_bearing_fields_tuple_matches_model` · `test_nested_source_cites_captured`

**`test_migration.py`** — `test_rollup_level_check_rejects_unknown` ·
`test_files_changed_jsonb_check_rejects_bad_omission_reason` ·
`test_every_enumerated_domain_has_a_check` (introspects `pg_constraint`, not a count) ·
`test_downgrade_does_not_drop_extension` · `test_commits_created_before_deployments`

**`test_rollups.py`** — `test_wholly_empty_minute_rollup_deleted` ·
`test_exemplar_ignores_json_null_valued_keys` · `test_exemplar_ignores_empty_trace_id` ·
`test_two_sources_same_service_minute_do_not_deadlock_or_conflict` ·
`test_deleting_cited_exemplar_raises_fk_violation` ·
`test_crash_after_base_rows_before_rollups_leaves_no_gap` (asserts the single-transaction property)

**`test_conflict_policy.py`** — `test_deployment_in_progress_to_success_updates` ·
`test_deployment_success_to_in_progress_rejected` ·
`test_immutable_row_conflicting_payload_raises` · `test_identical_payload_is_noop`

**`test_time.py`** — `test_window_end_on_boundary_not_widened` · `test_half_open_end_excluded` ·
`test_baseline_adjacent_and_disjoint` · `test_dst_ambiguous_local_time_marked` ·
`test_dst_nonexistent_local_time_marked`

**`test_replay_identity.py`** — `test_two_fresh_databases_produce_identical_citations` ·
`test_rollback_consumed_sequence_does_not_change_any_citation`

The last file is the one that would have caught the largest defect in revision 1.

---

## 16. Deliberately **not** frozen

Included here because over-freezing has its own cost, and the review flagged several revision-1
claims as risk reduction dressed up as unretrofittability:

- **Empty v0.2/v0.3 tables.** They are created in migration 1 because it is convenient, not because
  adding a table later is hard. It is ordinary additive migration work.
- **The `vector` extension's creation timing.** Same — solving privileges early is prudence, not
  necessity.
- **Empty response blocks** (`other_services`, `unattributed`). Adding an optional field before its
  first consumer is additive; it invalidates recorded output baselines but corrupts nothing.
- **The exact `RunContext` / `investigate` Python surface.** The *capability* — run-scoped capture of
  every citation — is frozen. The class shape is refactorable until an HTTP caller exists.
- **The HNSW threshold.** A future tuning decision with no place in a foundation contract.
- **Config fields for Slack, GitHub, and agent tuning.** They can arrive with their consumers.

---

## 17. Revision history

**Blocking defects found by `gpt-5.6-sol` (xhigh) in revision 1, all fixed here:**

| # | Defect | Fix |
| --- | --- | --- |
| 1 | Citations embedded sequence ids and 7-char SHAs — unstable across replay, and prefix-collidable | §0: content-derived `uid`, full SHA |
| 2 | `source_uid` used delimiter joining — deterministic collisions, `None`/`""` and `1`/`"1"` aliasing | §0.2 canonical JSON, versioned |
| 3 | Blanket `ON CONFLICT DO NOTHING` left deployments permanently stale at `in_progress` | §5.2 per-kind conflict policy with a monotonic guard |
| 4 | Ingest, watermark, and rollup recompute were not one transaction; per-source locks did not serialize same-minute writers | §8.3, §8.5: one transaction, service-level advisory lock |
| 5 | Dirty set built from surviving rows could not see a wholly deleted minute; exemplar FK action unspecified | §8.2 pre-mutation dirty set ∪ existing rollup pairs; §8.4 explicit `RESTRICT` and deletion order |
| 6 | Exemplar scored `attrs ? 'key'`, true for JSON `null` — "richest" picked the emptiest | §8.3 type-valid non-null scoring |
| 7 | `TemplateAnomaly` had one `cite` for a multi-bucket aggregate and no `bucket_start`; `Exemplar` had no citation | §10.1 `source_cites` / `baseline_cites` / exemplar `cite` |
| 8 | Subset-based tag ambiguity check failed on the `{}` default and missed compatible non-subset maps | §7.1 simultaneous-satisfiability check, empty maps ignored |
| 9 | Unprefixed `HEX` was lowercase-only; literal `<NUM>` in a message aliased a masked number | §6.2 case-insensitive hex; §6.1 placeholder escaping |
| 10 | `error_rollups.level` had no CHECK; `hunks_omitted` was application-only | §3.7 CHECK; §3.2 JSONPath CHECK |
| 11 | `fastmcp` and the agent SDK were absent from the dependency list | §1 |

**Accepted secondary fixes:** 128-bit `template_hash`; `Literal` types for `embedding_dim` and
`rollup_bucket_seconds`; no `DROP EXTENSION` on downgrade; consistent out-of-range status handling;
signed `DUR`/`SIZE`; corrected `HOSTPORT` rationale; the general leftmost-span rule documented rather
than two examples.

**Deviation from the plan text, honestly characterized:** the plan specifies "sentinel masking with a
restore pass"; §6.1 implements a single `re.sub` with a callback plus a placeholder-escaping
pre-pass. Revision 1 called this "strictly stronger", which was wrong — it is *different*
deterministic semantics. A single pass selects by leftmost span across all branches, whereas
sequential rules apply each branch globally in turn; the two produce different templates for
overlapping candidates. What the single pass does guarantee is that no branch can consume another's
placeholder, which is the property the plan's sentinels existed to provide. Literal-placeholder
aliasing is a separate problem that neither approach solves, which is why §6.1 adds the escape pass.
