---
okf_version: 0.1+TW1
title: Design Specification — ollama-delegate
purpose: As-shipped technical documentation — architecture, module and data
  design, configuration surface, interfaces — traced to URS_FS. DOC-03.
doc_type: design
status: wip
doc_revision: 2
created: 2026-08-30
last_updated: 2026-08-30T00:00:00-04:00
source_author: Tim Wortley + Claude
tags:
  - design
  - publication
  - specification
---

# Design Specification — `ollama-delegate`

**`DOC-03`. Satisfies `UR-07`, `UR-08`, `UR-10`.**

**Every `FS-` and `UR-` identifier cited here is defined in
[URS_FS](URS_FS.md),
which publishes alongside this document as `DOC-07` — ruled 2026-08-30.** The
trace matrix in §15 is only useful if its references resolve, so the register it
points at is published rather than assumed. In the repository both sit in
`docs/`, and the published copy links to `URS_FS.md`.

**This document describes the system as shipped.** It is the technical detail
needed to build, configure or verify an installation — architecture, module and
data design, the configuration surface, and the interfaces with their contracts.

**It is not a record of design decisions.** Where a mechanism is unobvious, this
document says what the mechanism *is* and what property it holds; it does not
argue for it or list what was rejected. Reasoning of that kind was recorded
during development and is not part of the published set.

> **Register caution.** This document set is GMP-*shaped*, not GMP-*compliant*.
> There is no QA approval, no change control and no validated environment. The
> shape is used because it finds defects — a specification that traces to
> identifiers makes an unimplemented function visible as a hole rather than as an
> absence nobody notices. **It found three defects while this document was being
> written**, none of which any passing check disagreed with; see §14.

---

## 1. Scope

**Four shipped components.**

| Component | File | State |
|---|---|---|
| **Delegation server** | `ollama_server.py` | Shipped — nine tools, always registered |
| **Retrieval indexer** | `vault_index.py` | Shipped — `build`, `search`, `status` |
| **Retrieval MCP tools** | `index_tools.py` | Shipped — four tools, **registered only when an index directory is configured**. See §11 |
| **Delegation skill** | `skills/local-inference-delegation/SKILL.md` | Shipped |

**The deliverable is the server and the skill together (`FS-30`).** The server
provides capability; the skill provides the judgement about when to use it.

**The tool surface has two states.** Without an index directory configured the
server registers nine tools and does not load the retrieval module; with one
configured it registers thirteen. §11.2 specifies the condition.

## 2. Architecture

### 2.1 Deployment topology

```
   ┌──────────────────────────┐
   │  MCP client              │        the client spawns the server as a
   │  (Claude Desktop, Code,  │        direct child process: no shell, no
   │   any stdio MCP host)    │        venv activation, no working directory
   └───────────┬──────────────┘
               │  stdio — JSON-RPC framing on stdout/stdin
   ┌───────────▼──────────────┐
   │  ollama_server.py        │        one process, one session, no state
   │    Guard.check()         │        that outlives it
   │    ContextCache          │
   │    _request()            │
   └───────────┬──────────────┘
               │  HTTP/1.1 over loopback, stdlib urllib
   ┌───────────▼──────────────┐
   │  Ollama  :11434          │        holds the weights; may itself proxy
   └──────────────────────────┘        `:cloud`-tagged models off the host

   ┌──────────────────────────┐
   │  vault_index.py          │        separate process, run by a human or a
   │  (CLI, not MCP)          │        script. Shares no state with the server
   └───────────┬──────────────┘
               │  HTTP to the same Ollama; reads the corpus directly
               ▼
   $OLLAMA_MCP_INDEX_DIR/<name>.index.json
               ▲
               │  read-only, one file, no corpus access
   ┌───────────┴──────────────┐
   │  index_tools.py          │        in the SERVER process, registered only
   │  (index_* MCP tools)     │        when the index directory is configured
   └──────────────────────────┘
```

**The index file is the seam.** The CLI writes it and is the only thing that
opens the corpus; the MCP tools read it and open nothing else. That is what makes
*"no MCP tool reads your filesystem"* a property of the shape rather than a
claim needing a guard behind it.

**Three properties follow from this shape and are relied on throughout.**

**The server is a subprocess, not a service.** It has no listening socket, no
port and no authentication, because it has no network surface to authenticate.
Its only client is the process that spawned it. This is why every capability
decision is made from the *process environment* (`FS-16`) — that is the one
channel the spawning client controls and a tool call cannot.

**Its lifetime is one client session.** Caches need no invalidation strategy
(§5.6), and nothing persists between runs.

**`stdout` is the MCP transport.** Any stray write to it corrupts JSON-RPC
framing and the client's connection dies. All logging is therefore attached to
`stderr` with `propagate = False`, and `--selftest` asserts both.

### 2.2 Trust boundaries

Two boundaries cross this system, and neither is authenticated by it.

| Boundary | Crossed by | Controlled by |
|---|---|---|
| Client → server | Tool calls, which are agent-generated | `Guard.check()` (§5.3) and the process environment |
| Server → Ollama | Prompt content | Nothing. Ollama has no auth and is trusted as a local peer |

**Model output is untrusted content.** Whatever returns from a delegated call is
data, never instruction. The server does not interpret it, and callers must not
either. Full analysis is in `SECURITY.md` and
the Threat Model;
this document describes mechanism only.

### 2.3 Constituents — `UR-07`

**The stack, named. A reader should be able to say what this is made of without
opening a file.**

| Layer                                             | Choice                                                                                                                       |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Language                                          | Python **3.10 or newer**, checked at runtime by both entry points. Not enforced by packaging — there is no `requires-python`. Tested on 3.10, 3.12 and 3.14; **not on 3.11**. See §14.4 |
| Runtime dependency                                | **The MCP SDK.** One direct dependency; `pip install mcp` transitively installs roughly 27 packages |
| HTTP                                              | Standard library `urllib.request`. No `requests`, no `httpx`, no async client                                                |
| Transport                                         | stdio JSON-RPC, via the MCP SDK's server class                                                                               |
| Serialisation                                     | Standard library `json`                                                                                                      |
| Inference backend                                 | Ollama's HTTP API. There is no second backend and no plugin seam — a product non-goal (`UR-08`, §14.3)                       |
| Index storage                                     | **A flat JSON file.** No database, no vector store, no server                                                                |
| Vector arithmetic                                 | Hand-written cosine similarity in `math`. No NumPy                                                                           |
| Test framework                                    | **None.** `--selftest` is in-process assertions inside the shipped module (§12)                                              |
| Web framework, ORM, container, config file format | **None of these exist in this system**                                                                                       |

**The absences are load-bearing, not oversights.** A user who can run Python and
`pip install mcp` has installed everything. `vault_index.py` runs on a bare
interpreter with nothing installed at all.

### 2.4 Dual SDK tolerance

`build_server()` imports `FastMCP` and falls back to `MCPServer`, which the SDK
renamed at v2.0. Both names are attempted at startup; whichever resolves is
used.

> **Verification limit.** Only the `FastMCP` path has been exercised. The
> fallback branch is written and untested — see `VERIFICATION.md`.

---

## 3. Module structure — `ollama_server.py`

**One file, ~1,730 lines, no package.** Sections in source order:

| Region | Contents |
|---|---|
| Logging | `stderr` handler, level from the environment, `propagate = False` |
| Config | `_env_flag()`, `Config` dataclass, `Config.from_env()` |
| Cloud detection | `_CLOUD_TAGS`, `_STUB_SIZE_CEILING`, `_is_cloud_model()`, `_location()`, `_normalise_base_url()` |
| **Enforcement** | `Refused`, `Guard`, `_base_name()` — the whole permission surface |
| **Transport** | `_request()`, `_ok()`, `_refusal()` — the only HTTP call site |
| Context budgeting | `_estimate_tokens()`, `ContextCache`, `_check_context()` |
| Result shaping | `_throughput()`, `_apply_completion_verdict()`, `_human_size()` |
| **Tool registration** | `register()` — the nine delegation tools as closures over `config`, `guard`, `ctx`. `build_server()` calls `index_tools.register()` for four more **only when an index directory is configured**, and imports that module inside the branch, so an unconfigured clone never loads it |
| Verification | `selftest()`, `probe()` |
| Entry point | `build_server()`, `main()` |

**Tools are nested functions inside `register()`, closing over one `Guard` and
one `ContextCache`.** That is what makes "a single enforcement point" structural
rather than a convention: there is one guard instance, and every tool that needs
it has it in scope.

---

## 4. Module structure — `vault_index.py`

**One file, ~1,290 lines, standard library only — it does not import `mcp`.**

| Region | Contents |
|---|---|
| Constants | `INDEX_FORMAT`, `BUILDER_VERSION`, `INDEX_SUFFIX`, `NAME_RE`, `CHARS_PER_TOKEN`, `CHUNK_OVERLAP`, `TARGET_CHUNK_CHARS`, `PREFIXES` |
| Errors and output | `IndexerError`, `_note()` |
| Transport | `_post()`, `context_limit()`, `model_installed()`, `embed_batch()` |
| Chunking | `paragraphs()`, `_origin_of_tail()`, `chunk_text()` |
| Index identity | `validate_name()`, `resolve_index_dir()`, `relative_key()`, `index_path()`, `compute_generation()`, `chunk_id()`, `iter_chunks()`, `load_index()` |
| Build | `build()` |
| Search | `cosine()`, `RUBRICS`, `_score_batch()`, `rerank()`, `embed_query()`, `score_all()`, `_cite()`, `search()` |
| Staleness | `status()` |
| Entry point | `main()` — argparse |

> **This module is imported into the MCP server process by `index_tools.py`, and
> two script-shaped habits had to go before it could be.** Library functions
> raise `IndexerError` rather than calling `sys.exit`, because an exit inside a
> tool call terminates the server and every other tool with it; and they write
> diagnostics to **stderr** via `_note()`, because stdout carries the MCP stdio
> framing and a stray print corrupts the stream rather than failing loudly.
> Only the command functions — `build()`, `search()`, `status()` — write stdout,
> and the server calls none of them.

**The two modules share no code.** Constants that appear in both —
`CHARS_PER_TOKEN = 3.2`, the `min(context_length, num_ctx)` rule — are
duplicated, not imported. `vault_index.py` therefore runs with nothing
installed, at the cost of two definitions that must be kept in step by hand.

---

## 5. Software design — server

### 5.1 Configuration

All configuration is read from the process environment into a frozen-at-startup
`Config` dataclass (`FS-16`). **No configuration file exists, and no tool call
can change a setting.** A capability the environment did not grant cannot be
acquired at runtime.

`OLLAMA_HOST` passes through `_normalise_base_url()`, which:

- accepts a bare `host:port` and prepends `http://`, because Ollama's own docs
  and shell exports use that form;
- **refuses any scheme other than `http`/`https`**, rather than handing a
  `file://` URL to `urlopen`;
- refuses a URL with no hostname;
- returns `scheme://netloc`, discarding any path or trailing slash.

A refused URL is fatal at startup: `main()` returns exit code 2 and the server
does not register.

### 5.2 Transport — one call site

**`_request()` is the only function in the module that performs HTTP** (`FS-17`).
Thirteen call sites reach it; nothing else opens a connection. It returns parsed
JSON or raises `Refused`, and it distinguishes six failure modes that would
otherwise collapse into one generic error:

| Condition | Verdict |
|---|---|
| HTTP 404 | `model_not_found` |
| Any other HTTP error status | `ollama_error` |
| Connection refused, DNS failure, no route | `ollama_unreachable` |
| No response inside the timeout | `timeout` |
| **HTTP 200 with an empty body** | `empty_response` |
| HTTP 200 with a non-JSON body | `bad_response` |
| **HTTP 200 with an `error` key in the JSON** | `ollama_error` |

**The last two rows are the ones that matter.** Ollama reports some failures as a
200 carrying an `error` key rather than an error status, and an evicted model can
produce an empty 200. Without these checks both arrive as a successful call that
returned nothing.

> ### ⚠️ `generate` calls `/api/generate`, and `FS-05` requires `/api/chat`
>
> **As shipped, the `generate` tool posts to `/api/generate`**
> (`ollama_server.py:897`). `chat` posts to `/api/chat` (line 994).
>
> `FS-05` reads: *"`generate` performs single-prompt completion via `/api/chat`,
> never `/api/generate`."* **The code does not meet it.**
>
> `/api/generate` bypasses the model's chat template. Template-sensitive
> families then emit reserved vocabulary — Gemma emits `<unused50>` — instead of
> text. `vault_index.py:365` carries the fix and a comment naming the failure;
> `ollama_server.py` does not.
>
> **This is stated because the DS documents as-shipped.** It is recorded as a
> defect in `VERIFICATION.md`, not corrected here. **It has not been reproduced
> against a live host** — the prediction is that `generate` with a Gemma-family
> model returns reserved tokens, and that is the falsifier.

### 5.3 Enforcement — one function

**`Guard.check(op, model)` is the entire permission surface** (`FS-10`). Every
tool that names a model or changes host state calls it first, and it is the only
place a call is refused on policy.

| Check | Refuses when | Verdict |
|---|---|---|
| Delete gate | `op == "delete"` and `OLLAMA_MCP_ALLOW_DELETE` is not set | `not_permitted` |
| Pull gate | `op == "pull"` and `OLLAMA_MCP_ALLOW_PULL` is not set | `not_permitted` |
| Empty model | A model name is required and blank | `invalid_request` |
| Allowlist | `OLLAMA_MCP_MODELS` is set and the name is not on it | `not_permitted` |

**Both write operations are refused by default** (`FS-08`, `FS-09`). Neither can
be enabled by a tool call or a config file; only the environment of the server
process grants them, which means the person who wrote the MCP client config.

**Allowlist comparison strips an explicit `:latest`** via `_base_name()`, so an
entry written `qwen2.5` matches a call for `qwen2.5:latest`. Ollama treats them
as the same model and the allowlist must not fail closed against its own default
tag.

**Every refusal carries a cause and a remedy** (`FS-17`). The remedy names the
exact variable and states that it is deliberately not settable from a tool call.

> **Reachability limit.** A well-behaved client never reaches the refusal path:
> a diligent agent reads `server_info` (§6), sees `pull_model: disabled` and does
> not make the call. **`server_info` is the load-bearing surface; the verdict
> strings are a backstop.** Recorded because it changes what testing the guard
> through a client can demonstrate.

### 5.4 Cloud labelling — `FS-12`

Ollama can register models that execute on Ollama's hosted infrastructure. Their
local manifest is a pointer stub of a few hundred bytes rather than weights, so
in a plain listing they are indistinguishable from local models: same name
shape, same call, same response.

**They are labelled, not refused.** Every model listing and every inference
response carries a `location` field with one of three values:

| Value | Condition |
|---|---|
| `cloud` | The name's tag is in `_CLOUD_TAGS` (`{"cloud"}`) |
| `cloud?` | No cloud tag, but the reported size is under `_STUB_SIZE_CEILING` (1 MiB) |
| `local` | Neither |

**Detection is on the tag; size only corroborates.** Size is not available
everywhere a name is — a caller passes a name, not a listing — and a future stub
could be padded. `cloud?` exists so that a disagreement between the two signals
is reported as a disagreement rather than resolved into a confident answer.

`list_models` additionally sets a `hosted_models` list and a `note` when any
non-local model is present.

### 5.5 Context budgeting — `FS-13`

**Ollama does not error when input exceeds a model's context. It truncates and
returns a normal-looking response.** For generation that yields an answer to a
half-seen question; for embeddings, a structurally valid vector representing the
first fragment of a document. Both read as success, and nothing later disagrees.

The limit spans two orders of magnitude across models on one host, so it cannot
be assumed and is read per model.

**Estimation.** `_estimate_tokens()` returns `len(text) / 3.2 + 1`. English
averages roughly 4 characters per token; code, JSON, non-Latin scripts and long
identifiers run denser. **3.2 is deliberately pessimistic** — an estimate used as
a safety limit must err toward refusing a call that would have fit. Ollama
exposes no tokenizer endpoint, so an exact count is not available to this
server, and every response carrying the estimate says it is one.

**Headroom.** `_OUTPUT_HEADROOM = 0.15` of the window is reserved for the
response and chat-template overhead. Usable input is `int(limit * 0.85)`.

**Decision table** for `_check_context()`:

| Limit known? | Estimate fits? | `allow_truncation` | Result |
|---|---|---|---|
| Yes | Yes | — | Proceed |
| Yes | No | `False` | Refuse — `context_exceeded` |
| Yes | No | `True` | Proceed |
| **No** | — | `False` | Refuse — `context_unknown` |
| **No** | — | `True` | Proceed |

**An unknown limit is reported, never defaulted.** Substituting a plausible
number for an unread one produces a wrong answer wearing good provenance.

`chat` budgets **the entire message history**, not the newest message (`FS-06`),
because the whole history is re-sent every turn. A conversation that fits on
turn 3 can overflow on turn 9 with no change in caller behaviour.

`embed` applies a stricter variant: **no headroom** (embeddings generate no
tokens, so the whole window is input), and the check is **per input**, naming
every oversized index rather than reporting a count.

### 5.6 The context cache

`ContextCache.limit_for(model)` fetches `/api/show` once per model per process
and returns `{"limit", "source", "disagreement"}`.

Two sources can disagree: the architecture's `*.context_length` in `model_info`,
and `num_ctx` parsed out of the `parameters` text block. **The lower value is
used, and the disagreement is reported alongside it** rather than silently
resolved. `limit` is `None` when it genuinely could not be read, and `source`
then records why.

**No invalidation.** Models do not change size mid-session and the process lives
for one session.

### 5.7 Completion verdicts — `FS-14`

**Three states, not two.** The distinction is between *finished*, *known
truncated*, and *not known either way*.

| `done_reason` | Text | Verdict |
|---|---|---|
| any | empty or whitespace | `empty_generation` |
| `"length"` | any | `truncated`, plus `truncated: true` |
| **missing or `null`** | any | `completion_unverified` |
| `"stop"` | non-empty | `ok` |
| anything else | non-empty | `completion_unverified` |

**Absent metadata is not evidence of completion.** A trailing run of whitespace
sets `trailing_whitespace: true` as corroboration, but the verdict does not
depend on that heuristic — missing metadata is sufficient on its own.

`generate` and `chat` share this function, so they cannot drift apart.

### 5.8 Throughput reporting — `FS-15`

`_throughput()` extracts, when Ollama reports them: `eval_count`,
`tokens_per_second` (from `eval_count / eval_duration`), `prompt_eval_count`,
and `cold_load_seconds` — the last only when `load_duration` exceeds 0.1 s,
which distinguishes a genuine model load from a warm call.

**Load time is reported separately from generation rate** because a cold load
dominates a short generation and would otherwise make a fast model look slow.

---

## 6. Interfaces — MCP tool surface

Traces `FS-01` … `FS-09`. Every tool returns a dictionary; **no exception
reaches the client**, and there is no success channel other than `verdict`.

<!-- BEGIN GENERATED: tools -->

**13 tools, registered in a single `register()`.** Every one returns a dict carrying a `verdict`; there is no other success channel and no exception reaches the client.

#### `chat`

Multi-turn conversation, preserving history.

```python
chat(model: str, messages: list[dict[str, str]], temperature: float | None = None, max_tokens: int | None = None, json_mode: bool = False, allow_truncation: bool = False) -> dict[str, Any]
```

**Returns on success:** `model`, `location`, `role`, `content`, `duration_s`, `done_reason`, throughput fields, when Ollama reports them

**Verdicts reachable:** `bad_response`, `completion_unverified`, `context_exceeded`, `context_unknown`, `empty_generation`, `empty_response`, `invalid_request`, `model_not_found`, `not_permitted`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`, `truncated`

#### `delete_model`

Delete a model from the local Ollama server. Destructive and irreversible -- the model must be re-downloaded to be used again.

```python
delete_model(model: str) -> dict[str, Any]
```

**Returns on success:** `model`, `deleted`

**Verdicts reachable:** `bad_response`, `empty_response`, `invalid_request`, `model_not_found`, `not_permitted`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`

#### `embed`

Generate embedding vectors.

```python
embed(model: str, texts: list[str], allow_truncation: bool = False) -> dict[str, Any]
```

**Returns on success:** `model`, `location`, `count`, `dimensions`, `embeddings`

**Verdicts reachable:** `bad_response`, `context_exceeded`, `empty_response`, `incomplete`, `invalid_request`, `model_not_found`, `not_permitted`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`

#### `generate`

Single-turn completion. The delegation workhorse.

```python
generate(model: str, prompt: str, system: str = '', temperature: float | None = None, max_tokens: int | None = None, json_mode: bool = False, allow_truncation: bool = False) -> dict[str, Any]
```

**Returns on success:** `model`, `location`, `response`, `duration_s`, `done_reason`, throughput fields, when Ollama reports them

**Verdicts reachable:** `bad_response`, `completion_unverified`, `context_exceeded`, `context_unknown`, `empty_generation`, `empty_response`, `invalid_request`, `model_not_found`, `not_permitted`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`, `truncated`

#### `list_models`

List every model installed on the local Ollama server.

```python
list_models(include_details: bool = False) -> dict[str, Any]
```

**Returns on success:** `count`, `local_count`, `models`, `host`

**Verdicts reachable:** `bad_response`, `empty_response`, `model_not_found`, `no_models_installed`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`

#### `list_running`

List models currently loaded in memory, with their VRAM footprint and expiry. Useful for judging whether a call will be fast (already warm) or slow (cold load).

```python
list_running() -> dict[str, Any]
```

**Returns on success:** `count`, `running`, `note`

**Verdicts reachable:** `bad_response`, `empty_response`, `model_not_found`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`

#### `pull_model`

Download a model to the local Ollama server.

```python
pull_model(model: str) -> dict[str, Any]
```

**Returns on success:** `model`, `status`, `note`

**Verdicts reachable:** `bad_response`, `empty_response`, `incomplete`, `invalid_request`, `model_not_found`, `not_permitted`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`

#### `server_info`

Report what this MCP server is configured to do and what it refuses.

```python
server_info() -> dict[str, Any]
```

**Returns on success:** `host`, `write_operations`, `model_allowlist`, `timeout_s`, `pull_timeout_s`, `retrieval_tools`, `note`

**Verdicts reachable:** `ok`

#### `show_model`

Show a model's capabilities, context length and parameters.

```python
show_model(model: str) -> dict[str, Any]
```

**Returns on success:** `model`, `location`, `capabilities`, `context_length`, `details`, `parameters`, `template_present`

**Verdicts reachable:** `bad_response`, `empty_response`, `invalid_request`, `model_not_found`, `not_permitted`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`

#### `index_explain`

Where does a passage you KNOW exists actually rank for this query?

```python
index_explain(index: str, query: str, text: str) -> dict[str, Any]
```

**Returns on success:** `index`, `query`, `hits`, `diagnosis`, `explanation`

**Verdicts reachable:** `internal_error`, `invalid_request`, `ok`

#### `index_get`

Return the text of named chunks, from the index itself.

```python
index_get(index: str, ids: list[str]) -> dict[str, Any]
```

**Returns on success:** `index`, `generation`, `chunks`

**Verdicts reachable:** `internal_error`, `invalid_request`, `ok`, `stale_id`

#### `index_list`

List the semantic indexes this server can read.

```python
index_list() -> dict[str, Any]
```

**Returns on success:** `index_dir`, `indexes`, `unreadable`, `note`

**Verdicts reachable:** `internal_error`, `not_configured`, `ok`

#### `index_search`

Semantic search over an index. Returns CITATIONS, not content.

```python
index_search(index: str, query: str, k: int = 10, rerank: bool = True, rerank_model: str = DEFAULT_RERANK_MODEL, pool: int = RERANK_POOL) -> dict[str, Any]
```

**Returns on success:** `index`, `generation`, `built_at`, `model`, `location`, `chunks_searched`, `spread`, `reranked_by`, `results`, `note`

**Verdicts reachable:** `internal_error`, `invalid_request`, `ok`

<!-- END GENERATED: tools -->

---

## 7. Verdict vocabulary — `FS-11`, `FS-17`

**A caller branches on `verdict`.** It never branches on the presence or absence
of a field, and it never treats a missing error as success.

`ok` is the only success value. Every refusal additionally carries `error` (the
cause) and, where one exists, `remedy` (what to do about it).

<!-- BEGIN GENERATED: verdicts -->

**16 verdicts, one vocabulary.** A caller branches on `verdict` and never on the presence or absence of a field. `ok` is the only success value; every other value names a specific failure, and *“the check did not happen”* is spelled differently from *“the check passed”*.

| Verdict | Raised in |
|---|---|
| `bad_response` | `_request` |
| `completion_unverified` | `_apply_completion_verdict` |
| `context_exceeded` | `_check_context`, `embed` |
| `context_unknown` | `_check_context` |
| `empty_generation` | `_apply_completion_verdict` |
| `empty_response` | `_request` |
| `incomplete` | `embed`, `pull_model` |
| `invalid_request` | `Guard.check`, `chat`, `embed`, `generate` |
| `model_not_found` | `_request` |
| `no_models_installed` | `list_models` |
| `not_permitted` | `Guard.check` |
| `ok` | `_ok`, `chat`, `delete_model`, `embed`, `generate`, `list_models`, `list_running`, `pull_model`, `server_info`, `show_model` |
| `ollama_error` | `_request` |
| `ollama_unreachable` | `_request` |
| `timeout` | `_request` |
| `truncated` | `_apply_completion_verdict` |

<!-- END GENERATED: verdicts -->

---

## 8. Configuration surface — `FS-16`

<!-- BEGIN GENERATED: config -->

**The two components have separate configuration surfaces and they are not interchangeable.** `vault_index.py` is a CLI and takes its settings as flags. The one variable they share is `OLLAMA_MCP_INDEX_DIR`, which names the directory the CLI writes indexes to and the `index_*` tools read them from — deliberately the same value, because an index the tools cannot see is the failure it exists to prevent. No other `OLLAMA_MCP_*` variable reaches the indexer.

**10 environment reads across 2 files.** Every one is listed; this table is generated from the call sites, not from any docstring.

#### `ollama_server.py`

| Variable | Default | Type | Read in |
|---|---|---|---|
| `OLLAMA_HOST` | `'http://localhost:11434'` | string, via `_normalise_base_url()` | `from_env` |
| `OLLAMA_MCP_ALLOW_DELETE` | `False` | boolean flag | `from_env` |
| `OLLAMA_MCP_ALLOW_PULL` | `False` | boolean flag | `from_env` |
| `OLLAMA_MCP_INDEX_DIR` | `''` | string | `from_env` |
| `OLLAMA_MCP_LOGLEVEL` | `'INFO'` | string | `<module>` |
| `OLLAMA_MCP_MODELS` | `''` | string | `from_env` |
| `OLLAMA_MCP_PULL_TIMEOUT` | `'3600'` | float | `from_env` |
| `OLLAMA_MCP_TIMEOUT` | `'300'` | float | `from_env` |

#### `vault_index.py`

| Variable | Default | Type | Read in |
|---|---|---|---|
| `OLLAMA_HOST` | `'http://localhost:11434'` | string | `<module>` |
| `OLLAMA_MCP_INDEX_DIR` | `''` | string | `resolve_index_dir` |

<!-- END GENERATED: config -->

**`OLLAMA_MCP_LOGLEVEL` is read at module scope and is reported nowhere.** It is
absent from the module docstring and from `server_info()`. It is listed above
because this document describes what the code reads, not what the code says
about itself. **Whether it is a supported setting is unresolved** — it is either
a documented option that belongs in the manual and in `server_info()`, or an
internal one that should be marked unsupported. It is listed here either way.

---

## 9. Software design — retrieval indexer

**One rule governs the split: anything that touches the corpus is a CLI
operation, and MCP reads the index.** The indexer walks folders, reads files and
writes vectors. None of that is reachable from a tool call.

### 9.1 Why a script rather than tool calls

An embedding is 768 floats. Passing thousands of them through an agent's context
to do arithmetic on them exhausts the context window almost immediately. **The
vectors are produced, stored and compared on the machine that holds them**; the
agent decides what to index and reads the results, which are small.

### 9.2 Chunking

`chunk_text()` packs whole paragraphs into chunks under a character ceiling, and
returns each as `{text, heading, lines}` rather than a bare string.

**The bookkeeping is not decoration — a citation cannot be recovered afterwards.**
Once a chunk is a string, the source line range is gone: searching the file for
the text back-references the wrong copy wherever a corpus repeats itself, and
fails outright for a chunk that begins mid-word after an overlap. `paragraphs()`
therefore carries a 1-based inclusive line range and the nearest preceding ATX
heading through the packing, and `_origin_of_tail()` converts the overlap tail's
character offset back to the line it really starts on.

Two cases that produced wrong-looking citations and are handled explicitly:

- **A note with no blank line anywhere is one block.** Citing the block for every
  slice of it gave `lines: [1, 204]` on a real 204-line file — a citation
  pointing at the whole document. Slices are character slices of a string whose
  newlines are all present, so each cites its own range by newline count
- **`#` inside a fenced code block is not a heading.** Treating one as a section
  title attaches a confident wrong heading to every chunk after it

**The packing arithmetic itself is unchanged from the tuned version**, and
`--selftest` carries the 2026-08-26 chunker verbatim as a golden reference,
asserting the emitted text is identical. Retrieval quality — prefixes, chunk size,
overlap — was all measured against that behaviour.

| Parameter | Value | Effect |
|---|---|---|
| `TARGET_CHUNK_CHARS` | 1500 | Target size — roughly one section |
| Hard cap | `context_limit × 3.2` | Never exceeds what the model can read |
| Effective | `min(--chunk-chars, hard cap)` | Retrieval quality first, capped by capability |
| `CHUNK_OVERLAP` | 200 chars | Carried between adjacent chunks |

**Paragraph boundaries, not fixed offsets.** A chunk starting mid-sentence
embeds to a muddled vector that retrieves badly for everything. A paragraph over
the ceiling is split on its own, and every slice after the first is trimmed
forward to the next space.

**The overlap tail is snapped to a word boundary** for the same reason.

**Chunk size is a retrieval-quality parameter, not a capacity one.** An embedding
is approximately an average of everything in its chunk, so a chunk spanning many
topics produces a vector that matches all of them weakly and none strongly.

### 9.3 Task prefixes

`nomic-embed-text` is trained asymmetrically and requires different prefixes for
documents and queries. **Omitting them is a silent quality regression** —
everything runs, scores look plausible, retrieval is simply worse.

| Model | Document prefix | Query prefix |
|---|---|---|
| `nomic-embed-text`, `:latest` | `search_document: ` | `search_query: ` |
| `mxbai-embed-large` | *(none)* | `Represent this sentence for searching relevant passages: ` |
| Anything else | *(none)* | *(none)* |

**The prefixes used at build time are stored in the index**, and `search` takes
the query prefix from the index rather than from the current code. An index
built before prefixes existed carries none, and its queries get none —
consistent with its own documents, which is what matters.

### 9.4 Embedding and the index write

`build()` reads the model's real context limit before chunking and **exits
rather than guessing** if it cannot be determined. `embed_batch()` asserts one
vector per input and aborts rather than writing a partial index.

The whole index is written in one `write_text()` with `newline="\n"`.

### 9.5 Incremental rebuild — `FS-20`

Each file entry stores `digest`: the first 16 hex characters of the SHA-256 of
the file's text. On a rebuild, an entry whose digest matches is reused
unchanged. **`--rebuild` ignores any existing index.** A prior index built with a
different model is discarded rather than mixed.

**There is no index-level generation hash.** Change detection is per file only.

### 9.6 Search

The query is embedded with **the model recorded in the index**, not the current
default. Vectors from different models are not comparable — the arithmetic still
works and returns confident nonsense.

`cosine()` divides out magnitude, which is what makes a two-line note comparable
to a two-page one. Every chunk is scored; there is no approximate index, no
clustering and no shortlist.

### 9.7 Ranking diagnostics — `FS-26`

Two diagnostics, answering different questions, and the normal output cannot
distinguish the failures they detect.

**Spread.** `nomic` vectors are not centred, so absolute scores sit high even for
unrelated text. **Only the spread carries information.** A top-*N* spread under
0.06 emits a warning that the ranking is weak; a rerank pool spread under 0.05
warns that the pool boundary is close to arbitrary and suggests widening it.

**`--explain SUBSTRING`.** Literal substring match across every chunk, reporting
the rank of each hit. **This separates a recall failure from a ranking failure** —
a chunk absent from the top results may be missing from the index or present and
out-ranked, and those need opposite fixes. When the best match falls outside the
rerank pool, the output says so explicitly: no reranker can recover what recall
missed.

### 9.8 Reranking — `FS-27`

Cosine similarity measures topical overlap, not answerhood. Reranking asks a
local chat model to score candidates on whether they *answer* the question.
Embeddings do recall; the model does precision. Nothing leaves the machine.

| Property | Value |
|---|---|
| Transport | `/api/chat` — **never** `/api/generate`, which skips the chat template |
| Batch size | 5 by default, and **this is a correctness parameter** |
| Rubric | Absolute 0/1/2, not a ranking, so scores from separate batches compare |
| Temperature | 0 |
| `num_predict` | 2000 |
| Response format | **Line-oriented `index=score`. Not JSON** |
| Sort | Model score first, cosine as tiebreak, stable |
| Unscored candidates | Keep their embedding position — unscored is not rejected |

**Batch size is a correctness parameter.** Twenty candidates in one call sent
roughly 8,300 prompt tokens to a small model and produced well-formed scores that
did not correspond to the passages. The format was perfect and the judgement was
noise.

**Line format, not JSON.** There are no braces to balance, and a truncated reply
stays valid up to the cut. Forcing a JSON grammar broke two models outright.
Indices outside the batch are dropped rather than remapped.

**Two rubrics ship.** `plain` is the default. `strict` was the obvious
improvement and **measured worse**; it is retained rather than deleted so the
result is not rediscovered.

**Three conditions are reported rather than absorbed:** a model using its own
scale instead of 0–2; more than half of all candidates sharing the top score,
which means ties fall through to embedding order and the ranking within that
group is unreranked; and every candidate scoring 0, which is the model stating
that nothing in the pool answers the question.

### 9.9 Command-line surface

<!-- BEGIN GENERATED: cli -->

**3 subcommands.** `build`, `search`, `status`. There are no others.

#### `vault_index.py build`

Build or refresh an index.

| Argument | Default | Notes |
|---|---|---|
| `--dir` | `None` | directory holding <name>.index.json files (default: $OLLAMA_MCP_INDEX_DIR) |
| `folder` | *required, positional* | — |
| `--name` | *required* | index name: lowercase letters, digits, _ and - only. Written as <name>.index.json in the index directory |
| `--describe` | `''` | what this corpus is for, in a sentence. With more than one index this is the field a caller chooses between them on; an index without one is a filename |
| `--model` | `'nomic-embed-text:latest'` — `DEFAULT_MODEL` | — |
| `--host` | `$OLLAMA_HOST`, else `'http://localhost:11434'` — `DEFAULT_HOST` | — |
| `--batch` | `16` | parsed as `int` |
| `--chunk-chars` | `1500` — `TARGET_CHUNK_CHARS` | parsed as `int`; target chunk size in characters (default 1500); capped by the model's real context window |
| `--rebuild` | `False` | ignore any existing index and re-embed everything |

#### `vault_index.py search`

Query an index.

| Argument | Default | Notes |
|---|---|---|
| `--dir` | `None` | directory holding <name>.index.json files (default: $OLLAMA_MCP_INDEX_DIR) |
| `index` | *required, positional* | index name, not a path |
| `query` | *required, positional* | — |
| `--top` | `5` | parsed as `int` |
| `--explain` | `None` | report where chunks containing this literal text rank, to separate a recall failure from a ranking failure |
| `--host` | `$OLLAMA_HOST`, else `'http://localhost:11434'` — `DEFAULT_HOST` | — |
| `--rerank` | `None` | bare flag uses `'gpt-oss:20b'`; rerank results with a local chat model, e.g. gemma3:4b. Prefer a model WITHOUT the `thinking` capability -- a reasoning wrapper breaks the JSON response |
| `--rerank-rubric` | `'plain'` | choices: `plain`, `strict`; scoring rubric wording (default 'plain'). `strict` was measured WORSE -- see RUBRICS. |
| `--rerank-batch` | `5` | parsed as `int`; passages scored per model call (default 5). Large batches give well-formed but unreliable scores. |
| `--rerank-pool` | `20` | parsed as `int`; how many embedding candidates to rerank (default 20) |

#### `vault_index.py status`

Re-hash the corpus and name what changed.

| Argument | Default | Notes |
|---|---|---|
| `--dir` | `None` | directory holding <name>.index.json files (default: $OLLAMA_MCP_INDEX_DIR) |
| `index` | *required, positional* | index name, not a path |

<!-- END GENERATED: cli -->

### 9.10 Staleness — `FS-19`, `UR-16`

**`status` re-hashes the corpus and names what changed.** It reads `source_root`
from the index header, walks it, digests every file the same way `build()` does,
and compares against the per-file digests already stored for incremental rebuild
(§9.5). Output is added / changed / removed by name, capped at twenty per class,
followed by the exact rebuild command.

**Three properties, each deliberate:**

- **It is a CLI command, not a tool.** Re-hashing opens every source file, and §11's
  rule is that anything touching the corpus stays on the CLI. A security property
  that holds without a caveat is worth more than one needing a footnote
- **Exit 1 on drift, so it can gate a script**, and **exit 2 when `source_root` is
  not a directory on this host** — an index built elsewhere cannot be checked from
  here, and that is a different answer from *"unchanged"*. Collapsing the two
  would report a clean corpus for a machine this one has never seen
- **It is the only staleness answer offered.** `index_list` and `index_search`
  report `built_at` and name this command; neither estimates. *"Probably
  current"* derived from a timestamp is a guess presented as a fact, about
  precisely the thing a caller is trusting

Paths are normalised through `relative_key()` — relative to the corpus root,
POSIX separators — by both `build()` and `status()`. Storing the platform's
separator made an index a fact about the machine that built it, and left every
file reading as removed-and-added when checked from another.

---

## 10. Data design — the index file

<!-- BEGIN GENERATED: schema -->

**One JSON file, written whole.** There is no database, no sidecar and no index-level content hash: change detection is per file, on the `digest` field below. The file is not committed — `.gitignore` excludes `/*.json`, because a built index contains the corpus.

```
{
  "format":
  "name":
  "description":  // args.describe or ''
  "source_root":  // str(root)
  "built_at":  // datetime.now().astimezone().isoformat(timespec='seconds')
  "builder_version":
  "generation":
  "model":  // args.model
  "doc_prefix":
  "query_prefix":
  "chunk_chars":
  "dimensions":  // len(entries[0]['chunks'][0]['vector']) if entries else 0
  "file_count":  // len(entries)
  "chunk_count":
  "files": [
    {
      "file":
      "digest":
      "chunks": [
        {
          "text":  // c['text']
          "heading":  // c['heading']
          "lines":  // c['lines']
          "vector":
        }, ...
      ]
    }, ...
  ]
}
```

| Level | Fields |
|---|---|
| root | `format`, `name`, `description`, `source_root`, `built_at`, `builder_version`, `generation`, `model`, `doc_prefix`, `query_prefix`, `chunk_chars`, `dimensions`, `file_count`, `chunk_count`, `files` |
| file entry | `file`, `digest`, `chunks` |
| chunk | `text`, `heading`, `lines`, `vector` |

<!-- END GENERATED: schema -->

**Field semantics.**

| Field | Meaning |
|---|---|
| `format` | Index format version. **A reader that does not recognise it refuses the file** rather than interpreting the fields it happens to know |
| `name` | The index's own name, matching its filename. `[a-z0-9_-]+` |
| `description` | What the corpus is for. **With several indexes this is the field a caller chooses between them on**; an index without one is a filename |
| `source_root` | Absolute path the corpus was built from, on the machine that built it. Reported by `index_list`, so an index is identifiable by what it indexed (`UR-12`), and used by `status`, which reports `2` when it is not reachable here |
| `built_at` | ISO 8601 with offset. **Reported, never used to estimate staleness** |
| `builder_version` | Which builder wrote the file, so a reader need not infer it from which fields are present |
| `generation` | Short hash scoping every chunk id. See below |
| `model` | The embedding model used. A query must use this model or results are meaningless |
| `doc_prefix`, `query_prefix` | The task prefixes in force at build time. `search` uses `query_prefix` from here, never from current code |
| `chunk_chars` | The effective ceiling used — `min(--chunk-chars, model limit × 3.2)` |
| `dimensions` | Vector length, taken from the first chunk. `0` for an empty index |
| `file_count`, `chunk_count` | Counts as written, so `index_list` reports rather than recomputes |
| `files[].file` | Path relative to the indexed root, **POSIX separators**, never absolute |
| `files[].digest` | SHA-256 of the file text, first 16 hex characters. Change detection only |
| `files[].chunks[].text` | The chunk verbatim, **without** the document prefix |
| `files[].chunks[].heading` | Nearest preceding ATX heading **at the line the chunk starts on** — not the section it mostly covers |
| `files[].chunks[].lines` | 1-based inclusive source range. Carried through chunking, because it cannot be recovered afterwards |
| `files[].chunks[].vector` | The embedding, **with** the document prefix applied |

**Chunk ids are derived, not stored.** `<generation>:<ordinal>`, where ordinal is
the chunk's position in the flattened file order. A stored id can disagree with
the header it claims to be scoped to; a derived one cannot.

**`generation` covers what moves a chunk** — format, model, both prefixes, chunk
size, and every `(file, digest)` pair, NUL-separated so `("ab","c")` and
`("a","bc")` cannot collide. It **excludes** `name`, `description`,
`source_root` and `built_at`: none of them moves a chunk, and a generation that
changed when nothing did would refuse still-valid ids and teach a caller to
ignore the refusal.

**The text stored and the text embedded differ by the prefix.** Storing the
prefixed form would corrupt every snippet shown to a reader.

**The index contains the corpus in full.** `.gitignore` excludes `/*.json` at the
repository root **and `*.index.json` anywhere in the tree** — the root-only rule
would miss an index directory configured inside it. A built index is a copy of
whatever was indexed, and committing one publishes it.

---

## 11. Retrieval MCP tools — `index_tools.py`

**`FS-21` … `FS-25` are implemented.** `index_list`, `index_search`, `index_get`
and `index_explain` are registered from a separate module, in the server process,
**only when an index directory is configured**. Their signatures and returns are
generated into §6 from source.

> **This section was a named hole until 2026-08-30.** It read *"`index_tools.py`
> does not exist"*, and two of the constraints it listed did not survive
> construction — see *Where this design changed* below. The hole was accurate and
> is kept in the history rather than quietly overwritten.

### 11.1 The one rule the surface follows

> **Anything that touches the corpus is a CLI operation. MCP reads the index.**

No exceptions, which is what makes it enforceable rather than aspirational. The
agent-facing surface opens exactly one file — the index — and `index_get`
hydrates chunk text **from inside it**, so no tool opens a corpus file even to
return corpus content. Build and `status` are CLI commands for this reason and
no other.

**This is why the design has no path allowlist**, reversing an earlier
requirement in this section. A guard that checks a parameter can be bypassed by a
new tool that forgets to call it; **a parameter that does not exist cannot be.**
No `index_*` tool accepts a filesystem path.

### 11.2 Opt-in, and it does three jobs

`OLLAMA_MCP_INDEX_DIR` names a directory of `<name>.index.json` files.

| Job | Consequence |
|---|---|
| **Opt-in** | Unset, the tools are not registered *and the module is never imported* — the import sits inside the branch in `build_server()`. A default clone exposes exactly the nine-tool bridge |
| **Allowlist** | Only indexes in that directory are reachable |
| **Location** | Resolves *"where does the index live"*, which the CLI left relative to a working directory an MCP client chooses |

`name` is validated `[a-z0-9_-]+` — no separators, no dots. **Traversal is not a
rejected path but an invalid name**, so there is no expression of it for a filter
to miss.

`server_info` reports the state either way, including when the tools are absent:
an agent that cannot see them has no way to distinguish *"not configured on this
host"* from *"not supported by this server"*, and those have different remedies.

### 11.3 Citations, not content — the load-bearing decision

**Returning content forces `k` to be a context-budget decision taken before
anything is known about relevance.** Ask for ten and you pay for ten, including
the seven that were noise. Citations move that decision *after* the evidence:
search cheaply, read paths, headings and scores, hydrate the two that matter.

A citation also composes, being a path and a line range — hydration can come from
`index_get`, from another MCP server, or from a human opening the file. Content
in a return value composes with nothing.

**Measured 2026-08-30 against a 443-chunk documentation corpus**, on a question
whose answer sits in one 25-chunk note:

| Path | ~Tokens |
|---|---|
| `index_search`, ten citations | ~640 |
| `index_get` on the two that scored 2 | ~890 |
| **Cycle total** | **~1,530** |
| Opening the single note that does contain the answer | ~6,800 |

The `FS-21`…`FS-25` falsifier — *does a search-then-hydrate cycle cost more than
reading the files an experienced guess would pick* — **passes at roughly 7×**.
Note that figure and not the three-orders-of-magnitude one, which compares
against reading the whole corpus and answers a different question.

### 11.4 Three refusals, each preventing a confident wrong answer

| Refusal | Why a warning would not do |
|---|---|
| **An index of an older format is refused**, naming the rebuild command | It carries no headings, line ranges or generation, so every result would be a path and nothing else. Reporting six fields as `unknown` is a truthful label on an unusable answer |
| **Chunk ids are generation-scoped** (`<generation>:<ordinal>`) and a mismatch is refused | The same ordinal in a rebuilt index is a different passage under the same path. Hydrating it is wrong with no error anywhere. The id is **derived, not stored** — a stored id can disagree with the header it claims to be scoped to |
| **A missing embedding model is refused, never substituted** | Cosine over vectors from two models returns a complete, well-ordered, meaningless ranking, and nothing downstream can detect it |

**Bounded returns.** `k` is 1–25 and the response is byte-capped; when the cap
truncates a hydration it says which ids were left out. Returning fewer than asked
without a word is the failure — naming them is not.

### 11.5 Reporting the rerank honestly

`reranked_by` carries `judged`, `of_pool`, the **score distribution**, per-batch
failures, and a `weak_discrimination` note when one score dominates.

**The distribution is the load-bearing field, not the count.** `scored 20 of 20`
measures participation; only the spread measures discrimination. A 4B model once
returned twenty well-formed scores in 1.5 seconds with nine tied at the top, so
the cosine tiebreak ordered the visible results and the output looked like a
complete success.

### 11.6 Known limitation — reranking is bounded by the client's timeout

**Two measured results contradict each other and no single default satisfies
both.**

| Measured | |
|---|---|
| **Pool 20 can miss the answer** — one query's answering passage sat at cosine rank 32 of 403, outside the pool, so no reranker ever saw it. Pool 40 put it at rank 1 unchanged | 2026-08-26 |
| **Pool 40 exceeds an MCP client's request timeout**, with the model already warm | 2026-08-30 |

**The CLI has no such limit; the tool surface does.** The ceiling is a property of
the transport, not of the retrieval design, and it depends on a timeout belonging
to whichever client the operator uses — which this project neither controls nor
can detect.

`index_search` defaults to `pool=20`, because **a default that always times out
is worse than one that is occasionally short.** The shortfall is made visible
rather than hidden: the distribution shows when nothing was judged a direct
answer, a partial-only result sets `pool_may_be_short` naming both the wider
retry and the CLI, `index_explain` separates a recall failure from a ranking one,
and `pool` is a caller-supplied argument bounded at 80.

**That is honest, and it is not a fix.** The remedy — a two-stage or asynchronous
rerank — is tracked as post-1.0 work, with its own falsifier: if default-pool
callers do materially worse than the CLI across a real question set, stating the
limitation was not enough.

### 11.7 Where this design changed under construction

Recorded because both were stated as fixed requirements in the previous revision
of this section:

- **The path allowlist is withdrawn.** Moving index building to the CLI removed
  the need for it by removing the capability — no tool takes a folder. §11.1
- **Ids are derived rather than stored.** The interface design showed an `id`
  field in the returned object; storing one creates a fact that can disagree
  with its own header. §11.4

---

## 12. Self-verification — `FS-28`, `FS-29`

**`--selftest` ships inside `ollama_server.py`.** There is no `tests/` directory
and no test framework: the suite runs on a bare interpreter with nothing
installed, which is the same floor as the rest of the system. It exits non-zero
on any failure.

Coverage: URL normalisation and scheme refusal · delete and pull gating **against
a default `Config()`**, not an explicitly-disabled one · allowlist matching
including the `:latest` equivalence · context budgeting including the
unknown-limit and disagreement paths · `ContextCache` parsing against a stubbed
transport · `embed`'s per-input refusal exercised through the **real registered
tool** · all seven completion-verdict states · cloud labelling including
stub-sized and no-size cases · stdio hygiene · unreachable-host verdict
distinction · index naming, generation stability, chunk citation and heading
attribution · a **golden reference copy of the 2026-08-26 chunker**, asserting
emitted chunk text is unchanged · `build` and `status` end to end against a
stubbed embedder · all four `index_*` tools against a fixture index.

### 12.1 Mutation checking — `mutation_check.py`

**A passing suite says the assertions ran. It does not say any of them could have
failed.** `mutation_check.py` breaks the code on purpose — currently **18
mutants** — and fails if `--selftest` still passes. It ships in the repository
and belongs in CI beside `--selftest`.

**It found three assertions that could not fail**, none visible by reading the
suite: a POSIX-path check asserted through a built index, which passes trivially
on Linux because `str(PurePath)` already yields forward slashes there — while the
defect it was written for can only occur on Windows; two line-range checks that
tested **containment**, so widening a citation to the whole file still satisfied
them; and a reranker fixture whose stub agreed with embedding order, so *"the
reranked order is used"* passed equally well when that order was discarded.

**The baseline guard is the load-bearing part.** An early version reported 12/12
caught while the selftest was already red — every mutant "caught" by a failure
already present. **A perfect score from a broken measurement**, which is this
project's own signature trap occurring inside the instrument built to look for
it. It now proves the baseline green before running, and reports a mutant whose
anchor no longer matches rather than silently skipping it: a mutant that did not
apply is not a mutant that passed.

Earlier rounds, on the delegation half alone, surfaced two real defects and one
test passing for the wrong reason — a `file:///etc/passwd` case refused by the
*hostname* check rather than the scheme check, so it would have passed with the
scheme check deleted. Every scheme case now carries a hostname.

**`--probe`** performs a live check against the configured host: reachability,
model count, and a per-model listing with `location` and size. It reports the
hosted-model count explicitly and exits non-zero when Ollama is unreachable or
no models are installed.

---

## 13. The skill — `FS-30`

`skills/local-inference-delegation/SKILL.md` ships in the repository and is
**half the deliverable**, not documentation about it.

**It carries routing judgement**, which the server deliberately does not: when a
job is worth delegating, which model class suits which task, what budgets a
reasoning model needs, and the failure modes that make a working model look
broken.

**Its write path is inverted relative to normal source control.** A saved skill
changes only through the host application's save mechanism; editing the file on
disk does not propagate. The repository copy is canonical: **edit the repository
file, commit, then save with overwrite.** Editing the saved copy first bypasses
version control entirely.

**It is not required to use the server.** The tools work without it. It is
required for them to be used well, and the failure it exists to prevent is
non-use — an agent grinding through bulk work inline, never reaching for
delegation at all.

---

## 14. Known deviations

**Four were found by tracing this document against `URS_FS` and against the
source. Two are now closed.** A design specification records what shipped, so a
resolved deviation is marked rather than removed — a hole that silently
disappears leaves no evidence the trace matrix ever worked.

| # | Deviation | Kind | State |
|---|---|---|---|
| 14.1 | `FS-19` — `status` specified, not implemented | Unbuilt function | ✅ **Closed 2026-08-30** |
| 14.2 | `FS-05` — `generate` uses `/api/generate`, not `/api/chat` | **Code contradicts the FS** | Open |
| 14.3 | `UR-08` — no second-backend seam | Stated non-goal | Open, by decision |
| 14.4 | No declared Python version floor | Undeclared constraint | ✅ **Closed 2026-08-30** — 3.10 declared and enforced; untested span named |

### 14.1 `FS-19` — `status`: ✅ CLOSED 2026-08-30

**This deviation is resolved and the entry is kept rather than deleted**, because
the trace matrix is a defect-finding instrument and a hole that silently
disappears teaches nobody anything.

It read: *"`vault_index.py` registers two subcommands, `build` and `search`.
There is no `status`."* That was true when written and was found by this
document's own trace matrix, not by any test. **`vault_index.py status <name>`
now exists** — it re-hashes the corpus against the per-file digests stored at
build time and names what changed, added and removed. `FS-19` and `UR-16` are
met; see §9.10.

**It stays a CLI command, and that is a design constraint rather than an
implementation convenience:** re-hashing opens every source file, and the rule
in §11 is that anything touching the corpus is a CLI operation.

### 14.2 `FS-05` — `generate` uses `/api/generate`

See §5.2. **The requirement says `/api/chat`, never `/api/generate`; the code
uses `/api/generate`.** Predicted symptom: template-sensitive model families
return reserved vocabulary instead of text. **Not reproduced against a live
host.**

### 14.3 `UR-08` — no second-backend seam

`UR-08` asks that a forker be able to add an alternative inference backend
without rewriting the tool layer. **There is no such seam.** `_request()` is the
transport and every tool reaches it directly, so a second backend means editing
each one.

**Counted rather than asserted:** `_request()` has **13 call sites** — 12 inside
`register()`, and one in `ContextCache.limit_for()`.

### 14.4 Python floor — ✅ DECLARED 2026-08-30, and partly tested

**The supported minimum is Python 3.10.** Declared by the owner as a support
commitment; enforced at runtime by both entry points, which check
`sys.version_info` before doing anything and exit with a named message. The
check can only speak if the file parses, so the codebase uses no 3.10-only
syntax — no `match`/`case`, no PEP 604 unions outside annotations, and
`from __future__ import annotations` in every module.

**It is not enforced by packaging.** There is no `pyproject.toml` and no
`requires-python`, so `pip` will not refuse an old interpreter; the runtime
check is the whole mechanism. `requirements.txt` states the floor in a comment
and says so.

> **A declared floor and a tested floor are different claims, and this section
> keeps them apart on purpose.** The previous revision reported *no floor at
> all*, which was accurate then. What follows is what has actually been run.

| Version | Environment | Evidence | Grade |
|---|---|---|---|
| **3.10.12** | `ENV-4` | `--selftest` and `mutation_check.py` throughout the retrieval work, **current code**, 2026-08-30 | **Unit and static.** `ENV-4` reaches no Ollama endpoint, so no live model and no MCP client |
| **3.11** | — | None | **Untested, by decision** |
| **3.12.3** | `ENV-2` | Install → `--selftest` → `--probe` → full tool exercise through a desktop MCP client, 2026-08-29 | End to end, **on code that predates the retrieval tools** |
| **3.14.4** | `ENV-1` | `--selftest`, current code, 2026-08-30 | Selftest only |

**Three things this table says that a single "tested floor" figure would hide.**
The most thorough run — 3.12.3, end to end against a live host — is also the
**stalest**, made before `index_tools.py` existed. The interpreter the system is
developed on daily is 3.14.4, nowhere near the floor. And the floor's own
evidence comes from an environment that **cannot reach a model at all**, so it
establishes that the code runs, not that it works.

**Grade, environment and recency matter as much as the version number**, which
is why all four are in the table and why no row is summarised into a single
claim.

**Policy, owner-set:** 3.10 and 3.11 will not be deliberately tested. There is
no CI matrix. **`UR-31` is therefore met in the form it actually asks for** — a
floor is stated, and the span for which no evidence exists is named rather than
implied. `PER-1` gets a sentence instead of a `SyntaxError`.

**Residual risk, accepted:** 3.11 is unexercised, and 3.10's evidence is
selftest-only. The failure this leaves open is a version-specific runtime
difference that the selftest's stubbed transport cannot reach. Closing it needs
a continuous-integration matrix, which does not exist.

---

## 15. Traceability

**Inline above, and consolidated here.** The matrix is the instrument: an
unimplemented function shows as a gap in a column rather than as an absence
nobody notices.

| FS | Function | Documented in | State |
|---|---|---|---|
| FS-01 | `list_models` | §6 | ✅ |
| FS-02 | `show_model` | §6 | ✅ |
| FS-03 | `list_running` | §6 | ✅ |
| FS-04 | `server_info` | §6 | ✅ |
| FS-05 | `generate` | §6, **§5.2** | ⚠️ **Deviation — §14.2** |
| FS-06 | `chat`, history budgeting | §6, §5.5 | ✅ |
| FS-07 | `embed`, count assertion | §6, §5.5 | ✅ |
| FS-08 | `pull_model`, gated | §6, §5.3 | ✅ |
| FS-09 | `delete_model`, gated | §6, §5.3 | ✅ |
| FS-10 | `Guard.check()` single point | §5.3 | ✅ |
| FS-11 | Named verdict on every response | §7 | ✅ |
| FS-12 | `location` on listings and inference | §5.4 | ✅ |
| FS-13 | Context refused before the call | §5.5 | ✅ |
| FS-14 | Truncation reported, not returned | §5.7 | ✅ |
| FS-15 | Throughput and timing | §5.8 | ✅ |
| FS-16 | Environment-only configuration | §5.1, §8 | ✅ |
| FS-17 | Cause and remedy on every refusal | §5.2, §5.3, §7 | ✅ |
| FS-18 | `build` — chunk, embed, header | §9.2 – §9.4, §10 | ✅ |
| FS-19 | `status` — staleness | §9.10, §14.1 | ✅ |
| FS-20 | Incremental reuse by digest | §9.5 | ✅ |
| FS-21 | `index_*` opt-in registration | §11.2, §6 | ✅ |
| FS-22 | `index_list` | §11, §6 | ✅ |
| FS-23 | `index_search` — citations | §11.3, §6 | ✅ |
| FS-24 | `index_get` — hydration | §11.4, §6 | ✅ |
| FS-25 | `index_explain` — MCP | §11, §6 | ✅ |
| FS-26 | Spread reporting | §9.7 | ✅ |
| FS-27 | Rerank batches ≤ 5 | §9.8 | ✅ |
| FS-28 | `--selftest` | §12 | ✅ |
| FS-29 | `--probe` | §12 | ✅ |
| FS-30 | The skill ships with the server | §13 | ✅ |

**29 documented as shipped · 1 shipped with a deviation · 0 not built.**

Was *"24 shipped · 1 deviation · 5 not built"* until 2026-08-30. **`FS-19` and
`FS-21` … `FS-25` all closed on the same day**, which is the largest single
movement this matrix has recorded and the reason it exists.

`FS-25` deserves a note: **`--explain` exists both as a CLI flag** (§9.7) **and
as an MCP tool** (§11), and they are not the same thing. The CLI flag reports
ranks and leaves the reading to a human; the tool returns a `diagnosis` naming
which failure it is — no failure, ranking, or recall — because that distinction
needs opposite fixes and ordinary output cannot carry it.

> **`FS-25` was the trap in this row.** While the tool did not exist, the CLI
> flag made the requirement *look* satisfied to anyone checking by feature name
> rather than by interface. A matrix that matched on the word `explain` would
> have reported it green for weeks.

| UR | Satisfied by this document | State |
|---|---|---|
| `UR-07` — a reader can name the constituents | §2.3 | ✅ |
| `UR-08` — second-backend seam | §14.3 | ❌ Non-goal, stated |
| `UR-10` — module and data design | §3, §4, §5, §9, §10 | ✅ |

**`UR-31` is touched but not satisfied by this document.** It concerns a stated
Python floor; §14.4 records that no floor is stated in the repository at all.
That is a finding for `VERIFICATION.md` and for CI, not something a DS can
close.

---

## 16. Related

[URS_FS](URS_FS.md) — **the register this document traces to** ·
Threat Model ·
`README.md` · `SECURITY.md` · `MANUAL.md` · `VERIFICATION.md`

