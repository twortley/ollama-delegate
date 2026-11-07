# Changelog

Every release of `ollama-delegate`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

**Known defects are not listed here.** They live in one place, the defect
register in [`docs/VERIFICATION.md`](docs/VERIFICATION.md), each with its
severity and release note. A release's notes on GitHub are this file's entry
followed by that register, composed by `ci/release_notes.py`, so the two cannot
disagree.

## [1.1.0] - 2026-11-07

Decision models, and a fix for the way v1.0.0 handled them.

### Added

- **`decide`**, a tenth tool, for Ollama's decision models (`/v1/systemone`,
  Ollama 0.35.0 and newer). It asks typed questions about some content —
  `choice` from a set of labels, `noul` for yes/no, `score` on ordered levels —
  and returns probabilities instead of generated text. Every answer is checked
  against the question asked: a label that was not offered, probabilities that
  do not sum to one, or a question left unanswered is reported as
  `bad_response` or `incomplete`, never as `ok`. On an Ollama too old for the
  endpoint it says so (`endpoint_unavailable`) rather than reporting a missing
  model.
- **`think`** on `generate` and `chat`: `true`, `false`, or a level the model
  names, which `show_model` now lists as `think_levels`. Left unset, the
  model's default holds as before. Responses echo the level and report
  `thinking_chars`, and an answer emptied by thinking says so in its remedy.
- **`server_info` reports Ollama's own version**, and whether `decide` can work
  on it (`decide_supported`: yes, no or unknown).
- **The skill** covers `decide`: when to use it, how to phrase questions, and why
  `confidence` is not accuracy.

### Fixed

- **A decision model run through `generate` or `chat` returned prose that read
  like an answer.** `nimble` through `generate` returned *"Thinking Process:
  1. Analyze the Request…"* and no probabilities. Both tools now refuse a model
  whose capabilities name `decision` and not `completion`, with
  `wrong_capability` and a remedy naming `decide`. A model that reports no
  capabilities is not refused.
- **The design specification showed a closed deviation as open.** As published
  at 1.0.0, `docs/DESIGN.md` still listed `FS-05` as a deviation that its working
  copy had closed in August. It is published from the working copy again.
- **CI failed on Windows from its first run, so 1.0.0 has no GitHub release.**
  A selftest check compared a path before and after resolving it; GitHub's
  Windows runners use a short-name temp directory, so it failed there and
  nowhere else, and the release job never ran. The check now compares resolved
  paths. **This is the first release with a release page and the skill ZIP.**

## [1.0.0] - 2026-09-26

First release.

### Included

- **The MCP server** (`ollama_server.py`): nine tools over stdio — `list_models`,
  `show_model`, `list_running`, `generate`, `chat`, `embed`, `pull_model`,
  `delete_model`, `server_info`. Every response carries `location: local` or
  `cloud`; every failure returns a named verdict. `pull_model` and
  `delete_model` are refused unless enabled in the server's environment.
- **Semantic search**: `vault_index.py` builds, searches and checks indexes over
  a folder of markdown, returning citations rather than content. Setting
  `OLLAMA_MCP_INDEX_DIR` adds four `index_*` tools so an agent can search too.
- **The companion skill**, `local-inference-delegation`, attached to this release
  as `local-inference-delegation.zip` for clients that take an uploaded skill.
- **Documentation**: this README, the operator manual, the requirements and
  design specifications, the verification report, and `SECURITY.md`.

### Added since the last published commit

- `server_info` reports the release `version`, and `ollama_server.py --version`
  prints it.
- **The skill defers to a model the operator names.** It no longer substitutes a
  model it prefers, falls back quietly when the named one is slow, or moves a
  named task to a cloud model on its own.
- **CI** on every push: the selftest on Ubuntu and Windows, a real stdio
  handshake on both MCP SDK generations, an encoding check on every tracked text
  file, and a layout check on the skill ZIP. What a green run does not cover is
  stated at the top of `.github/workflows/ci.yml`.
- The selftest parses every `vault_index.py` command in the README, the manual
  and the skill with the CLI's own parser.
- `docs/MANUAL.md`, the operator manual. The README is now the short path; detail
  moved to the manual.

### Fixed since the last published commit

- **The skill's index commands could not run.** It showed
  `vault_index.py build <folder> --out index.json`; the CLI takes `--name`, and
  searches by name, not by path. Found while re-checking the documentation for
  this release, and now caught by the selftest.
