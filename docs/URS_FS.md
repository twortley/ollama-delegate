---
okf_version: 0.1+TW1
title: ollama-delegate URS_FS — User Requirements and Functional Specification
purpose: The identified user requirements, the functions that satisfy them, and
  the traceability between them. The identifiers all downstream documents cite.
doc_type: specification
status: wip
doc_revision: "1"
created: 2026-08-28
last_updated: 2026-09-11T00:00:00-04:00
source_author: Tim Wortley + Claude
tags:
  - specification
  - requirements
  - urs
  - functional-spec
---

# URS_FS — User Requirements and Functional Specification

| | |
|---|---|
| **Document** | URS_FS |
| **Revision** | 1 (draft — not approved) |
| **System** | `ollama-delegate` — an MCP server exposing a local Ollama instance, its retrieval tooling, and the `local-inference-delegation` skill |
| **Personas** | PER-1 … PER-5, defined in section 4 |
| **Supersedes** | Nothing. First formal requirement set for this system |

> **GMP-shaped, not GMP-compliant.** This borrows the structure and discipline of
> regulated-industry specification practice — identified requirements, stated
> verification methods, bidirectional traceability. It carries **no QA approval,
> no change control, and no validated environment.** Nothing here claims a
> compliance status this project does not hold. The formality is present because
> it finds defects.

## 1. Purpose and scope

**Purpose.** To state what `ollama-delegate`'s users require, what the system
does in response, and the traceability between them.

**In scope:** the MCP server, the retrieval tooling, the skill, and the
documentation set required to make them usable by someone other than the author.

**Out of scope:** authentication,
multi-tenancy, multiple simultaneous Ollama hosts, automatic model routing,
generation over retrieved evidence, any persistence beyond a rebuildable index.

## 2. Definitions

| Term | Meaning |
|---|---|
| **Operator** | The human who installs and configures the system |
| **Caller** | Whatever invokes a tool — in practice an LLM agent, not a person |
| **Verdict** | A named outcome on every response, distinguishing failure modes |
| **Location** | Whether a call executed locally or on hosted infrastructure |
| **Index** | A flat file of chunked text plus embedding vectors, rebuildable |
| **Generation** | A short hash identifying one build of an index |

## 3. How to read a requirement

### 3.1 Assurance approach

**This project follows regulated-industry specification practice by the author's
preference**, not because any regulation applies to it.

**The approach is GAMP 5 Computer Software Assurance:** effort follows risk,
testing happens continuously rather than as a phase, and existing evidence is
used rather than re-created.

**Risk here means:** the chance a user is misled, blocked, or a published claim
fails when a reader checks it. Measured on the Likelihood / Impact /
Observability scale already used in the Threat Model
— one risk vocabulary across the project.

> **Nothing here is validated.** Validation is something only a regulated party
> can perform, against their own processes and intended use. This system is
> **verified**, to a stated standard.

### 3.2 Attributes

| Attribute | Values |
|---|---|
| **Class** | **M** mandatory for `v1.0.0` · **D** desirable |
| **Risk** | **H** · **M** · **L** — chance a user is misled or blocked, weighted by how badly and by whether they would notice |
| **Acceptance criterion** | The concrete, observable thing that demonstrates satisfaction. **If it cannot be written, the requirement is not testable and must be rewritten or dropped** |
| **Method** | **T** test · **I** inspection · **A** analysis · **D** demonstration |
| **Status** | ✅ met · ◐ partial · ❌ not met |

**Rigour follows risk, not class:**

| Risk | Verification effort |
|---|---|
| **H** | A repeatable check with a recorded result, re-runnable by someone else. Failure is a release blocker |
| **M** | Executed once, result noted in the VR. Failure is a triage decision |
| **L** | Inspected, or exercised in normal use. A sentence in the VR is sufficient |

**Every `UR` names a parent persona. Every `FS` names a parent `UR`.** Section 7 proves both
directions.

### 3.3 Verification environments

**Acceptance criteria are constrained to what this project can execute.** A
criterion that names no environment capable of running it is not a criterion.

| ID        | Specification                                                                                                                                                                                                                            | Covers                                                                                                                                               |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ENV-1** (`A001`) | Windows 11 Pro 25H2 · Ollama 0.33.2 · **Python 3.14.4** · Claude Desktop                                                                                                                                                                 | The main development machine and test bed. Most verification was executed here                                                                       |
| **ENV-2** (`A005`) | Ubuntu 24.04.4 · Ollama 0.30.6 · **Python 3.12.3** · Claude Desktop                                                                                                                                                                      | Verification against a clean Ubuntu target                                                                                                           |
| **ENV-3** (`A001`) | **A second MCP client — a non-Claude agentic IDE**, on the same Windows host and Ollama instance as `ENV-1`                                                                                                                              | Tool enumeration and response handling outside Claude Desktop                                                                                        |
| **ENV-4** (no asset) | Unit-test environment · Linux container · **Python 3.10.12** · **no Ollama reachable** · no MCP client                                                                                                                                   | **The declared floor interpreter.** Unit and static verification: `--selftest`, `mutation_check.py`, source inspection                               |
| **ENV-5** (`A011`) | **A Windows laptop that is not the development host** · **Python 3.14** and Ollama both already present · **not a designated inference host** — an embedding model and two small local chat models are installed and run, slowly; the cloud-tagged models on it are the usable ones · no prior copy of this project | The install path followed by a reader who already meets the prerequisites: obtain the code, venv, dependency, `--selftest`, `--probe`, client wiring |
| **ENV-6** (no asset) | **The session sandbox with an MCP bridge to a host's Ollama** · Linux container · the *caller* runs here, the *model* runs on the bridged host · `localhost:11434` is NOT reachable from the container; the bridge is                    | Agent-executed testing of the caller-facing artefacts — the skill, routing, and anything measuring what a caller does                                |

> **`ENV-3` assigned 2026-08-31**, having been *"to be determined"* since this
> document was written. Same `mcpServers` entry shape as `ENV-1`; connected first
> attempt.
>
> **Deliberately not another Claude client.** A Claude-based one refuses
> destructive actions on its own policy regardless of our gate — `VT-031` Arm B
> showed exactly that, which tests the client rather than us.
>
> **Evidenced, not merely connected.** `VT-040` enumeration and `location` on all
> 18 models · `VT-041` a real model call, 768 dimensions, `location: local` ·
> **`VT-014` the refusal path — `not_permitted` reached and shown to the
> operator through a non-Claude client.**
>
> 🔴 **And it found what only this environment could.** Refused by the tool, the
> client ran `ollama rm` in a shell. The operator declined it; **nothing in this
> system would have.** It also displayed our remedy telling a Gemini user to
> *"restart Claude Desktop"*. See Threat 4; the remedy wording is tracked as a
> defect.

> **`ENV-5` is the first environment that is not the development host.** `ENV-1`
> and `ENV-2` both had this project's own venv in place before any test began, so
> every install run so far started part-way through the instructions. **`ENV-5`
> had no copy of this project at all** — it exercises obtaining the code, venv
> creation, dependency resolution, `--selftest`, `--probe` and client wiring as
> one uninterrupted sequence.
>
> **It resolved the dependency independently.** A fresh install there pulled
> **`mcp` 2.1.1**, a different SDK generation from `ENV-1`'s, in 29 packages —
> against the README's stated expectation of "around thirty". `requirements.txt`
> leaves `mcp` unpinned on the grounds that both SDK generations are tolerated in
> source; **until this run that was an assertion**, and it is now a measurement
> taken against the version a new reader actually gets.
>
> **It also exercised the embedding-model precondition for real.** No embedding
> model was installed, the `embed` call failed as the README predicts, and
> `ollama pull nomic-embed-text` fixed it — the one prerequisite in the list that
> was genuinely absent.
>
> 🔴 **This row was corrected on 2026-08-31.** It previously specified a machine
> with **no Ollama, no Python and no models**, and named hardware — 16 GB shared
> memory, integrated GPU — that the machine used does not have. The machine
> actually used had Python 3.14 and Ollama already installed. **A published
> environment description that does not match the machine is a claim that fails
> when checked**, which is this project's stated risk basis, so the row now
> describes what was run rather than what was planned.
>
> **Installing Python and Ollama is out of scope, and their absence here is not a
> coverage gap.** Both have their own installers; what this project owes is a
> named refusal when either is missing, which the `Declared Python floor` group
> and `verdict is ollama_unreachable` already assert. Listing them as untested
> environments was manufacturing a gap to make a matrix look complete.
>
> 🔴 **Correction, later the same day: `ENV-5` did falsify the performance
> guidance, and this note previously said it could not.** The paragraph here read
> *"the performance falsifier is unmet, and now has no home"*. That was written
> before the retrieval tools were exercised on the host, and it was wrong.
>
> ⚠️ **`ENV-5` is an embedding host, not an inference host.** Ollama runs there
> and `nomic-embed-text` embedded a 33-chunk corpus without complaint, but
> **reranking does not complete**: the default `gpt-oss:20b` is not installed,
> and `qwen2.5:7b` did not return inside the client's timeout. Searches on that
> host run on cosine alone.
>
> **That is guidance, not a defect.** Every timing in this project — cold load,
> rerank duration, the client-timeout limitation — was measured on a fast
> unified-memory desktop or a box with datacentre cards, and the manual has so
> far said nothing about hardware that cannot carry a reranker at all.
> **"Embeds, but cannot rerank" is a real class of target machine**, and
> `--rerank-pool` on the CLI, which has no client timeout, is the escape the tool
> already provides.
>
> **The cosine fallback is normal behaviour, not a failure of anything.** How
> fast Ollama runs a given model on given hardware is outside this project's
> scope; reranking is optional by design, and a host that cannot afford it gets
> embedding order instead. What this project owns is only the *reporting* of it —
> `reranked_by.failures` carried the timeout, so the caller was told the order
> was unreranked rather than being handed cosine order dressed as a judged one.
> Given how often this project has been caught by output that looked healthy and
> was wrong, that the instrumentation said so out loud is the result worth
> recording.
>
> **Still unfalsified:** the *timings* themselves. `ENV-5` establishes that a
> modest host cannot rerank; it does not give the manual a number for how long
> anything takes on one.

> **`ENV-4` is where the floor is exercised.** Python 3.10.12 at the time of
> build and test, 2026-08-30 — the only environment in this set that runs the
> declared minimum, and the reason `UR-31`'s floor has evidence behind it at all.
>
> **It cannot verify anything that needs a model.** No Ollama endpoint is
> reachable, so `--probe`, live inference and every retrieval result are out of
> reach by construction. It establishes that the code runs, parses and self-tests
> on 3.10 — which is exactly what a unit-test environment is for. **A result
> attributed to `ENV-4` that required a live model did not come from `ENV-4`.**
>
> The container is provisioned per session, so **confirm the interpreter when
> recording a new run** rather than assuming this row still describes it. That
> does not weaken the runs already recorded against it.

---

## 4. User requirements

### 4.1 PER-1 — the struggling adopter

| ID | Requirement | Cls | Risk | Acceptance criterion — what we run | M | Status |
|---|---|---|---|---|---|---|
| **UR-01** | The operator **shall be able to** install and run the system by following documented instructions, without editing source | M | **H** | On a clean container and on the Ubuntu host, follow the manual verbatim; the server starts and enumerates its tools. **No step requires a value not printed in the manual.** ⚠️ **Failed twice on `ENV-2` 2026-08-29** — interpreter path, then `python.exe` surviving into a POSIX client config. Both fixes written, **neither re-proven** | D | ✅ |
| **UR-02** | The operator **shall be able to** confirm a correct installation without reading source code | M | **H** | On that same clean environment, `--selftest` exits 0 and `--probe` names the reachable host and lists installed models. Both are reachable from the manual's install section. **Met on `ENV-2` 2026-08-29: clean clone, both checks correct, no hand remediation** | T | ✅ |
| **UR-03** | The operator **shall be able to** configure model access, write permissions and index location without editing code | M | M | Each `OLLAMA_MCP_*` variable changes the permitted operation set, and `server_info` reports the change. No source file is touched. **The documented config example names every variable at its default**, so their existence is discoverable without reading source | T | ✅ |
| **UR-04** | The operator **shall be able to** determine, for any call, whether content left their machine | M | **H** | Every inference response carries `location`. A `:cloud`-tagged model returns `cloud`; a local model returns `local`. **Exercised on `ENV-2` through a live client: 4 local / 2 cloud split correctly, and the agent volunteered *"the text never left the machine"* unprompted** | T | ✅ |
| **UR-05** | The operator **shall be able to** index a corpus of their own choosing | M | M | `build` against an arbitrary operator-supplied folder produces an index whose file count equals the folder's `.md` count | D | ✅ |
| **UR-06** | On failure the operator **shall be able to** obtain a stated cause and a remedy, not a stack trace | M | **H** | Every refusal path returns `verdict`, `reason` and `remedy`. **Each remedy is executed and produces the promised effect** | T | ⚠️ |
| **UR-31** | The system **shall** run on the Python version stated in its own documentation | M | M | **Floor declared 3.10 and enforced at runtime** by both entry points (2026-08-30); stated in `requirements.txt`, the README and DS §14.4. Exercised on **3.10.12** (`ENV-4`, selftest and mutation check, current code), **3.12.3** (`ENV-2`, end to end, but on code predating the retrieval tools) and **3.14.4** (`ENV-1`, selftest, current code). **3.11 is untested and will stay so — owner decision, no CI matrix.** Met with a stated and accepted gap, not by measurement across the range | T | ⚠️ |

### 4.2 PER-2 — the developer

| ID        | Requirement                                                                                                                       | Cls | Risk | Acceptance criterion — what we run                                                                                                                                                                                                                                                                                                         | M   | Status |
| --------- | --------------------------------------------------------------------------------------------------------------------------------- | --- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --- | ------ |
| **UR-07** | A reader **shall be able to** determine **what** was chosen — the technologies, protocols and structures the system is built from | M   | M    | The DS names the constituent choices at a level a builder would need: *"stdio MCP server, Python, stdlib HTTP, flat-file index"*. **Verified by inspection: a reader can list the technology stack from the DS without opening the source.** Reasoning and rejected alternatives are explicitly **not** required                           | I   | ✅      |
> #### ✏️ `FS-05` rewritten 2026-08-30 — it specified an engineering decision, not a function
>
> **It read:** *"`generate` performs single-prompt completion via `/api/chat`,
> never `/api/generate`."*
>
> **That is an implementation, written where a behaviour belongs.** The endpoint
> was the mechanism someone chose in order to get the chat template applied; the
> *requirement* was always that the template gets applied, because skipping it
> makes template-sensitive families emit reserved vocabulary such as
> `<unused50>`.
>
> **The cost of the confusion was a whole verification cycle.** `VT-010` returned
> clean prose on four independent paths — the behaviour was correct — and was
> still graded a failure, because the code called the other endpoint. **A test
> that passes its own stated criterion cannot be a failure**, and a requirement
> that turns a passing behaviour into a deviation is over-specified.
>
> **The general rule, since this will recur:** an FS says *what must be true*, in
> terms a test can check. *How* it is achieved is the DS's business, and putting
> it in the FS converts every future implementation change into a spec deviation
> for no gain in safety.
>
> **Nothing is lost.** The new wording is *more* testable, not less: the
> `prompt_eval_count` comparison detects a skipped template **whatever endpoint
> is used and whatever Ollama does next**, which the old wording could not — it
> only checked that we called a particular URL. The `<unused50>` history is kept
> in DS §5.2 as the reason, where reasons belong.

| **UR-08** | A developer **shall be able to** add an alternative inference backend without rewriting the tool layer                               | D   | L    | **Verified by inspection 2026-08-28:** exactly one `urlopen` call site per file; `_request()` is defined once and called from 13 tools. A second backend is a single-function change                                                                                                                                                       | I   | ✅      |
| **UR-10** | A reader **shall be able to** distinguish a deliberate constraint from an incidental implementation detail                        | D   | L    | The non-goals section exists, and each constraint named there cites a decision                                                                                                                                                                                                                                                             | I   | ✅      |

### 4.3 PER-3 — the retrieval user

| ID        | Requirement                                                                                                         | Cls | Risk  | Acceptance criterion — what we run                                                                                                                                                                     | M   | Status |
| --------- | ------------------------------------------------------------------------------------------------------------------- | --- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --- | ------ |
| **UR-11** | The operator **shall be able to** maintain multiple independent indexes over different corpora                      | M   | M     | Two indexes built over disjoint folders coexist; a search of one returns **no chunk originating in the other**                                                                                         | T   | ✅      |
| **UR-12** | The operator **shall be able to** identify an index's purpose and provenance without opening it                     | M   | M     | `index_list` reports name, description, source root, build time and model for each. An index lacking a field reports `unknown`, never a value                                                          | T   | ✅      |
| **UR-13** | A caller **shall be able to** search one index in isolation from the others                                         | M   | M     | Covered by UR-11's criterion                                                                                                                                                                           | T   | ✅      |
| **UR-14** | A caller **shall be able to** judge whether a ranked result discriminates between candidates or merely participates | M   | M     | A query with a genuinely relevant result and a query with none produce **visibly different spread lines**; the latter is flagged. *The system supplies the signal; the judgement remains the caller's* | T   | ✅      |
| **UR-15** | A caller **shall be able to** distinguish a recall failure from a ranking failure                                   | M   | M     | Seed one string absent from the corpus and one present but poorly ranked. `--explain` distinguishes them                                                                                               | T   | ✅      |
| **UR-16** | A caller **shall be able to** determine an index's staleness before relying on it                                   | M   | M     | Modify a source file after a build; `status` names that file. `index_search` reports `built_at` and the refresh command                                                                                | T   | ✅      |
| **UR-17** | An agent **shall be able to** invoke retrieval directly, without a human transcribing terminal output               | M   | **H** | An agent calls `index_search` over MCP and receives citations, with no human in the loop                                                                                                               | D   | ✅      |

### 4.4 PER-4 — the orchestrating agent

| ID        | Requirement                                                                                                                                                                      | Cls | Risk  | Acceptance criterion — what we run                                                                                                                                                                                                                                                                                                                                                                                                              | M   | Status |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------ |
| **UR-18** | A caller **shall be able to** distinguish a successful call from one that succeeded and returned nothing useful                                                                  | M   | **H** | An empty generation, a truncated generation and a mismatched embedding count each return a **distinct verdict**, none of them `ok`                                                                                                                                                                                                                                                                                                              | T   | ✅      |
| **UR-19** | A caller **shall be able to** determine where a call executed, on every response, without inferring it from a name                                                               | M   | **H** | `location` present on every listing and inference response. `cloud?` appears **only** where tag and size disagree                                                                                                                                                                                                                                                                                                                               | T   | ✅      |
| **UR-20** | A caller **shall be** refused, before execution, on the three actions this system identifies as silently corrupting or irreversible: over-length input, model pull, model delete | M   | **H** | **All three verified.** `VT-030` the guard logic; `VT-031` the refusal reached and seen on `ENV-1`, including `context_exceeded` **before any model call**; `VT-014` the same through a non-Claude client on `ENV-3`. ⚠️ **Scope of the guarantee:** the gate governs *this tool surface*, not the capability — on `ENV-3` the client, having been refused, ran `ollama rm` in a shell and was stopped by the operator, not by us. See Threat 4 | T   | ✅      |
| **UR-21** | A caller **shall be able to** discover installed models and their capabilities rather than guessing identifiers                                                                  | M   | M     | Every model name returned by `list_models` is callable without a 404                                                                                                                                                                                                                                                                                                                                                                            | T   | ✅      |
| **UR-22** | A caller **shall be able to** obtain retrieval evidence at a context cost proportional to what it requested, not to what was searched                                            | M   | M     | Search returns citations without chunk text. **A 10-result search returns under 5 kB against a 400+ chunk corpus.** Measured `VT-020`: 2,240 chars, ~224 per citation, 443-chunk index, `ENV-1`, 2026-08-30. **Ceiling widened from 1 kB by ruling 2026-08-30 — the original figure was never measured**; citations now carry a heading and a line range, which is the feature                                                                  | T   | ✅      |
| **UR-23** | A caller **shall be able to** determine when delegation is appropriate, from guidance supplied with the system                                                                   | M   | **H** | A caller routes correctly **in both directions** — delegating bulk mechanical work and declining judgement work — across four cases, from the guidance the system supplies. ⚠️ **Criterion restated 2026-09-12**, after the previous one (*a difference in delegation rate against a control without the guidance*) proved unmeasurable: the tool descriptions are themselves guidance supplied with the system, so no control can be deprived of them without testing a product that is not shipped                                                                                                                                                                                                                                                                                                                     | T   | ⚠️      |

| **UR-33** | The caller **shall be able to** determine what a completed call cost, in generated tokens, throughput and model load time | D | L | Every inference response carries `eval_count` and `tokens_per_second` when Ollama reports them, and reports model load time **separately** from generation rate so a cold load does not read as a slow model | I | ✅ |

### 4.5 PER-5 — the evaluating reader

| ID        | Requirement                                                                                            | Cls | Risk  | Acceptance criterion — what we run                                                        | M   | Status |
| --------- | ------------------------------------------------------------------------------------------------------ | --- | ----- | ----------------------------------------------------------------------------------------- | --- | ------ |
| **UR-24** | A reader **shall be able to** determine the system's purpose within minutes, led by capability rather than cost   | M   | M     | The README's first screen states what it is for, and **cost is not the leading argument** | I   | ✅      |
| **UR-25** | A reader **shall be able to** determine the system's non-goals, and what it does not unlock                       | M   | M     | Named *Assumptions and non-goals* section present, including the accountability statement | I   | ✅      |
| **UR-26** | A reader **shall be able to** determine how this differs from redirecting **Claude Code** at Ollama via `ANTHROPIC_BASE_URL`             | M   | M     | The README answers the question explicitly, by name, and **distinguishes Claude Code from Cowork** rather than treating them as one client                                       | I   | ✅      |
| **UR-27** | A reader **shall be able to** determine what has been verified, with what limits, and what defects ship knowingly | M   | M     | VR published, carrying the defect triage register with a release decision per entry       | I   | ✅      |
| **UR-28** | A reader **shall be able to** determine the licence terms                                                         | M   | **H** | `LICENSE` present at repository root and named in the README. **MIT, © 2026 Tim Wortley, written 2026-08-28.** README states the licence covers the specification and verification documents, not only source | I   | ✅ |
| **UR-32** | A reader **shall be able to** determine what the system was *required* to do, and follow any requirement through to its design and its verification | M | M | **Every `FS`/`UR` identifier cited in a published document resolves within the published set.** Run as a link check, not a judgement: extract every identifier from `docs/*.md`, and fail on any that no published document defines | T | ✅ |

> #### ⚠️ `UR-32` is PROPOSED, not agreed — added 2026-08-30
>
> **It was written because `DOC-07` had no parent and the backward trace would
> have caught it.** Publishing `URS_FS` was ruled the same day (§5.10), and a
> deliverable with no requirement behind it is the orphan shape §8.2 exists to
> find.
>
> **The gap it fills is real and predates the ruling.** PER-5 asks a reader to
> determine purpose, non-goals, positioning, verification and licence. **It never
> asks that they be able to determine what was required** — so the entire trace
> discipline, the thing this document set is built around, had no user
> requirement demanding it. `DOC-03` and `DOC-04` were parented to what they
> *contain*; nothing parented the fact that they *trace*.
>
> **This is the requirement the owner described on `DS_Open_Questions` Q13** —
> *"a high-level user requirement about enabling compliance, or in this case
> following best practices for SDLC in regulated industries"* — narrowed to
> something testable. **The broad version is not testable and would be ceremony;
> the link check finds a defect.**
>
> **Status corrected 2026-09-12.** This block read *"Status `❌` because the
> check does not pass today — the DS cites 35 identifiers and none resolves in
> the published set."* **That stopped being true when `URS_FS` was published.**
> The link check ran 2026-08-31 over the published set and again 2026-09-12 with
> `VERIFICATION.md` added to it: **62 identifiers cited, 62 defined, none
> unresolved** (`VT-026`). The row has stood at ✅ since; this prose did not.
> Stated rather than quietly amended, because prose transcribing a register it
> never re-read is the defect this document set keeps finding in itself.
>
> **Still outstanding, and unchanged: `UR-32` is PROPOSED. Wording and class are
> the owner's to rule on.**

### 4.6 Constraints

| ID        | Requirement                                                                                 | Cls | Risk | Acceptance criterion — what we run                                                              | M   | Status |
| --------- | ------------------------------------------------------------------------------------------- | --- | ---- | ----------------------------------------------------------------------------------------------- | --- | ------ |
| **UR-29** | The system **shall** install with the minimum dependency set its function requires          | D   | L    | `requirements.txt` names exactly one **direct** runtime dependency; the retrieval tool imports stdlib only. ⚠️ **Criterion known too narrow from 2026-08-29:** `pip install mcp` installs ~27 transitive packages. The requirement is met; the criterion did not measure install footprint | I   | ✅      |
| **UR-30** | The system **shall** make no network connection other than to the configured inference host | D   | M    | The transport has a single call site (`_request`); every URL it builds derives from `config.base_url`. **Verified by inspection of the call graph** — packet capture is disproportionate at this level | I   | ✅      |

## 5. Functional specification

Each function names the `UR` it exists to satisfy.

### 5.1 Discovery

| ID | Function | Satisfies |
|---|---|---|
| **FS-01** | `list_models` returns every installed model with `location`, size, family and quantisation | UR-21, UR-04 |
| **FS-02** | `show_model` returns capabilities, context length, parameters and `location` | UR-21 |
| **FS-03** | `list_running` returns currently loaded models | UR-21 |
| **FS-04** | `server_info` returns the effective configuration, including base URL and permission state | UR-03, UR-04 |

### 5.2 Inference

| ID | Function | Satisfies |
|---|---|---|
| **FS-05** | `generate` submits single-prompt completions **such that the model's chat template is applied**, so a template-sensitive family returns text rather than reserved vocabulary. Verified by comparing `prompt_eval_count` against an equivalent `chat` call: equal counts mean equal tokenisation, so no template was skipped | UR-18 |
| **FS-06** | `chat` performs multi-turn completion, budgeting the entire message history | UR-18, UR-20 |
| **FS-07** | `embed` returns vectors, asserting output count equals input count | UR-05, UR-20 |

### 5.3 Host state

| ID | Function | Satisfies |
|---|---|---|
| **FS-08** | `pull_model` fetches a model; refused unless enabled in the process environment | UR-03, UR-20 |
| **FS-09** | `delete_model` removes a model; refused unless enabled in the process environment | UR-03, UR-20 |

### 5.4 Enforcement and reporting

| ID | Function | Satisfies |
|---|---|---|
| **FS-10** | `Guard.check()` is the single enforcement point: write gating and model allowlisting | UR-03, UR-20 |
| **FS-11** | Every response carries a named `verdict` from a fixed vocabulary, never a bare result | UR-06, UR-18 |
| **FS-12** | Every model listing and inference response carries `location` — `local`, `cloud`, or `cloud?` | UR-04, UR-19 |
| **FS-13** | Input exceeding the model's context is refused before the call; proceeding requires explicit opt-in | UR-20 |
| **FS-14** | A truncated generation is reported as incomplete rather than returned as finished output | UR-18 |
| **FS-15** | Responses report token throughput and timing | UR-33 |
| **FS-16** | All configuration derives from the process environment; no config file grants capability | UR-03 |
| **FS-17** | Every refusal carries a cause and a remedy | UR-06 |

### 5.5 Retrieval — CLI

| ID | Function | Satisfies |
|---|---|---|
| **FS-18** | `build` chunks a folder, embeds it, and writes an index with a descriptive header | UR-05, UR-11, UR-12 |
| **FS-19** | `status` re-hashes sources against stored digests and names what changed | UR-16 |
| **FS-20** | Rebuild reuses unchanged files by per-file digest | UR-16 |

### 5.6 Retrieval — MCP, opt-in

| ID | Function | Satisfies |
|---|---|---|
| **FS-21** | `index_*` tools register only when an index directory is configured | UR-03, UR-17 |
| **FS-22** | `index_list` returns each index's name, description, source root, build time, size and model | UR-12, UR-16 |
| **FS-23** | `index_search` returns citations, embedding the query with the index's own recorded model | UR-13, UR-22 |
| **FS-24** | `index_get` hydrates named chunks from the index, refusing generation-mismatched ids | UR-22, UR-16 |
| **FS-25** | `index_explain` reports where known text ranks for a query | UR-15 |

### 5.7 Diagnostics

| ID | Function | Satisfies |
|---|---|---|
| **FS-26** | Search reports score spread and warns when one value dominates | UR-14 |
| **FS-27** | Reranking scores candidates in batches of five or fewer | UR-14 |

### 5.8 Self-verification

| ID | Function | Satisfies |
|---|---|---|
| **FS-28** | `--selftest` runs in-process assertions, each mutation-tested to prove it can fail | UR-02 |
| **FS-29** | `--probe` exercises the configured host and reports what it found | UR-02, UR-01 |

### 5.9 Judgement

| ID | Function | Satisfies |
|---|---|---|
| **FS-30** | The `local-inference-delegation` skill supplies routing judgement and ships with the server | UR-23 |

### 5.10 Documentation deliverables

| ID         | Deliverable                                                                                                                                                  | Satisfies                         |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------- |
| **DOC-01** | `README.md` — purpose (capability-led); assumptions and non-goals including what this does not unlock; positioning against redirecting Claude Code at Ollama | UR-24, UR-25, UR-26               |
| **DOC-02** | `docs/MANUAL.md` — install, configure, verify, troubleshoot, per OS                                                                                                    | UR-01, UR-02, UR-03, UR-06, UR-31 |
| **DOC-03** | `docs/DESIGN.md` — **as-built** technical detail: architecture, module and data design, configuration surface, interfaces                                | UR-07, UR-08, UR-10               |
| **DOC-04** | `docs/VERIFICATION.md` — evidence, limits, defect triage register                                                                                      | UR-27                             |
| **DOC-05** | `SECURITY.md` and Threat Model — boundaries and accepted risks                                                                                               | UR-04, UR-25                      |
| **DOC-06** | `LICENSE`                                                                                                                                                    | UR-28                             |
| **DOC-07** | `docs/URS_FS.md` — **this document** — user requirements and functional specification, published so that every `FS`/`UR` cited elsewhere resolves                               | UR-32                             |

> #### ✅ RULED 2026-08-30 — `URS_FS` is published, as `DOC-07`
>
> **The DS cites 35 identifiers — 30 `FS` and 5 `UR` — and until this ruling
> none of them resolved in the published set.** `DOC-01` … `DOC-06` are README,
> Manual, DS, VR, `SECURITY.md` + Threat Model, and `LICENSE`. This document was
> not among them.
>
> **That breaks the constraint the owner set on `DS_Open_Questions` Q11:**
> *anything the DS cites must be in the DS or in another published document —
> there is no "see the vault" escape hatch.* The DS's trace matrix is its
> defect-finding instrument, and it pointed at a register no reader could open.
>
> **The VR would have had it worse.** It verifies *against* these identifiers, so
> every row would have been unresolvable rather than just the matrix.
>
> **Publishing this document was the cheapest of three options** — it exists and
> is owner-reviewed; the alternatives were duplicating 35 requirement statements
> into the DS as an appendix (a second register to keep in step, this project's
> known drift failure) or dropping identifiers from the published copies
> (removing the instrument that found four deviations in the DS alone).
>
> **§8's gap analysis becomes public, and that is a feature.** Honest not-met
> counts and persona scoring are the evidence `PER-5` exists to get; `UR-27`
> already commits to shipping known defects with a release decision. **A
> specification that publishes its own gaps is more credible than one that does
> not, not less.**
>
> **It publishes to `docs/URS_FS.md`.** `Target_Repo_Layout.md` updated the same
> day.

---

## 6. Assumptions

1. A single trusted operator. The caller holds operator authority
2. One Ollama host per server process, reachable and correctly secured by the operator
3. The indexed corpus is trusted content
4. Cloud models are permitted and labelled, not prevented
5. Python 3.10 or later

---

## 7. Traceability

**Forward — every `UR` reaches a function or a deliverable.**

| UR | Satisfied by |
|---|---|
| UR-01 | FS-29, DOC-02 |
| UR-02 | FS-28, FS-29, DOC-02 |
| UR-03 | FS-04, FS-08, FS-09, FS-10, FS-16, FS-21, DOC-02 |
| UR-04 | FS-01, FS-04, FS-12, DOC-05 |
| UR-05 | FS-07, FS-18 |
| UR-06 | FS-11, FS-17, DOC-02 |
| UR-07 | DOC-03 |
| UR-08 | DOC-03 |
| UR-10 | DOC-03 |
| UR-11 | FS-18 |
| UR-12 | FS-18, FS-22 |
| UR-13 | FS-23 |
| UR-14 | FS-26, FS-27 |
| UR-15 | FS-25 |
| UR-16 | FS-19, FS-20, FS-22, FS-24 |
| UR-17 | FS-21 |
| UR-18 | FS-05, FS-06, FS-11, FS-14 |
| UR-19 | FS-12 |
| UR-20 | FS-06, FS-07, FS-08, FS-09, FS-10, FS-13 |
| UR-21 | FS-01, FS-02, FS-03 |
| UR-22 | FS-23, FS-24 |
| UR-23 | FS-30 |
| UR-24 | DOC-01 |
| UR-25 | DOC-01, DOC-05 |
| UR-26 | DOC-01 |
| UR-27 | DOC-04 |
| UR-28 | DOC-06 |
| UR-29 | *constraint — inspection of `requirements.txt`* |
| UR-30 | *constraint — inspection of the transport call graph* |
| UR-31 | DOC-02 |
| UR-32 | **DOC-07** |
| UR-33 | **FS-15** |

**Backward — every `FS` reaches a `UR`.** Verified in both directions; no
function is unparented and no requirement is unsatisfied.

---

## 8. Related

**Published and linkable:** `README.md` · [Design Specification](DESIGN.md) ·
`SECURITY.md` · `LICENSE`

**Specified but not yet published** — named rather than linked, because a link to
a document that does not exist is worse than no link: `docs/MANUAL.md` (DOC-02) ·
`docs/VERIFICATION.md` (DOC-04) · `THREAT_MODEL.md` (DOC-05)

