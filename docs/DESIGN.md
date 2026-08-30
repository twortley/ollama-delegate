---
okf_version: 0.1+TW1
title: Design Specification — ollama-delegate
purpose: As-shipped technical documentation — architecture, module and data
  design, configuration surface, interfaces — traced to URS_FS. DOC-03.
doc_type: design
status: wip
doc_revision: "1"
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

**Two shipped components and one that is specified but not built.**

| Component | File | State |
|---|---|---|
| **Delegation server** | `ollama_server.py` | Shipped |
| **Retrieval indexer** | `vault_index.py` | Shipped |
| **Delegation skill** | `skills/local-inference-delegation/SKILL.md` | Shipped |
| **Retrieval MCP tools** | `index_tools.py` | **Not built.** See §11 |

**The deliverable is the server and the skill together.** The server provides
capability; the skill provides the judgement about when to use it. Nine tools
with no skill is a set of buttons nobody presses at the right moment, so the
skill is documented here as product, not as an accessory (`FS-30`).

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
               │  HTTP to the same Ollama
               ▼
        index.json on disk
```

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
[the Threat Model](THREAT_MODEL.md);
this document describes mechanism only.

### 2.3 Constituents — `UR-07`

**The stack, named. A reader should be able to say what this is made of without
opening a file.**

| Layer | Choice |
|---|---|
| Language | Python. **No minimum version is declared anywhere in this repository** — see §14.4 |
| Runtime dependency | **The MCP SDK, and nothing else.** `pip install mcp` transitively installs roughly 27 packages; the direct dependency is one |
| HTTP | Standard library `urllib.request`. No `requests`, no `httpx`, no async client |
| Transport | stdio JSON-RPC, via the MCP SDK's server class |
| Serialisation | Standard library `json` |
| Inference backend | Ollama's HTTP API. There is no second backend and no plugin seam — a product non-goal (`UR-08`, §14.3) |
| Index storage | **A flat JSON file.** No database, no vector store, no server |
| Vector arithmetic | Hand-written cosine similarity in `math`. No NumPy |
| Test framework | **None.** `--selftest` is in-process assertions inside the shipped module (§12) |
| Web framework, ORM, container, config file format | **None of these exist in this system** |

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
| **Tool registration** | `register()` — all nine tools as closures over `config`, `guard`, `ctx` |
| Verification | `selftest()`, `probe()` |
| Entry point | `build_server()`, `main()` |

**Tools are nested functions inside `register()`, closing over one `Guard` and
one `ContextCache`.** That is what makes "a single enforcement point" structural
rather than a convention: there is one guard instance, and every tool that needs
it has it in scope.

---

## 4. Module structure — `vault_index.py`

**One file, ~710 lines, standard library only — it does not import `mcp`.**

| Region | Contents |
|---|---|
| Constants | `CHARS_PER_TOKEN`, `CHUNK_OVERLAP`, `TARGET_CHUNK_CHARS`, `PREFIXES` |
| Transport | `_post()`, `context_limit()`, `embed_batch()` |
| Chunking | `chunk_text()` |
| Build | `build()` |
| Search | `cosine()`, `RUBRICS`, `_score_batch()`, `rerank()`, `search()` |
| Entry point | `main()` — argparse |

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

**9 tools, registered in a single `register()`.** Every one returns a dict carrying a `verdict`; there is no other success channel and no exception reaches the client.

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

**Returns on success:** `host`, `write_operations`, `model_allowlist`, `timeout_s`, `pull_timeout_s`, `note`

**Verdicts reachable:** `ok`

#### `show_model`

Show a model's capabilities, context length and parameters.

```python
show_model(model: str) -> dict[str, Any]
```

**Returns on success:** `model`, `location`, `capabilities`, `context_length`, `details`, `parameters`, `template_present`

**Verdicts reachable:** `bad_response`, `empty_response`, `invalid_request`, `model_not_found`, `not_permitted`, `ok`, `ollama_error`, `ollama_unreachable`, `timeout`

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

**The two components have separate configuration surfaces and they are not interchangeable.** `vault_index.py` is a CLI and takes its settings as flags; it reads exactly one environment variable, and none of the `OLLAMA_MCP_*` variables reach it.

**8 environment reads across 2 files.** Every one is listed; this table is generated from the call sites, not from any docstring.

#### `ollama_server.py`

| Variable | Default | Type | Read in |
|---|---|---|---|
| `OLLAMA_HOST` | `'http://localhost:11434'` | string, via `_normalise_base_url()` | `from_env` |
| `OLLAMA_MCP_ALLOW_DELETE` | `False` | boolean flag | `from_env` |
| `OLLAMA_MCP_ALLOW_PULL` | `False` | boolean flag | `from_env` |
| `OLLAMA_MCP_LOGLEVEL` | `'INFO'` | string | `<module>` |
| `OLLAMA_MCP_MODELS` | `''` | string | `from_env` |
| `OLLAMA_MCP_PULL_TIMEOUT` | `'3600'` | float | `from_env` |
| `OLLAMA_MCP_TIMEOUT` | `'300'` | float | `from_env` |

#### `vault_index.py`

| Variable | Default | Type | Read in |
|---|---|---|---|
| `OLLAMA_HOST` | `'http://localhost:11434'` | string | `<module>` |

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

`chunk_text()` packs whole paragraphs into chunks under a character ceiling.

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

**2 subcommands.** `build`, `search`. There are no others.

#### `vault_index.py build`

Build or refresh an index.

| Argument | Default | Notes |
|---|---|---|
| `folder` | *required, positional* | — |
| `--out` | `'index.json'` | — |
| `--model` | `'nomic-embed-text:latest'` — `DEFAULT_MODEL` | — |
| `--host` | `$OLLAMA_HOST`, else `'http://localhost:11434'` — `DEFAULT_HOST` | — |
| `--batch` | `16` | parsed as `int` |
| `--chunk-chars` | `1500` — `TARGET_CHUNK_CHARS` | parsed as `int`; target chunk size in characters (default 1500); capped by the model's real context window |
| `--rebuild` | `False` | ignore any existing index and re-embed everything |

#### `vault_index.py search`

Query an index.

| Argument | Default | Notes |
|---|---|---|
| `index` | *required, positional* | — |
| `query` | *required, positional* | — |
| `--top` | `5` | parsed as `int` |
| `--explain` | `None` | report where chunks containing this literal text rank, to separate a recall failure from a ranking failure |
| `--host` | `$OLLAMA_HOST`, else `'http://localhost:11434'` — `DEFAULT_HOST` | — |
| `--rerank` | `None` | bare flag uses `'gpt-oss:20b'`; rerank results with a local chat model, e.g. gemma3:4b. Prefer a model WITHOUT the `thinking` capability -- a reasoning wrapper breaks the JSON response |
| `--rerank-rubric` | `'plain'` | choices: `plain`, `strict`; scoring rubric wording (default 'plain'). `strict` was measured WORSE -- see RUBRICS. |
| `--rerank-batch` | `5` | parsed as `int`; passages scored per model call (default 5). Large batches give well-formed but unreliable scores. |
| `--rerank-pool` | `20` | parsed as `int`; how many embedding candidates to rerank (default 20) |

<!-- END GENERATED: cli -->

> **`FS-19` specifies a `status` subcommand. It does not exist.** See §14.1.

---

## 10. Data design — the index file

<!-- BEGIN GENERATED: schema -->

**One JSON file, written whole.** There is no database, no sidecar and no index-level content hash: change detection is per file, on the `digest` field below. The file is not committed — `.gitignore` excludes `/*.json`, because a built index contains the corpus.

```
{
  "model":  // args.model
  "doc_prefix":
  "query_prefix":
  "chunk_chars":
  "dimensions":  // len(entries[0]['chunks'][0]['vector']) if entries else 0
  "files": [
    {
      "file":
      "digest":
      "chunks": [
        {
          "text":
          "vector":
        }, ...
      ]
    }, ...
  ]
}
```

| Level | Fields |
|---|---|
| root | `model`, `doc_prefix`, `query_prefix`, `chunk_chars`, `dimensions`, `files` |
| file entry | `file`, `digest`, `chunks` |
| chunk | `text`, `vector` |

<!-- END GENERATED: schema -->

**Field semantics.**

| Field | Meaning |
|---|---|
| `model` | The embedding model used. A query must use this model or results are meaningless |
| `doc_prefix`, `query_prefix` | The task prefixes in force at build time. `search` uses `query_prefix` from here, never from current code |
| `chunk_chars` | The effective ceiling used — `min(--chunk-chars, model limit × 3.2)` |
| `dimensions` | Vector length, taken from the first chunk. `0` for an empty index |
| `files[].file` | Path relative to the indexed root, not absolute |
| `files[].digest` | SHA-256 of the file text, first 16 hex characters. Change detection only |
| `files[].chunks[].text` | The chunk verbatim, **without** the document prefix |
| `files[].chunks[].vector` | The embedding, **with** the document prefix applied |

**The text stored and the text embedded differ by the prefix.** Storing the
prefixed form would corrupt every snippet shown to a reader.

**The index contains the corpus in full.** `.gitignore` excludes `/*.json` at
the repository root for exactly this reason: a built index is a copy of whatever
was indexed, and committing one publishes it.

---

## 11. Retrieval MCP tools — specified, not built

**`index_tools.py` does not exist.** `FS-21` … `FS-25` — `index_list`,
`index_search`, `index_get`, `index_explain` and their opt-in registration — are
specified in `URS_FS` and **have no implementation as of this document.**

**This section is a hole, and it is named as one.** A design specification that
silently omitted an unbuilt subsystem would be indistinguishable from one
describing a system that does not have it.

What is fixed about the design, and holds when it is built:

- **A separate module and an `index_*` namespace with no state shared with the
  delegation tools.** The seam must be visible in the file layout
- **Registration is opt-in**, conditional on an index directory being configured
  (`FS-21`) — absent that, the tools do not appear on the surface at all
- **A path allowlist is a hard requirement**, not a suggestion
- **Tools return citations, not content** — paths, headings, line ranges and
  scores. Hydration is a separate, deliberate step once relevance is known
- **Nothing that touches the corpus becomes a tool.** Building and re-indexing
  stay CLI operations

Until then, the retrieval capability is reachable only by a human at a command
line.

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
distinction.

**Every assertion is mutation-tested to prove it can fail.** Six guards were
deliberately broken and six were caught. The exercise surfaced two real defects
that a passing suite had been hiding, and one test that passed for the wrong
reason: a `file:///etc/passwd` case was being refused by the *hostname* check
rather than the scheme check, so it would have passed with the scheme check
deleted. Every scheme case now carries a hostname.

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

**It is not required to use the server.** The nine tools work without it. It is
required for them to be used well, and the failure it exists to prevent is
non-use — an agent grinding through bulk work inline, never reaching for
delegation at all.

---

## 14. Known deviations

**Four, all found by tracing this document against `URS_FS` and against the
source.** None is corrected here; a design specification records what shipped.

| # | Deviation | Kind |
|---|---|---|
| 14.1 | `FS-19` — `status` specified, not implemented | Unbuilt function |
| 14.2 | `FS-05` — `generate` uses `/api/generate`, not `/api/chat` | **Code contradicts the FS** |
| 14.3 | `UR-08` — no second-backend seam | Stated non-goal |
| 14.4 | No declared Python version floor | Undeclared constraint |

### 14.1 `FS-19` — `status` is specified and not implemented

`FS-19` reads: *"`status` re-hashes sources against stored digests and names what
changed."* **`vault_index.py` registers two subcommands, `build` and `search`.**
There is no `status`.

The digest machinery it would need exists and is used by incremental rebuild
(§9.5); what is missing is the subcommand that reports on it. `UR-16` —
staleness — is consequently unmet.

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

### 14.4 No Python version floor is declared

**Nothing in this repository states a minimum Python version.** There is no
`pyproject.toml`, no `setup.py`, no `requires-python`, no CI matrix, and the
README's install instructions name no version. `requirements.txt` contains one
line: `mcp`.

**`UR-31` concerns a *stated* floor being untested.** As shipped there is no
stated floor at all, so a user on too old an interpreter meets a `SyntaxError`
or an `ImportError` with nothing to tell them what happened — the failure mode
`PER-1` is least equipped to recover from.

**What the code actually requires has not been measured.** Both modules open with
`from __future__ import annotations`, which defers annotation evaluation and so
removes the most obvious floor-raising construct (`X | None` in signatures) from
consideration. **The real floor may be lower than assumed, and the honest
statement is that it is unknown.** Establishing it is CI's job, and there is no
CI.

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
| FS-19 | `status` — staleness | **§14.1** | ❌ **Not built** |
| FS-20 | Incremental reuse by digest | §9.5 | ✅ |
| FS-21 | `index_*` opt-in registration | **§11** | ❌ **Not built** |
| FS-22 | `index_list` | **§11** | ❌ **Not built** |
| FS-23 | `index_search` — citations | **§11** | ❌ **Not built** |
| FS-24 | `index_get` — hydration | **§11** | ❌ **Not built** |
| FS-25 | `index_explain` — MCP | **§11** | ❌ **Not built** |
| FS-26 | Spread reporting | §9.7 | ✅ |
| FS-27 | Rerank batches ≤ 5 | §9.8 | ✅ |
| FS-28 | `--selftest` | §12 | ✅ |
| FS-29 | `--probe` | §12 | ✅ |
| FS-30 | The skill ships with the server | §13 | ✅ |

**24 documented as shipped · 1 shipped with a deviation · 5 not built.**

`FS-25` deserves a note: **`--explain` exists as a CLI flag** (§9.7) and is not
the same thing. `FS-25` specifies it as an MCP tool, and that does not exist.

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
[Threat Model](THREAT_MODEL.md) ·
`README.md` · `SECURITY.md` · `MANUAL.md` · `VERIFICATION.md`

