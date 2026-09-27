# Changelog

Every release of `ollama-delegate`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

**Known defects are not listed here.** They live in one place, the defect
register in [`docs/VERIFICATION.md`](docs/VERIFICATION.md), each with its
severity and release note. A release's notes on GitHub are this file's entry
followed by that register, composed by `ci/release_notes.py`, so the two cannot
disagree.

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
