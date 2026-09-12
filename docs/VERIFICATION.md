<!-- GENERATED from verification_manifest.json by generate_verification.py. Do not hand-edit: your changes will be overwritten and, worse, will not be checked. -->

# Verification Report

**40 tests — 1 deferred · 39 pass.** **14 known defects, each with a release decision.**

Every entry carries an expected outcome and a **falsifier**. An entry without a falsifier is a demonstration that a process was followed, and the generator refuses to render one.

`disclosed` is a conscious release decision with a release note — **not a synonym for untested.** A gap shipped knowingly is a normal release; a gap shipped quietly is a claim that fails when someone checks it.

## Known defects, and what was decided about each  (14)

**Every defect this project knows about — including the ones already fixed — with the decision taken on it.** A gap shipped knowingly is a normal release; a gap shipped quietly is a claim that fails when someone checks it.

Decisions come from one vocabulary — **fix before release**, **release with disclosure**, **defer with justification** — and `status` says whether the decision has been carried out. The release gate refuses a defect decided *fix before release* that is still open.

| | Defect | Severity | Decision | Status |
|---|---|---|---|---|
| **D-01** | The skill's contribution over the tool descriptions is unmeasured | medium | defer with justification | open |
| **D-02** | The bundled embedding script was never exercised by the eval that cites it | medium | release with disclosure | open |
| **D-03** | No eval case drives the context-overflow guard end to end | medium | release with disclosure | open |
| **D-04** | Tolerance of two MCP SDK generations is asserted from the code, not observed | medium | release with disclosure | open |
| **D-05** | Retrieval tuning is measured on a single corpus | medium | defer with justification | open |
| **D-06** | Indexed content reaches a model's context and is not sanitised | medium | release with disclosure | open |
| **D-07** | The server does not report the build it is running | low | defer with justification | open |
| **D-08** | A read timeout escaped every handler and reached the operator as a stack trace | critical | fix before release | resolved |
| **D-09** | A requirement's acceptance criterion carried a number nobody had measured | high | fix before release | resolved |
| **D-10** | `index_list` omitted the source root its requirement names | medium | fix before release | resolved |
| **D-11** | Tool descriptions and refusal remedies misstated the server's own configuration | medium | fix before release | resolved |
| **D-12** | Two shipped behaviours had no test, and a coverage count could not tell that from a bookkeeping gap | medium | fix before release | resolved |
| **D-13** | Installing from the README required a substitution the README did not document | medium | fix before release | resolved |
| **D-14** | The declared Python floor was never exercised | low | fix before release | resolved |

### D-01 — The skill's contribution over the tool descriptions is unmeasured

**medium · defer with justification · open**

| | |
|---|---|
| Affects | — |
| Evidence | `VT-048`, `VT-051` |
| Rationale | Three eval rounds compared a caller holding the skill's guidance against one without it, and found no difference in routing. The first round was void -- its control could not have delegated. The second and third were sound and returned nulls. The cause is structural: the control always holds the tool descriptions, which are themselves guidance supplied with the system, so the skill's marginal contribution cannot be isolated without testing a stripped product. What the runs DO establish is that a caller routes correctly in both directions, which is what UR-23 asks and what VT-051 records. |
| Release note | The system conveys when delegation is appropriate; the skill's contribution OVER the tool descriptions is unmeasured, and no figure is published for it. The skill ships as a routing procedure and a set of model-specific facts, not as a measured improvement in routing. |

### D-02 — The bundled embedding script was never exercised by the eval that cites it

**medium · release with disclosure · open**

| | |
|---|---|
| Affects | — |
| Evidence | — |
| Rationale | The eval's subagents run in an isolated sandbox with no route to the host's Ollama endpoint. Both arms worked around it by embedding through the server and reimplementing the arithmetic, so the case tested reasoning about the script rather than the script. |
| Release note | Eval case 1 is reported as what it is — a test of reasoning about the bundled script, not of running it. |

### D-03 — No eval case drives the context-overflow guard end to end

**medium · release with disclosure · open**

| | |
|---|---|
| Affects | — |
| Evidence | `VT-007`, `VT-044`, `VT-050` |
| Rationale | Carried since 2026-08 as “an untested area”, and checking it before publication corrected that: the guard itself is verified — oversized input is refused before the call (VT-050), `chat` budgets the whole message history rather than the newest turn (VT-044), and a truncated generation returns a distinct verdict rather than finished-looking output (VT-007). What no case covers is the guard driven end to end by a caller, and silent truncation is still the worst failure mode available. |
| Release note | The context guard is covered by assertions rather than by an end-to-end eval case. |

### D-04 — Tolerance of two MCP SDK generations is asserted from the code, not observed

**medium · release with disclosure · open**

| | |
|---|---|
| Affects | — |
| Evidence | — |
| Rationale | The dependency is unpinned and the installed SDK was 2.1.1. The compatibility path ran and left no evidence of which generation it used, so the claim is unfalsified rather than verified. |
| Release note | Compatibility with the older MCP SDK generation is a design claim, not a measured one. Pin the SDK if you depend on it. |

### D-05 — Retrieval tuning is measured on a single corpus

**medium · defer with justification · open**

| | |
|---|---|
| Affects | — |
| Evidence | — |
| Rationale | The reranking figures come from one corpus, one afternoon, five queries and a negative control. Nothing establishes that the defaults generalise, and a second corpus is the deferred work that would settle it. |
| Release note | Treat the retrieval defaults as a starting point: they are measured against one corpus. |

### D-06 — Indexed content reaches a model's context and is not sanitised

**medium · release with disclosure · open**

| | |
|---|---|
| Affects | — |
| Evidence | — |
| Rationale | Anything indexed can carry instructions, and whatever a model returns is data rather than instruction. The path is accepted with a stated reassessment trigger rather than controlled, because filtering it would be a claim the implementation could not keep. |
| Release note | Index only content you trust, and treat model output as data. See SECURITY.md. |

### D-07 — The server does not report the build it is running

**low · defer with justification · open**

| | |
|---|---|
| Affects | — |
| Evidence | `VT-047` |
| Rationale | `server_info` reports no version or commit, so no manually captured result can name the code that produced it from the server's own output — provenance depends on the operator recording it separately. A long-running stdio server serves the code it loaded at start, which is what makes this worth saying rather than assuming. |
| Release note | `server_info` reports no build identifier. Record the commit alongside any manual result. Deferred past this release. |

### D-08 — A read timeout escaped every handler and reached the operator as a stack trace

**critical · fix before release · resolved**

| | |
|---|---|
| Affects | `UR-06`, `FS-17` |
| Evidence | `VT-032` |
| Rationale | `URLError` and `TimeoutError` are siblings under `OSError` rather than parent and child, so a read timeout walked past every handler: a stack trace in one environment, a bare tool error in another. Fixed 2026-08-31, with eight assertions and three mutants behind the fix. |
| Release note | Fixed. A read timeout returns a verdict with a remedy instead of a traceback. |

### D-09 — A requirement's acceptance criterion carried a number nobody had measured

**high · fix before release · resolved**

| | |
|---|---|
| Affects | `UR-22` |
| Evidence | `VT-020` |
| Rationale | UR-22 required a ten-result search to fit under 1 kB. The measurement returned 2,240 characters against a 443-chunk index, and the cause was a deliberate feature — every citation gained a heading and a line range. The 1 kB figure had never been measured; it existed only in the row asserting it. The criterion was widened to 5 kB against a measurement, rather than the return being quietly restated to fit. |
| Release note | The context-budget criterion was corrected from an asserted figure to a measured one before release. |

### D-10 — `index_list` omitted the source root its requirement names

**medium · fix before release · resolved**

| | |
|---|---|
| Affects | `UR-12`, `FS-22` |
| Evidence | `VT-015`, `VT-034` |
| Rationale | The tool described an index without naming the folder it was built from, which UR-12's criterion requires — so a caller holding two indexes could not tell which corpus a result came from. Fixed and re-run through a live client. |
| Release note | Fixed. `index_list` reports the source root. |

### D-11 — Tool descriptions and refusal remedies misstated the server's own configuration

**medium · fix before release · resolved**

| | |
|---|---|
| Affects | `UR-20` |
| Evidence | `VT-014`, `VT-031` |
| Rationale | `pull_model`'s description omitted the flag that gates it while `delete_model`'s named its own, so an agent reasoning from the schemas alone concluded a multi-gigabyte download would start; and the delete refusal told every caller to restart one named product. Both fixed, with assertions pairing each gated tool to its own flag and checking that no description or remedy names a particular client. **Stated limit:** the fix is verified by the suite and by a live refusal, not by the reader-level re-read — handing a fresh session the schemas alone and asking which operations are gated — which is the arm that tests the reader rather than the string. |
| Release note | Fixed. Each gated tool names its own flag, and no refusal names a particular client. |

### D-12 — Two shipped behaviours had no test, and a coverage count could not tell that from a bookkeeping gap

**medium · fix before release · resolved**

| | |
|---|---|
| Affects | `FS-20`, `FS-27` |
| Evidence | `VT-042`, `VT-043` |
| Rationale | Incremental reuse by per-file digest and the five-item rerank batch were both implemented and neither was tested. The build test only ever added a file, so it passed with the digest comparison deleted; and the rerank default could be restored to twenty — the value an earlier measurement had already shown produced well-formed scores that did not match their items — without breaking a single assertion. Of twenty-five identifiers a coverage pass flagged, twenty were citations missing from the register and two were behaviours nothing tested. A coverage figure cannot tell those apart, and they need opposite responses. |
| Release note | Both behaviours now carry tests that fail when the behaviour is removed. |

### D-13 — Installing from the README required a substitution the README did not document

**medium · fix before release · resolved**

| | |
|---|---|
| Affects | `UR-01`, `UR-02`, `FS-29` |
| Evidence | `VT-011`, `VT-012W` |
| Rationale | A stranger following the install section on a machine that was not the development host had to substitute a step the document did not mention. Corrected, then re-run to a pass on a Linux and a Windows environment, neither of them the development host. |
| Release note | Fixed. The install path is the one in the README, run from it on two environments that are not the development host. |

### D-14 — The declared Python floor was never exercised

**low · fix before release · resolved**

| | |
|---|---|
| Affects | — |
| Evidence | — |
| Rationale | The 3.10 floor was declared and never tested. It is now declared in the requirements file, enforced at runtime and exercised on 3.10.12. The reason first given for the floor was also wrong: `from __future__ import annotations` defers the annotation that was blamed for it. |
| Release note | Runs on Python 3.10 and is tested there. 3.11 is untested — an accepted gap, not a known failure. |

## Automated — runs with no host  (16)

Executed by `--selftest` and `mutation_check.py`. Each names the exact check labels it relies on, and the generator fails if a label stops being printed.

### VT-001 — The four retrieval tools register, and only when configured

**Result: PASS** · ENV-4, 2026-08-30, Python 3.10.12

| | |
|---|---|
| Discharges | `FS-21`, `FS-22`, `UR-17` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | With no index directory configured the tools are absent and server_info says so; with one, all four register. |
| **Falsifier** | A default clone exposing index_* tools, or a configured server exposing fewer than four. |
| Observed | SELFTEST PASSED. All four labelled checks present. |

### VT-002 — Search returns citations, never chunk text

**Result: PASS** · ENV-4, 2026-08-30, Python 3.10.12

| | |
|---|---|
| Discharges | `FS-23`, `UR-22` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | Every result carries id, path, heading, line range and scores, and no `text` field. |
| **Falsifier** | A `text` key on any index_search result. |
| Observed | SELFTEST PASSED. |

### VT-003 — A stale chunk id is refused, not resolved

**Result: PASS** · ENV-4, 2026-08-30, Python 3.10.12

| | |
|---|---|
| Discharges | `FS-24` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | verdict `stale_id` with a remedy naming index_search; malformed ids separated from stale ones. |
| **Falsifier** | Any id from a different generation returning chunk text. |
| Observed | SELFTEST PASSED. |

### VT-004 — Assertions in the suite can actually fail

**Result: PASS** · ENV-4, 2026-09-11, Python 3.11.15, build `6d3fa8b`

| | |
|---|---|
| Discharges | `FS-28` |
| Environment | ENV-4 |
| Procedure | python mutation_check.py |
| Expected | Baseline green, then every mutant caught. Exit 0. |
| **Falsifier** | A surviving mutant, or a run that reports a score without first proving the baseline green -- an earlier version reported 12/12 while the suite was already red. |
| Observed | baseline green, 32 mutants, 32/32 caught (2026-09-11). Each of the three mutants added that day was caught by the assertion written for it, by name -- reusing a changed file's vectors, letting --rebuild reuse the prior index, and restoring the 20-passage rerank batch. Earlier runs: 20/20 2026-08-30, 23/23 2026-08-31, 29/29 2026-09-11. |

### VT-005 — The declared Python floor is enforced and reachable

**Result: PASS** · ENV-4, 2026-08-30, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-31` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | Both entry points declare 3.10 and the codebase stays parseable on the version it refuses, so the guard can speak. |
| **Falsifier** | 3.10-only syntax anywhere, which would raise SyntaxError before the version check runs. |
| Observed | SELFTEST PASSED on 3.10.12 -- the floor itself, not a proxy for it. |

### VT-006 — Retrieval diagnostics separate discrimination from participation, and recall from ranking

**Result: PASS** · ENV-4, 2026-08-31, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-14`, `UR-15`, `FS-25`, `FS-26` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | A rerank with real spread is not flagged; one where the model scores everything alike IS flagged and the distribution shows it. `explain` returns `recall` for a phrase absent from the corpus and no-failure for one already at rank 1. |
| **Falsifier** | A saturated rerank passing unflagged -- the case that matters, because a count of judged candidates measures participation and looks identical to discrimination. Equally: `explain` returning the same diagnosis for an absent phrase and a present-but-poorly-ranked one, since those need opposite fixes. |
| Observed | SELFTEST PASSED. Also observed live on ENV-5 2026-08-31: a genuinely undifferentiated corpus produced a 0.035 spread and was flagged as such. |

### VT-007 — A call that succeeded and returned nothing useful is distinguishable from one that worked

**Result: PASS** · ENV-4, 2026-08-31, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-18`, `FS-11`, `FS-14` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | Empty output, truncated output and a missing or unrecognised done_reason each return a distinct verdict, none of them `ok`. |
| **Falsifier** | Any of those returning `ok`. Absence of an error is not evidence of a useful answer, and a caller that cannot tell them apart will treat an empty generation as a result. |
| Observed | SELFTEST PASSED. |

### VT-008 — An index can be built over a folder the operator chooses

**Result: PASS** · ENV-4, 2026-08-31, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-05`, `FS-18` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest (end-to-end build group, which builds against a temporary corpus) |
| Expected | A build over an arbitrary folder produces an index whose header records the source root and whose counts match the body. |
| **Falsifier** | A header count that disagrees with the body, or a source root the header does not record -- either makes the index unattributable to the corpus it came from. |
| Observed | SELFTEST PASSED. Also exercised live on ENV-5 2026-08-31 against an operator-chosen folder: 8 files, 33 chunks, header source_root correct, `status` then reported the index matched the corpus. |

### VT-034 — index_list reports source_root from the header

**Result: PASS** · ENV-4, 2026-09-11, Python 3.10.12, build `20e3927`

| | |
|---|---|
| Discharges | `FS-22` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | Each index_list entry carries source_root, equal to the value in that index's header. |
| **Falsifier** | source_root absent from an entry, or different from the header's value. Proven able to fail by the mutation_check.py mutant 'drop source_root from index_list, which UR-12 requires', caught by this label by name (24/24 caught, 2026-09-11). |
| Observed | SELFTEST PASSED 2026-09-11 at 20e3927, 157 checks; mutation check 24/24. |
| Issue | D-10 |

### VT-030 — The guard refuses all three corrupting actions

**Result: PASS** · ENV-4, 2026-08-30, Python 3.10.12, build `554d687`

| | |
|---|---|
| Discharges | `UR-20`, `FS-08`, `FS-09`, `FS-10` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | Refusal on default configuration for pull and delete, and over-length input refused before the call. |
| **Falsifier** | Any of the three proceeding on a default Config(). Note this proves the GUARD, not that a caller ever sees the refusal -- VT-031 covers that. |
| Observed | SELFTEST PASSED. Also exercised on ENV-2 2026-08-29 as part of the selftest run there. |

### VT-032 — A timeout reaches the operator as a stated cause and a remedy, not a traceback

**Result: PASS** · ENV-4, 2026-08-31, Python 3.10.12, build `57a6bab`

| | |
|---|---|
| Discharges | `UR-06`, `FS-17` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest, group 'A timeout is a stated cause, not a traceback'. The assertions fail at the SOCKET, driving the real chain -- _post converts, _score_batch collects, rerank absorbs -- rather than stubbing an intermediate layer. |
| Expected | A read timeout becomes an IndexerError with verdict ollama_timeout and an actionable remedy; a timing-out rerank batch is collected into failures and the search returns embedding order with the reason attached. |
| **Falsifier** | The timeout propagating as TimeoutError, or being reported as ollama_unreachable -- the host answered, slowly, and telling an operator it was unreachable sends them to fix the wrong thing. Proven able to fail by three mutants (23/23 caught, 2026-08-31): disabling the TimeoutError clause, emptying the remedy, and stopping rerank collecting a failed batch. |
| Observed | SELFTEST PASSED 2026-08-31 after the fix. THE DEFECT: urllib.error.URLError and TimeoutError are SIBLINGS under OSError, not parent and child, so a read timeout walked past _post's `except URLError`, past _score_batch's `except IndexerError`, and past rerank()'s per-batch `failures` collector -- whose sole purpose is to absorb exactly this. It reached an operator on ENV-2 as a stack trace, and through a client on ENV-5 as a bare tool error with no verdict, reason or remedy. One defect, two masks; two different reranking models had failed identically first, which sent the diagnosis after the model when the model was never the variable.<br>FIXED: _post now converts TimeoutError and any other OSError; a shared _timed_out() keeps one remedy; all four MCP tools gained a catch-all returning verdict internal_error with the exception class named, so UR-06 holds by construction rather than by enumerating types -- the enumeration is what failed. index_list had NO handler at all, which was the fourth instance found the same day.<br>ALSO CORRECTED: three places claimed the CLI 'has no timeout' -- index_tools.py:347, README.md and the RERANK_POOL comment. All false; _score_batch passes timeout=120. The pool warning sent operators to the CLI BECAUSE of that claim, where the uncaught TimeoutError then crashed. A remedy that relocates the failure is worse than none. |
| Issue | D-08 |

### VT-033 — index_search reports built_at and names the staleness check rather than estimating

**Result: PASS** · ENV-4, 2026-09-11, Python 3.10.12, build `a1f681c`

| | |
|---|---|
| Discharges | `UR-16` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest |
| Expected | index_search returns the index header's built_at unchanged, and its note names `vault_index.py status <name>` -- the command that re-hashes the corpus and prints the rebuild command -- rather than a verdict on freshness. |
| **Falsifier** | built_at missing, defaulted or altered; or a note that estimates staleness instead of naming the status command. Proven able to fail 2026-09-11 by two mutants, one per assertion: built_at hard-coded to 'unknown' at index_search's return, and the status command removed from the staleness note. Each failed its own label and only its own label. |
| Observed | SELFTEST PASSED 2026-09-11 on unmodified source; both labels print PASS. Under each mutant, exactly the targeted label printed FAIL and the suite failed. |

### VT-035 — Every environment variable is documented at its default, and its effect is reported

**Result: PASS** · ENV-4, 2026-09-11, Python 3.10.12, build `e1b0cdb`

| | |
|---|---|
| Discharges | `UR-03`, `FS-04`, `FS-16` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest, group 'Configuration is discoverable without reading source', plus the server_info assertions in 'Tool descriptions answer the questions agents ask of them'. |
| Expected | Every OLLAMA_HOST / OLLAMA_MCP_* name the three modules read appears in README.md, which carries all eight at their defaults in one pasteable block; and server_info reports each one's effect -- host, write_operations, write_gates, model_allowlist, timeout_s, pull_timeout_s, log_level, retrieval_tools. |
| **Falsifier** | A variable read in code and named nowhere in the README -- which is how OLLAMA_MCP_LOGLEVEL came to exist undocumented, and it is the mutant that proves this can fail: reading OLLAMA_MCP_VERBOSITY instead is caught by name (29/29, 2026-09-11). NOT a falsifier: a variable missing from the table but present in the defaults block -- the assertion checks the document, not one row of it. A mutant that deleted the table row SURVIVED for that reason, and was replaced. |
| Observed | SELFTEST PASSED 2026-09-11, 171 checks. THE DEFECT FOUND BY WRITING IT: OLLAMA_MCP_LOGLEVEL was read at ollama_server.py:73 and named nowhere in the README -- the same class the DS generator caught in August, found again only by asking the question mechanically. README now carries the row, and a block of all eight variables at their defaults that a reader can paste unchanged. server_info gained log_level, which was the one variable whose effect the report did not show. |

### VT-042 — A rebuild reuses the files whose content did not change, and only those

**Result: PASS** · ENV-4, 2026-09-11, Python 3.11.15, build `6d3fa8b`

| | |
|---|---|
| Discharges | `FS-20` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest, the end-to-end build group. Builds against a temporary corpus with a stubbed embedder, then rebuilds three ways: incrementally after a file is ADDED, incrementally after a file's CONTENT changes at the same path, and with --rebuild. |
| Expected | The unchanged file is carried forward and is not re-embedded; the file whose content changed is re-embedded even though its path is identical; --rebuild reuses nothing. |
| **Falsifier** | A file re-embedded when nothing changed (which only costs time), or -- the one that matters -- a stale vector carried forward for a file whose content changed. That writes an index whose header is indistinguishable from a correct one and whose answers quietly come from the old text. Proven able to fail by two mutants (32/32 caught, 2026-09-11): comparing `if prior:` instead of the digest is caught by name, and dropping the --rebuild guard is caught by name. |
| Observed | SELFTEST PASSED 2026-09-11, 176 checks. Reuse was implemented on 2026-08-26 and unverified until now: the existing build group only ever ADDED a file, and an add-only test passes with the digest comparison deleted. The mutant confirms it -- `if prior:` survives every other assertion in the suite. |

### VT-043 — Reranking batches five passages at a time, because the batch size is a correctness parameter

**Result: PASS** · ENV-4, 2026-09-11, Python 3.11.15, build `6d3fa8b`

| | |
|---|---|
| Discharges | `FS-27` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest, group 'Retrieval: reranking is batched small enough to stay correct'. Twelve candidates are reranked against a stub that reports how many passages each call carried. |
| Expected | Three calls of at most five passages each; all twelve candidates scored. |
| **Falsifier** | A default batch above five. Twenty candidates in one ~8,300-token call returned well-formed scores that did not correspond to the passages -- a NAS chunk rated a direct answer to a GPU question -- so nothing looks broken when this breaks. Proven able to fail (32/32 caught, 2026-09-11): restoring `batch_size: int = 20` is caught by name. |
| Observed | SELFTEST PASSED 2026-09-11, 176 checks. The default was written after the 2026-08-26 measurement and then protected by nothing: raising it back to 20 broke no assertion in the suite until this test existed. That is the register's own doctrine -- a check never seen to fail is not yet a check -- applied to a parameter this project already knows is load-bearing. |

### VT-050 — Oversized input is refused before the call, and proceeding anyway has to be asked for

**Result: PASS** · ENV-4, 2026-09-11, Python 3.11.15, build `6d3fa8b`

| | |
|---|---|
| Discharges | `FS-13` |
| Environment | ENV-4 |
| Procedure | python ollama_server.py --selftest, group 'Context budgeting'. The limits used are real ones read off a live installation (2,048 for nomic-embed-text, 262,144 for gemma4:12b), so the arithmetic is exercised against numbers the system actually meets. |
| Expected | An input the model cannot read is refused before the request is sent, with the estimate and the limit both reported; the call proceeds only when allow_truncation is passed explicitly; an unknown limit is reported as unknown rather than assumed generous. |
| **Falsifier** | An oversized call being sent and silently truncated by the server, or an unknown context limit treated as unlimited. Both produce an answer computed from part of the input, which is the failure mode that looks like a working call -- the operator gets a confident reply to a question the model never saw in full. A pessimistic token estimate is deliberate for the same reason: under-estimating loses input, over-estimating only refuses. |
| Observed | SELFTEST PASSED 2026-09-11, 176 checks. The group's own history is the case for it: the stub cache initially bypassed ContextCache.limit_for entirely, so the num_ctx parsing was untested until mutation testing said so, and the cache's own checks were added afterwards. |

## Scripted — a command and its output  (4)

Reproducible by anyone with the repository and, where noted, a host.

### VT-020 — A 10-result search fits the stated context budget

**Result: PASS** · ENV-1, 2026-08-30, Python 3.14.4, build `unknown — pre-commit working tree`

| | |
|---|---|
| Discharges | `UR-22` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | index_search with k=10 against a 400+ chunk index; measure the serialised response. |
| Expected | Under 5 kB for a 10-result search against a 400+ chunk corpus. Widened from 1 kB by owner ruling 2026-08-30 -- see `observed` for why the original figure was never sound. |
| **Falsifier** | A 10-result search exceeding 5 kB. Per-citation cost is the durable figure: at K_MAX=25 the same ~224 chars each would reach ~5,600 chars, so this criterion is bound to k=10 by construction and the response byte cap is what bounds the rest. |
| Observed | 2,240 chars (~224 per citation) for k=10 against the 443-chunk index, ENV-1, 2026-08-30. Comfortably inside 5 kB. THE ORIGINAL 1 kB FIGURE WAS NEVER MEASURED: it appears nowhere except the requirement row that asserts it, the row's method was `A` (analysis) while its text read 'Measured return size', and it most likely came from the design note's ~300-token ESTIMATE, written before format v2 added a heading and line range to every citation. |
| Issue | D-09 |

### VT-026 — Every requirement identifier cited in a published document resolves within the published set

**Result: PASS** · ENV-4, 2026-09-12, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-32` |
| Environment | ENV-4 |
| Procedure | Extract every UR-/FS- identifier cited anywhere in docs/*.md, extract every identifier DEFINED in a register row there, and report any cited identifier that no published document defines. Run as a link check, not a judgement. |
| Expected | Zero unresolved identifiers. |
| **Falsifier** | Any citation to an identifier that exists only in the vault. That is the specific failure of publishing from a working copy: a trace that resolves for the author and dead-ends for every reader. |
| Observed | Run 2026-08-31 against the published set: 62 identifiers cited, 62 defined, zero unresolved.<br>RE-RUN 2026-09-12 AT WIDER SCOPE: publishing a new document changes what this test covers, so it was re-run with VERIFICATION.md in the published set and URS_FS.md republished. 62 identifiers cited, 62 defined, zero unresolved. The VR introduced no citation that dead-ends for a reader -- its own defect identifiers are defined in the document that cites them. |

### VT-048 — The delegation skill changes what a caller does, measured against a control arm

**Result: deferred** · ENV-1, 2026-09-12, build `34ff28a`

| | |
|---|---|
| Discharges | `UR-23` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | A skill eval with a control arm that is CAPABLE of the behaviour being suppressed: run the same tasks with and without the skill loaded and compare delegation rates. |
| Expected | A measurable difference in delegation rate between the arms. |
| **Falsifier** | No difference -- which would mean the skill is documentation and not an instrument. The trap this must avoid is a control arm that could not have delegated anyway: that produces a tied result which reads as 'no effect' and measures nothing. One such result has already been produced here, and one claimed effect measured at a tenth of its asserted size. |
| Observed | RAN 2026-09-12 with the iteration-1 defect fixed: the control arm was told the tools exist and what each does, so it was capable of the behaviour under test. RESULT IS A NULL. Control delegated on 1 of 3 cases, treatment on 1 of 3, and the arms diverged on NONE. The criterion -- a difference in delegation rate -- did not occur.<br>CASE 0 (80 titles, closed vocabulary, delegating is correct): both arms delegated, 7/7 assertions each. The control, given only an accurate description of the tools, called list_models(include_details=true), chose gpt-oss:20b, confirmed location local, batched at 10, temperature 0. A capable caller routes this correctly without guidance.<br>CASE 2 (six-item prioritisation, delegating is wrong): both arms declined, 4/4 each.<br>CASE 1 IS VOID AND THE FAULT IS IN THE CASE. The corpus is 1,841 bytes, so declining is the correct call and no arm could have shown a difference. That is iteration 1's error reproduced in a new place -- an assertion incapable of failing -- and found the same way, by inspecting a tied result rather than by any check.<br>WHERE THE ARMS DID DIFFER, AND IT IS NOT THE CRITERION: execution quality. The control's first four calls all failed on json_mode with gpt-oss:20b. The treatment piloted gemma3:4b on 10 items, got verdict ok, done_reason stop, perfect format and THREE WRONG LABELS, and escalated on that measurement. Guidance changed how well it delegated, not whether it did.<br>STATED LIMIT: both arms are agent sessions with the tools reachable through the MCP bridge and the guidance injected as prompt text. This tests the guidance's CONTENT, not its TRIGGERING by description match in a real client.<br>Full workspace -- fixtures, both briefs verbatim, grader, six raw runs -- at 40_Test_and_Evals/Attachments/skill_eval_iteration_2_workspace.tar.gz. Recorded in Skill_Delegation_Evals_Iteration_2.md.<br>RE-RUN 2026-09-12 with hashed stimuli: four cases, both arms, arms diverged on NONE. The fourth case was built specifically to discriminate -- 24 incident reports, a naive single batch would be ~7,000 tokens -- and both arms batched at 4 unprompted and scored 24/24 aligned by tag. THIRD NULL. Recorded honestly: the criterion was restated only after this, and the argument for restating it does not depend on the results -- the tool descriptions were in both arms from the first run -- but it was the third null that made anyone look. |
| Issue | D-01 |
| Release decision | Deferred past v1.0.0. Ran three times with a control arm capable of the behaviour under test, and returned a null every time. The cause is structural, not a shortage of cases: the control always holds the tool descriptions, which are themselves guidance supplied with the system, so the skill's MARGINAL contribution cannot be isolated without testing a product that is not shipped. UR-23 is discharged by VT-051 on what the runs DID establish. The marginal question is real and is tracked as a deferred issue. |

### VT-051 — A caller determines when delegation is appropriate, in both directions

**Result: PASS** · ENV-1, 2026-09-12, build `34ff28a`

| | |
|---|---|
| Discharges | `UR-23` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | A four-case plan against the published skill. Four tasks: two where delegating is correct (80-title closed-vocabulary classification; 24 long incident reports) and two where the correct action is something else (a six-item judgement prioritisation; a reasoning model needing a budget that fits its thinking). Stimulus assembled and hashed by build_prompt.py; fixtures pinned. |
| Expected | The caller delegates the bulk work, declines the judgement work, and budgets for the reasoning model -- correct routing in both directions, from the guidance the system supplies. |
| **Falsifier** | Routing wrong in either direction: delegating the judgement task, or grinding through 80 titles and 24 reports inline. Either would show the system does not convey when delegation is appropriate. NOTE this does NOT test the skill's marginal contribution over the tool descriptions -- see D-01 -- it tests that the system as shipped conveys it. |
| Observed | PASSED 2026-09-12 across four cases and both arms -- eight runs. Bulk work delegated to a local model in every arm that faced it; the judgement task declined by every arm with a stated reason; 4000-token budgets set for the reasoning model by every arm; 24/24 aligned by tag on the batch-size case. Every prompt hashed, fixtures verified against MANIFEST.json before the run. Each run is recorded with its prompt hash and its evidence. |

## Inspection — settled by reading, not by running  (9)

A structural claim is verified by reading the code. Executing something would prove less, not more: a passing call shows one path worked, not that only one path exists.

### VT-021 — One transport call site, so a second backend is a single-function change

**Result: PASS** · ENV-4, 2026-08-30, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-08` |
| Environment | any |
| Procedure | Read the call graph: grep for urlopen across the source, and count callers of _request(). |
| Expected | Exactly one urlopen call site per file; _request() defined once and called from every tool that reaches the network. |
| **Falsifier** | A second urlopen, or any tool building its own request. Executing a call would prove LESS -- a passing call shows one path worked, not that only one path exists. |
| Observed | Verified by inspection 2026-08-28 and unchanged since: one urlopen per file, _request() called from 13 tools. Owner ruling 2026-08-30: the requirement is good as written and inspection is the legitimate method here. |

### VT-022 — The README carries the claims the URS makes about it

**Result: PASS** · ENV-4, 2026-09-11, build `1e7a2d3`

| | |
|---|---|
| Discharges | `UR-24`, `UR-25`, `UR-26`, `UR-28` |
| Environment | any |
| Procedure | Read README.md and check four specific claims, each falsifiable by reading:<br>1. UR-24 -- the first screen states what the system is FOR and does not lead with cost.<br>2. UR-25 -- a named non-goals section exists AND carries the accountability statement.<br>3. UR-26 -- the ANTHROPIC_BASE_URL question is answered by name, and Claude Code is distinguished from Cowork rather than treated as one client.<br>4. UR-28 -- LICENSE exists at the repository root, is named in the README, and the README states the licence covers the specification and verification documents, not only source. |
| Expected | All four present as described. |
| **Falsifier** | Any of the four absent, or present without the specific statement named. In particular: a non-goals section that lists technical limits but omits who remains accountable, which is the claim that matters and the easiest to lose in an edit. |
| Observed | Verified by inspection 2026-08-31 against the current README. UR-24: 'What it is for' opens the document, leads with capability, and states explicitly that cost is the weakest of the three arguments. UR-25: 'Assumptions and non-goals' present, with 'What this does not unlock' and the accountability statement. UR-26: 'Why not just point ANTHROPIC_BASE_URL at Ollama?' answers by name, and SINCE 1e7a2d3 (2026-09-11) the section states that the route redirects Claude Code and does not redirect Claude Desktop or Cowork, whose endpoint comes from the app's own configuration. UR-28: LICENSE at root, MIT, named in README with the scope sentence.<br>NOTE: UR-25, UR-26 and UR-24 stood at a failing or partial mark in the requirements register while the README already satisfied them. The register was behind the artefact, not the other way round.<br>CORRECTED 2026-09-11: this entry previously recorded UR-26 as met because 'Claude Code is named as a first-class client separately from Cowork'. THE README DID NOT SUPPORT THAT -- it never mentioned Cowork at all, and the section said 'Claude's clients are protocol clients', which is the opposite of the distinction UR-26 asks for. A passing inspection asserted a distinction that was not on the page. Found while settling how this server is positioned against the official gateway route, from an owner observation that Claude Desktop launched from a shell still reaches Anthropic. Re-inspected against 1e7a2d3, where the two sentences now exist. |

### VT-023 — The design specification names what was chosen, and cites a decision for each constraint

**Result: PASS** · ENV-4, 2026-08-31, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-07`, `UR-10` |
| Environment | any |
| Procedure | Read docs/DESIGN.md. UR-07: can a reader list the technology stack -- transport, language, HTTP client, index storage -- without opening source? UR-10: does each constraint named in the non-goals cite a decision identifier rather than asserting itself? |
| Expected | The stack is nameable from the DS alone; every named constraint carries a decision reference. |
| **Falsifier** | A constraint stated without a decision behind it. That is the failure mode that matters: an incidental implementation detail and a deliberate limit read identically once the reason is missing, and the reader cannot tell which they are allowed to change. |
| Observed | Verified by inspection. The DS names stdio MCP, Python, stdlib urllib and a flat-file index at a level a builder could restate without reading source; constraints in the non-goals cite AD- decisions. |

### VT-024 — No network destination other than the configured inference host

**Result: PASS** · ENV-4, 2026-08-31, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-30` |
| Environment | any |
| Procedure | Read the call graph: every urlopen site, and the derivation of every URL passed to it. |
| Expected | One transport function; every URL it builds derives from config.base_url. |
| **Falsifier** | A URL literal, or any request whose host does not come from config. Packet capture would prove less: a capture shows what happened during one run, not that no other destination is reachable. |
| Observed | Verified by inspection of the call graph. Single _request() transport; all URLs derived from config.base_url. Disproportionate to instrument further at this risk level -- the failure would be a user's content reaching an unintended host, which the call graph settles. |

### VT-025 — A completed call reports what it cost, with load time separated from generation rate

**Result: PASS** · ENV-4, 2026-08-31, Python 3.10.12

| | |
|---|---|
| Discharges | `UR-33`, `FS-15` |
| Environment | any |
| Procedure | Read the response-shaping code: confirm eval_count and tokens_per_second are emitted when Ollama reports them, and that model load time is a SEPARATE field rather than folded into the rate. |
| Expected | eval_count, tokens_per_second and a distinct load-time field. |
| **Falsifier** | Load time folded into the throughput figure. That single choice makes a cold load read as a slow model, which is the specific misreading this requirement exists to prevent -- and it is exactly the trap this project fell into when it attributed a rerank timeout to the pool size rather than the load. |
| Observed | Verified by inspection: eval_count and tokens_per_second computed from eval_duration; load_duration reported separately. |

### VT-044 — `chat` budgets the whole message history, not the newest turn

**Result: PASS** · ENV-4, 2026-09-11, Python 3.11.15

| | |
|---|---|
| Discharges | `FS-06` |
| Environment | any |
| Procedure | Read the chat tool's call site: confirm the token estimate passed to _check_context is computed over the concatenated content of EVERY message, not over messages[-1]. |
| Expected | The estimate is built from a join across the whole list before the guard runs. |
| **Falsifier** | An estimate computed from the newest message. The whole history is re-sent on every turn, so budgeting only the last one lets a long conversation overflow the model's context while each individual turn looks small -- the failure is silent and arrives late. |
| Observed | Verified by inspection 2026-09-11: ollama_server.py joins `str(m.get("content", ""))` across all messages and passes that to _check_context, under a comment stating the whole history is re-sent every turn. The budgeting arithmetic it feeds is asserted separately by the 'Context budgeting' group (VT-030). |

### VT-045 — The declared dependency set is one direct package, and the retrieval tool adds none

**Result: PASS** · ENV-4, 2026-09-11, Python 3.11.15

| | |
|---|---|
| Discharges | `UR-29` |
| Environment | any |
| Procedure | Read requirements.txt and the import block of vault_index.py. |
| Expected | Exactly one direct runtime dependency, with its forcing reason stated; vault_index.py imports stdlib only. |
| **Falsifier** | A second direct dependency, or a third-party import in vault_index.py. Either would mean the retrieval half cannot be run without the MCP stack, which is the property that lets it be a CLI operation. |
| Observed | Verified by inspection 2026-09-11. requirements.txt names one package, `mcp`, unpinned, with its forcing reason recorded ('being an MCP server') and an explicit record of what was NOT taken and why (requests/httpx, against stdlib urllib). vault_index.py imports argparse, hashlib, json, math, os, re, sys, urllib.error, urllib.request, datetime, pathlib, typing -- stdlib only, no third-party name. |

### VT-046 — The skill ships with the server, and the shipped copy is the vault copy

**Result: PASS** · ENV-4, 2026-09-11, Python 3.11.15

| | |
|---|---|
| Discharges | `FS-30` |
| Environment | any |
| Procedure | Extract the fenced skill from 30_Design/_SKILL_Working_Copy.md with publish_skill.py's own extractor and compare it byte-for-byte against skills/local-inference-delegation/SKILL.md in the repository. `python publish_skill.py --note 30_Design/_SKILL_Working_Copy.md --check` is the same comparison from the command line. |
| Expected | The repository carries skills/local-inference-delegation/SKILL.md and it is identical to the vault extract. |
| **Falsifier** | Any difference. The skill is edited in the vault and published to the repository, so a repository copy that has drifted is the shipped artefact being older than the reviewed one -- and it is the failure mode this write path was built to remove, after 333 lines came to exist only in one application's storage. |
| Observed | Verified 2026-09-11: vault extract and repository copy both 392 lines, 20,193 bytes, sha256 e3dade2f74ab. Identical. |

### VT-049 — The Verification Report publishes, carrying a release decision per known defect

**Result: PASS** · ENV-4, 2026-09-12, build `34ff28a`

| | |
|---|---|
| Discharges | `UR-27` |
| Environment | any |
| Procedure | Publish the VR and read it as a stranger: for every defect that ships, is there a stated release decision, and are the limits of what was verified stated rather than implied? |
| Expected | A published VR with a defect triage register and a decision per entry. |
| **Falsifier** | A published VR that reports only passes. A reader cannot tell 'nothing failed' from 'failures were not written down', and this register has already contained a tick that no test supported and an inspection record whose claim was false. |
| Observed | PASSED 2026-09-12. The VR now renders a defect triage register -- 14 entries, each with a severity, a decision from the three-value vocabulary, a status, a rationale and release-note text -- and it PUBLISHED: Publish-Document.py wrote docs/VERIFICATION.md clean, where before it refused.<br>WHAT THE REGISTER FIXED, AND IT WAS NOT ONLY THE TABLE. The report cited seven internal issue identifiers that resolve in no published document. They are now rewritten to the public D-nn the report itself defines, from the register's own mapping; an identifier with no entry is left alone so the scan still refuses on it. The remaining internal provenance -- host paths, a username, run records belonging to another project -- ships to the vault copy inside VAULT ONLY markers and is stripped from the published one: 24 regions.<br>READ AS A STRANGER, AND THAT FOUND A DEFECT THE GATES DID NOT. Twelve rows carried a newline inside a table cell, which ends the row: the remainder rendered as loose paragraph text under a broken table, and had been doing so in every version of this report. The generator now renders a cell as one line. Nothing in --check or --release-gate could see it, because both read the manifest and neither reads the rendered document.<br>PROVENANCE: rendered and published at 6d3fa8b plus the then-uncommitted generate_verification.py change that adds the register, and re-rendered at 9181732 and again at 34ff28a, which added build provenance to the rendered header. run_on.build NAMES THE GENERATOR COMMIT THAT PRODUCED THE PUBLISHED DOCUMENT, not the one the work was drafted against. This stamp is stable: 34ff28a is the last commit that changed the generator, so re-rendering from here reproduces the same document. |

## Manual — live host, specific client, or a human  (11)

Written so someone who was not present can run them.

### VT-010 — `generate` applies the model's chat template

**Result: PASS** · ENV-1, 2026-08-30, Python 3.14.4, build `554d687`

| | |
|---|---|
| Discharges | `FS-05` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | Through a connected MCP client, call THIS SERVER'S `generate` tool (not `ollama run`, not direct HTTP -- FS-05 is a claim about what our server posts). Three arms, identical prompt, temperature 0, max_tokens 2000: (1) `generate` with gemma3:4b; (2) `chat` with gemma3:4b as the control, since chat already uses /api/chat; (3) `generate` with a second Gemma-family model. Compare prompt_eval_count across (1) and (2). |
| Expected | Normal prose, and prompt_eval_count identical between the generate and chat arms. Equal prompt tokenisation means the template was applied on both -- a template adds turn markers, and turn markers are tokens. |
| **Falsifier** | Reserved vocabulary such as <unused50>, empty content with a normal done_reason, or a prompt_eval_count that DIFFERS between the two arms. Note the third: it detects a skipped template whatever endpoint is called and whatever Ollama does next, which a check on the URL cannot. |
| Observed | Four independent paths at temperature 0 -- generate and chat through the tool surface, and both endpoints by direct HTTP -- returned BYTE-IDENTICAL text with prompt_eval_count 26 and eval_count 45 on every one. gemma4:12b clean at eval_count 565. No reserved vocabulary, no empty content. The template is applied on both endpoints on Ollama 0.33.2. |

### VT-011 — Install from the README on a machine that is not the development host

**Result: PASS** · ENV-2, 2026-08-31, Python 3.12.3, build `554d687`

| | |
|---|---|
| Discharges | `UR-01`, `UR-02`, `FS-29` |
| Environment | ENV-2 · **needs a live host** |
| Procedure | Clean checkout on ENV-2. Follow the README verbatim, as literally as a stranger would, with no substitutions from memory. Then --selftest and --probe. |
| Expected | Server starts and enumerates its tools; --selftest exits 0; --probe names the host and lists models. No step requires a value not printed in the manual. |
| **Falsifier** | Any step needing a value not in the README. It failed exactly this way twice on 2026-08-29 -- interpreter path, then python.exe surviving into a POSIX client config. |
| Observed | PASSED on ENV-2, 2026-08-31, re-run against the corrected README after the two 2026-08-29 failures.<br>venv created; `pip install mcp` resolved mcp 2.1.1 in 28 packages; --selftest exited 0 with every group green including the retrieval assertions; --probe reached the host and listed 6 models with the local/cloud split and the leaves-the-host warning. NO STEP REQUIRED A VALUE NOT PRINTED IN THE MANUAL -- which is the criterion, and it is the first time this environment has met it.<br>The 2026-08-29 defects are therefore re-proven fixed, not merely fixed: interpreter path, and python.exe surviving into a POSIX client config.<br>vault_index.py build/search/status all ran from the documented commands: 18 files, 50 chunks; `status` then correctly reported 4 added files by name and printed the rebuild command.<br>SCOPE: this records the INSTALL path only. Three defects found during the same session are separate findings and are not VT-011 failures -- see the rerank-through-MCP fault and the unsearchable-identifier finding raised from this run. |
| Issue | D-13 |

### VT-012 — `status` names a file whose content changed

**Result: PASS** · ENV-1, 2026-09-11, build `a1f681c`

| | |
|---|---|
| Discharges | `FS-19`, `UR-16` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | Build an index. Modify the CONTENT of an indexed source file. Run `vault_index.py status <name>`. |
| Expected | The modified file is listed under `changed`, exit code 1. |
| **Falsifier** | A changed file reported as unchanged, or staleness estimated from a timestamp rather than re-hashed. |
| Observed | PASSED at build a1f681c. CHANGED PATH (2026-09-11): after a line was appended to one.md, `status p058tc003` printed '3 indexed, 3 on disk: 1 changed, 0 added, 0 removed' and 'changed  one.md', followed by the exact rebuild command, and exited 1. two.md and three.md were not listed.<br>TIMESTAMP ARM OF THE FALSIFIER (2026-09-09, same build): two.md's LastWriteTime was moved from 20:03:07 to 20:12:42 with Length unchanged at 59, and status still named only one.md, exit code 1. status re-hashes content; it does not read mtime.<br>WHY TWO RUNS ARE CITED: the 2026-09-11 run touched two.md in a non-proving, uncaptured step, so its 'touched file NOT reported' output is identical to its 'change detected' output and shows nothing on its own. The earlier run captured the touch. |

### VT-012W — Install from the README on a Windows machine that is not the development host

**Result: PASS** · ENV-5, 2026-08-31, Python 3.14, build `554d687`

| | |
|---|---|
| Discharges | `UR-01`, `UR-02`, `FS-29` |
| Environment | ENV-5 · **needs a live host** |
| Procedure | On ENV-5: a Windows host with Python and Ollama already present, no prior copy of this project. Follow the README's Windows/PowerShell path from the top, verbatim, as literally as a stranger would. No substitutions from memory, nothing carried from another machine.<br>STEP 0: confirm the target path is on LOCAL storage, not a mapped network drive -- `Get-PSDrive <letter>`. Added 2026-08-31 after a run whose drive letter resolved over SMB to the development host and cloned on top of the live repository.<br>STEP 1: record the Windows and Python versions before starting.<br>Then: obtain the code, venv, install the dependency, --selftest, --probe, wire the client, build an index with the PowerShell commands as printed, and call index_list and index_search through the client with NO --dir argument. |
| Expected | Every step runs as written. No command needs translating from the bash examples, and no step requires a value the README does not print. |
| **Falsifier** | Any step that has to be adapted. THE KNOWN CLASS: an instruction given only in bash form with no PowerShell equivalent -- the mirror image of the two POSIX defects that failed VT-011.<br>NOT A FALSIFIER: the reranker failing to load or return. How fast Ollama runs a given model on given hardware is outside this project's scope. Reranking is optional; the search falls back to embedding order and reports it. Record the behaviour as guidance. |
| Observed | PASSED, having first FOUND ITS OWN KNOWN CLASS OF DEFECT and then re-proven the fix.<br>Clean clone from a bundle to a local path; venv; `pip install mcp` resolved mcp 2.1.1 in 29 packages, matching the README's stated 'around thirty'; --selftest exited 0 with every group green; --probe listed the host's models and labelled the stub entries `cloud?` rather than local, which is the selftest's own assertion firing on real data it was not written against.<br>DEFECT FOUND: the index directory was documented only as a bash `export`. On PowerShell that is CommandNotFoundException, and the operator had to adapt the step by passing --dir. This is exactly the falsifier's named class.<br>ROOT CAUSE: the PowerShell form had been written on 2026-08-30 and was DESTROYED the same morning when a clone landed on the development host's working tree. The register recorded the defect as already fixed; the fix no longer existed. The test found it anyway, which is the case for the test.<br>FIXED AND RE-PROVEN IN SESSION: README now carries both forms plus an explicit note that `export` is not PowerShell and that shell variables do not reach the MCP client. Operator re-ran with `$env:OLLAMA_MCP_INDEX_DIR` and confirmed --dir was no longer needed.<br>SECOND DEFECT, CONFIGURATION: the client's env block named another user's home directory, carried over from the development host. Corrected to a `~`-relative path, which index_tools.register() expands via Path().expanduser(); verified by inspection and then live.<br>index_list and index_search answered through the MCP client with no --dir. The search correctly reported it could not answer from the indexed corpus, gave the 0.035 spread as undifferentiated, and separated filename inference from a confirmed answer.<br>HOST CAPABILITY: embeds fine, cannot carry a reranker. gpt-oss:20b absent; qwen2.5:7b did not return inside the client timeout; searches ran on cosine alone and `reranked_by.failures` said so. Out of scope as performance, recorded as prerequisite guidance. |

### VT-013 — Two indexes coexist without leaking into each other

**Result: PASS** · ENV-1, 2026-09-09, build `a1f681c`

| | |
|---|---|
| Discharges | `UR-11`, `UR-13` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | Build two indexes over disjoint folders, each containing a marker term absent from the other. Search each index for the OTHER index's marker. |
| Expected | A search of one returns no chunk originating in the other, in both directions. |
| **Falsifier** | Any result whose path lies outside the searched index's source root. |
| Observed | PASSED at build a1f681c, 2026-09-09. Two indexes over disjoint two-file folders: p058a (a1.md carries ZORBIFAX; generation adfcbe5f3a9c) and p058b (b1.md carries QUELPRAND; generation 8c429f7742ea). Searching p058a for QUELPRAND returned a1.md and a2.md only; searching p058b for ZORBIFAX returned b1.md and b2.md only. The default --top of 5 exceeds the four chunks across both indexes, so a search that leaked would have returned the other index's chunks. None appeared in either direction. |

### VT-014 — A refusal reaches the operator through a second MCP client

**Result: PASS** · ENV-3, 2026-08-31, build `554d687`

| | |
|---|---|
| Discharges | `UR-20` |
| Environment | ENV-3 · **needs a live host** |
| Procedure | From ENV-3, call delete_model with the write gate unset and record the whole response. Record ALSO whatever the client does next, including any attempt to reach the same outcome by another route. |
| Expected | verdict `not_permitted` with an error and a remedy, visible to the operator, through a client that is not Claude Desktop. |
| **Falsifier** | A deletion that proceeds. A refusal the client swallows so the operator never sees it. Or a remedy that names the wrong client. |
| Observed | 2026-08-31, ENV-3. delete_model('nomic-embed-text:latest') returned verdict `not_permitted` with error and remedy, rendered to the operator. THE REFUSAL PATH IS REACHABLE AND VISIBLE THROUGH A NON-CLAUDE CLIENT.<br><br>TWO FINDINGS THE ARM WAS NOT LOOKING FOR:<br><br>1. THE CLIENT IMMEDIATELY ATTEMPTED A ROUTEAROUND. Having been refused by the tool, it ran `ollama rm nomic-embed-text:latest` as a shell command. That was denied at the human approval prompt. NOTHING IN THIS SYSTEM STOPPED IT -- the operator did. Our gate governs the TOOL, not the CAPABILITY.<br><br>2. THE REMEDY NAMED THE WRONG CLIENT. It told a Gemini user to 'restart Claude Desktop'. D-11's second finding, raised by inspection earlier the same day, observed live in front of the user it misleads. |
| Issue | D-11 |

### VT-015 — index_list identifies each index's purpose and provenance without opening it

**Result: PASS** · ENV-1, 2026-09-11, build `20e3927`

| | |
|---|---|
| Discharges | `UR-12` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | With at least two indexes in the configured index directory, one of them lacking an optional header field, call index_list through an MCP client. |
| Expected | index_list reports name, description, source root, build time and model for each index. The index lacking a field reports `unknown` for it, never a value. |
| **Falsifier** | An index present in the directory but absent from the list; any listed field carrying a value its header does not contain; or a field the criterion names absent from an entry. |
| Observed | PASSED 2026-09-11 at 20e3927, after the desktop app was restarted so the server loaded it. index_list, called through the client, listed all four indexes, each with name, description, source_root, built_at and model. source_root matched the folder each index was built from, and distinguished them: two indexes built from two different pilot corpora reported their own separate roots, and a third index built from the first corpus reported that same root. vt015-nodesc, built without --describe, reported description 'unknown'.<br>FIRST RUN FAILED, same day at a1f681c: every field except source_root was present, and the missing-field rule already held. Raised as D-10, fixed in 20e3927, re-run here. A call made after the commit but BEFORE the restart still returned no source_root -- the server serves the code it loaded at start. |
| Issue | D-10 |

### VT-031 — Can a refusal be reached, and seen, through a live client?

**Result: PASS** · ENV-1, 2026-08-31, Python 3.14.4, build `554d687`

| | |
|---|---|
| Discharges | `UR-20`, `UR-06` |
| Environment | ENV-1 · **needs a live host** |
| Procedure | Through a connected MCP client with OLLAMA_MCP_ALLOW_PULL and OLLAMA_MCP_ALLOW_DELETE unset. Record each whole response, AND record when no call was made at all.<br><br>ARM A -- pull_model, with the agent's pre-check bypassed explicitly. Say verbatim:<br>  "Call the pull_model tool directly without reading server_info first. I want to see the raw refusal the server returns. Do not check whether it is enabled."<br>  ENV-2 2026-08-29 showed a plain request short-circuits: the agent read server_info and answered in the FUTURE TENSE -- 'the tool will refuse' -- without calling it.<br><br>ARM B -- delete_model. EXPECT THIS ARM TO BE UNREACHABLE. A Claude client refuses destructive actions on its own policy independently of the gate, which ENV-2 also recorded. Attempt it, and record the refusal's ORIGIN: the client's policy, or our verdict. Do not treat client-side refusal as evidence about our guard.<br><br>ARM C -- embed, model nomic-embed-text, one input of at least 50,000 characters. No server_info flag governs this, so the agent has no basis to pre-check and the call should actually reach us. THIS IS THE ARM MOST LIKELY TO PRODUCE EVIDENCE. |
| Expected | Arm C returns verdict `context_exceeded` BEFORE any model call, naming the estimate and the limit -- 'before execution' is the load-bearing phrase in UR-20 and arm C is what tests it. Arm A returns `not_permitted` with a remedy naming the environment variable. Arm B may be refused by the client before reaching us. |
| **Falsifier** | A pull or delete that PROCEEDS. An over-length input that reaches the model and returns a truncated result. AND THE NON-OBVIOUS ONE: if arm A still cannot reach the guard even with the pre-check bypassed, that is not a failed test -- it establishes that the refusal path is unreachable end to end through this client, which is a finding about our surface and belongs in the VR as one. Record it as the result, not as an absence. |
| Observed | ALL THREE ARMS REACHED THE SERVER. The expectation that the refusal path is unreachable through a well-behaved client is OVERTURNED.<br><br>ARM A pull_model -> verdict `not_permitted`, error and remedy naming OLLAMA_MCP_ALLOW_PULL. The gate fires ahead of any registry lookup; nothing downloaded. The tester first predicted from the tool descriptions that NO gate existed and a multi-gigabyte download would start, then corrected itself.<br><br>ARM B delete_model -> same `not_permitted` shape with OLLAMA_MCP_ALLOW_DELETE named. The client did NOT refuse on policy: it established the named model was not installed, so nothing could be destroyed, then called.<br><br>ARM C embed, 1 input of 73,789 chars -> verdict `context_exceeded` BEFORE any model call. Named the estimate (23,060 tokens), the limit (2,048), which input was oversized, a remedy, that the estimate IS an estimate because Ollama exposes no tokenizer, and the model_info/num_ctx disagreement with the lower value taken. This is UR-20's 'before execution' clause, evidenced. |
| Issue | D-11 |

### VT-040 — ENV-3 enumerates the tools and renders `location` on every model

**Result: PASS** · ENV-3, 2026-08-31, build `554d687`

| | |
|---|---|
| Discharges | `UR-04`, `UR-19`, `UR-21`, `FS-01`, `FS-12` |
| Environment | ENV-3 · **needs a live host** |
| Procedure | From the second MCP client, with the server configured as in ENV-1, issue verbatim: "Call the list_models tool and show me the raw result." Paste the complete response object, not the client's rendering of it. |
| Expected | verdict `ok`, every model carrying a `location` of local, cloud or cloud?, and the count matching `ollama list` on the host. |
| **Falsifier** | Any model row without a `location`. A `cloud`-tagged model reported as local. A count that disagrees with the host. Or a client that cannot enumerate the tools at all, which would falsify the portability claim outright. |
| Observed | 2026-08-31. verdict `ok`, host http://localhost:11434, 18 models — 14 local, 4 cloud. `location` present and correct on every row: the four `:cloud`-tagged models rendered as cloud, the rest local, including the slashed hf.co and user-repo names. No row reported `cloud?`, consistent with the Threat Model's note that no model in this inventory currently triggers the disagreement path.<br>PROVENANCE CAVEAT: recorded from the client's rendered summary, not the raw response object. The raw JSON is to be pasted into the evidence note. |

### VT-041 — ENV-3 completes a real model call and reports where it ran

**Result: PASS** · ENV-3, 2026-08-31, build `554d687`

| | |
|---|---|
| Discharges | `UR-04`, `FS-07` |
| Environment | ENV-3 · **needs a live host** |
| Procedure | From the second MCP client, issue verbatim: "Call the embed tool with model nomic-embed-text and a single short input, and show me the raw result." Paste the complete response object. |
| Expected | verdict `ok`, `location: local`, one vector per input, 768 dimensions for nomic-embed-text. FS-07 requires the output count to equal the input count. |
| **Falsifier** | A dimension count other than 768 for this model. More or fewer vectors than inputs. A missing or wrong `location`. Any of these through a second client when they hold on ENV-1 would indicate the response shape is client-dependent. |
| Observed | 2026-08-31. verdict `ok`, model nomic-embed-text:latest, location `local`, 1 input, 768 dimensions. Input: 'the quick brown fox jumped on the lazy dog'.<br>PROVENANCE CAVEAT: as VT-040, recorded from the client's rendering. Raw object to be pasted into the evidence note. |

### VT-047 — `show_model` and `list_running` return what the specification says they return

**Result: PASS** · ENV-3, 2026-09-11, build `6d3fa8b`

| | |
|---|---|
| Discharges | `FS-02`, `FS-03` |
| Environment | ENV-3 · **needs a live host** |
| Procedure | From the second MCP client, with the server configured as in ENV-1, issue verbatim: "Call show_model on a model you can see installed, then call list_running, and paste both raw results." Paste the complete response objects, not the client's rendering. |
| Expected | show_model returns capabilities, context length, parameters and a `location`; list_running returns the models currently loaded (an empty list is a valid answer and must be distinguishable from a failure). |
| **Falsifier** | show_model omitting `location` or the context length -- the context length is what the budgeting guard reads, so a tool that cannot report it leaves an operator unable to check the number the refusals are computed from. Or list_running returning nothing in a way that cannot be told apart from an error. |
| Observed | 2026-09-11 through Antigravity on ENV-3. show_model on gemma4:12b returned verdict `ok`, location `local`, capabilities [completion, vision, audio, tools, thinking], context_length 262,144, the full details block (family, parameter_size 11.9B, quantization Q4_K_M), the raw parameter listing, and template_present true. list_running returned verdict `ok`, count 0, an empty list AND a note -- 'No model is loaded. The next call will pay a cold-start load.' -- which is the falsifier's case answered: an empty result that cannot be mistaken for a failure.<br>COLLATERAL: 262,144 is the figure the Context budgeting fixtures use for gemma4:12b as a real limit read off a live installation. This is the first time the tool has confirmed it, which also means show_model is the surface an operator can use to check the number the refusals in VT-050 are computed from.<br>EVIDENCE HANDLING: the transcript opens with the client viewing list_models.json, show_model.json and list_running.json -- leftovers from the VT-040 session -- before calling anything. It did then call all three tools, so this result is live. A client that answered from a stale file of that name would produce a record indistinguishable from this one. |

