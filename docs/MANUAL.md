---
okf_version: 0.1+TW1
title: Operator Manual — ollama-delegate
purpose: How to install, connect, verify, configure, troubleshoot, update and remove ollama-delegate and its companion skill. DOC-02.
doc_type: design
status: canon
doc_revision: "2"
created: 2026-09-25
last_updated: 2026-09-26
source_author: Tim Wortley + Claude
tags:
  - publication
  - design
  - manual
---

# ollama-delegate — operator manual

How to install, connect, verify, configure, troubleshoot, update and remove
`ollama-delegate` and its companion skill.

**The README gets you running. This manual is for everything after that** — and
for the moment something does not work.

## Contents

1. [About this manual](#1-about-this-manual)
2. [Before you start](#2-before-you-start)
3. [Install the server](#3-install-the-server)
4. [Connect a client](#4-connect-a-client)
5. [Install the skill (optional)](#5-install-the-skill-optional)
6. [Verify the pair](#6-verify-the-pair)
7. [Configure](#7-configure)
8. [Local and cloud models](#8-local-and-cloud-models)
9. [Semantic search](#9-semantic-search)
10. [Troubleshooting](#10-troubleshooting)
11. [Updating](#11-updating)
12. [Uninstalling](#12-uninstalling)
13. [Known issues](#13-known-issues)

---

## 1. About this manual

**Who it is for:** someone installing `ollama-delegate` on their own machine, with
no access to the machine it was built on. Every step is written to be followed
literally.

**What it assumes:** you can open a terminal, run a command and edit a JSON file.
Installing Python and Ollama themselves is out of scope — both have their own
installers and documentation.

**What was actually tested:**

| Platform | End-to-end install tested | Clients |
|---|---|---|
| Windows | Yes — a clean install from the published repository, Python 3.13 | Claude Desktop (chat and Code tab), Claude Code CLI, Google Antigravity |
| Ubuntu | Yes — from a clean clone, Python 3.12, **on code that predates the semantic-search tools** | Claude Desktop (official Linux beta) |
| macOS | **No.** The POSIX commands should apply; nothing here has been run on a Mac | — |

**Python 3.10 is the supported floor** — a commitment, not a measurement on every
version. Which versions were exercised, how deeply and on what code is in
[`docs/DESIGN.md`](DESIGN.md) §14.4.

**The other documents:**

| Document | Answers |
|---|---|
| `README.md` | What this is, whether you want it, and the shortest path to running it |
| `docs/URS_FS.md` | What it is required to do |
| `docs/DESIGN.md` | How it does it — including the full tool reference |
| `docs/VERIFICATION.md` | What was verified, with what limits, and which defects ship knowingly |

**Conventions.** Commands are shown for **Windows (PowerShell)** and **macOS/Linux
(bash)**. The interpreter inside the virtual environment is written
`.venv/bin/python` in prose; on Windows it is `.\.venv\Scripts\python.exe`
everywhere it appears.

---

## 2. Before you start

**Five things. Each has one command that proves you have it** — find out now,
not halfway through a stack trace.

| You need | Prove it | If it is missing |
|---|---|---|
| **Python 3.10 or newer** | `python --version` (Windows) · `python3 --version` (macOS/Linux) | python.org, or your package manager |
| **Git** | `git --version` | git-scm.com — needed only to get the code |
| **Ollama, running** | `ollama list` | ollama.com. A table means the daemon is up; an error means fix that first |
| **An embedding model** | `ollama list` shows it, e.g. `nomic-embed-text` | `ollama pull nomic-embed-text` — needed for semantic search (section 9) |
| **A reasoning model that fits your machine** | it answers a prompt inside your client's timeout | `ollama pull` one sized for your hardware — needed for `generate`, `chat` and reranking |

**`pip` does not enforce the Python floor.** There is no `requires-python`. The
server checks the version itself at startup and refuses by name — but only once
you run it.

**An embedding model is not a chat model.** Having a reasoning model does not give
you one, and semantic search needs one specifically.

**"Working" means more than installed.** A model that is pulled but too large for
the machine will load and never return inside a client's timeout. **Embedding and
reasoning are independent:** modest hardware often embeds quickly and cannot run a
reasoning model large enough to rerank.

**Expect the first call to be slow.** Ollama loads a model on first use and evicts
it after about five minutes idle. On a laptop, a 4B model timed out twice before
answering in 13 seconds — and had been evicted again by the next prompt. See
[Troubleshooting](#10-troubleshooting).

---

## 3. Install the server

**Clone onto local storage.** A path that resolves to a network share will appear
to work and will operate on the wrong machine.

**Windows — PowerShell**

```powershell
git clone https://github.com/twortley/ollama-delegate.git
cd ollama-delegate
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install mcp
.\.venv\Scripts\python.exe ollama_server.py --selftest
.\.venv\Scripts\python.exe ollama_server.py --probe
```

**macOS and Linux — bash**

```bash
git clone https://github.com/twortley/ollama-delegate.git
cd ollama-delegate
python3 -m venv .venv
.venv/bin/python -m pip install mcp
.venv/bin/python ollama_server.py --selftest
.venv/bin/python ollama_server.py --probe
```

**To install a specific release**, add `--branch v1.0.0` (or the tag you want) to
the clone. `ollama_server.py --version` prints the release a checkout belongs to.

**Create the venv with `python3`; use it with `python`.** On macOS and Linux
`python` often does not exist until the venv does — the venv's own `bin/` provides
`python`, `python3` and a versioned name.

**`pip install mcp` installs around thirty packages.** That is the MCP SDK's own
dependency tree; this project adds no dependencies of its own.

### 3.1 `--selftest` — the guards work

Needs nothing running: no Ollama, no network. It asserts the server's guard
behaviour and prints one line per check, grouped:

```text
URL normalisation
  PASS  bare host:port gains an http:// scheme
  PASS  trailing slash is stripped
  PASS  ftp:// scheme is refused
  PASS  file:// scheme is refused
  PASS  empty host is refused
  PASS  scheme with no host is refused
...

SELFTEST PASSED
```

**The last line must be `SELFTEST PASSED`.** Anything else — a `FAIL` line, a
traceback, or no final line — means stop here; see
[Troubleshooting](#10-troubleshooting). The number of checks grows between
versions, so do not compare counts.

### 3.2 `--probe` — the server can see your Ollama

The live check: it lists the models Ollama actually has, and where each runs.

Linux, six models:

```text
Host: http://localhost:11434
  Reachable. 6 model(s):
    [local ] gemma3:4b                                        3.1 GB
    [cloud ] glm-5.2:cloud                                    338.0 B
    [cloud ] kimi-k2.7-code:cloud                             388.0 B
    [local ] nomic-embed-text:latest                          261.6 MB
    [local ] qwen3.5:4b                                       3.2 GB
    [local ] qwen3.5:9b                                       6.1 GB

  2 of 6 run on Ollama's hosted infrastructure, not this machine.
  Content sent to those leaves the host. Permitted and sometimes the right choice --
  the point is that it should be a choice, not a surprise.
```

Windows, three models:

```text
Host: http://localhost:11434
  Reachable. 3 model(s):
    [local ] gemma4:12b                                       7.0 GB
    [cloud ] glm-5.2:cloud                                    290.0 B
    [local ] nomic-embed-text:latest                          261.6 MB

  1 of 3 run on Ollama's hosted infrastructure, not this machine.
  Content sent to those leaves the host. Permitted and sometimes the right choice --
  the point is that it should be a choice, not a surprise.
```

**Your list will differ — model names are specific to each host.** What matters:
`Reachable`, at least one model, and a `[local ]` or `[cloud ]` label on each. A
cloud model is a few hundred **bytes**: it is a pointer, not weights. Section 8
explains the labels.

If Ollama cannot be reached, `--probe` says so instead of listing models. Fix
that before going further.

---

## 4. Connect a client

The server is a standard **stdio MCP server**: the client launches it. Every
client below uses the same block — only where it goes differs.

```json
{
  "mcpServers": {
    "ollama-delegate": {
      "command": "C:\\path\\to\\ollama-delegate\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\ollama-delegate\\ollama_server.py"],
      "env": {
        "OLLAMA_HOST": "http://localhost:11434"
      }
    }
  }
}
```

On **macOS and Linux** the same block takes POSIX paths, and no doubling:

```json
{
  "mcpServers": {
    "ollama-delegate": {
      "command": "/path/to/ollama-delegate/.venv/bin/python",
      "args": ["/path/to/ollama-delegate/ollama_server.py"],
      "env": {
        "OLLAMA_HOST": "http://localhost:11434"
      }
    }
  }
}
```

**Three rules, every client:**

1. **Absolute paths only.** The client starts the server with no shell and no
   predictable working directory; nothing relative resolves. **On Windows, double
   every backslash.**
2. **The key is `ollama-delegate`.** Not `ollama`. The skill refers to the server by
   that name, and the failure is silent: registered as `ollama`, a question that
   should have been delegated produced **no tool calls at all**; renamed, with
   nothing else changed, it produced four.
3. **Copy one server block, never the whole file.** A client's MCP config holds
   *every* server's settings — API keys included. Do not paste it whole into an
   issue, a chat, or a support request.

**Environment variables go in the block's `env`, not your shell.** The client
launches the server itself, so a variable set in your terminal never reaches it.
Section 7 lists them.

### 4.1 Claude Desktop

Settings → Developer → **Edit Config**. The file is:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Add the block and **restart Claude Desktop**. A **"running"** tag beside the
server name confirms it connected.

**Linux:** the official Claude Desktop build is a `.deb` from
[claude.com/download](https://claude.com/download), in beta since June 2026,
tested on Ubuntu 22.04+ and Debian 12+. Installing it registers Anthropic's apt
repository, so updates arrive with normal system updates.

### 4.2 Claude Code

Claude Code comes in two forms, and they read different config files.

**The Code tab in Claude Desktop** uses Claude Desktop's config. A server added
there (section 4.1) is available in the Code tab too — nothing more to do.

**Claude Code at the command line** keeps its own config, in **`~/.claude.json`**
(Windows: `%USERPROFILE%\.claude.json`). The simplest route is to ask Claude Code
to add the server: give it the command, the argument and any `env` values from the
block above, and it writes the entry to that file. **Then restart Claude Code** — it does not pick
up the new server until you do.

**If you use both forms, check each one's server list.** They read different
files, so do not assume a server added in one is visible in the other.

### 4.3 Google Antigravity

The global MCP config — one file for every agent — is:

| Platform | Path |
|---|---|
| Windows | `%USERPROFILE%\.gemini\config\mcp_config.json` |
| macOS / Linux | `~/.gemini/config/mcp_config.json` |

You can also reach it from **Settings → Customizations → Installed MCP Servers →
Open MCP Config**. The block is the same shape as above.

**Restart Antigravity after every edit to the config or the server's code.**
Neither control in Installed MCP Servers restarts the server:

- the **toggle** enables and disables it — the running code does not change;
- the **refresh** button resets tool permissions (expect to approve the tools
  again) — the running code still does not change.

**Connected** shows as a **green dot** beside `ollama-delegate` in Installed MCP
Servers, with a tool count. See section 6 for what the count should be.

**Antigravity's agent goes looking for answers outside the tools.** In testing it
ran a recursive search of the home folder for `*ollama*`, and read
`ollama_server.py` before calling a tool, in sessions where it was asked only to
use the server. To keep it on the MCP tools, **deny it access to those folders
when it asks**. It then uses the tools.

**Renaming a server leaves its old tool-schema cache behind**, in
`~/.gemini/antigravity/mcp/<old-name>/`. Harmless, but it can confuse anyone
reading that folder.

---

## 5. Install the skill (optional)

The server gives an agent the tools. The **`local-inference-delegation`** skill
tells it *when* to use them — without it, an agent with these tools and no
judgement about delegating mostly never calls them.

**The server works without the skill.** The skill needs a client that loads
`SKILL.md` skills — narrower than "any MCP client", but not one vendor's. It is in
the repository under `skills/local-inference-delegation/`.

> **Read this before installing.** Once installed, **the skill reroutes tasks that
> look like bulk work to Ollama without being asked** — it does not wait for you to
> mention a local model. In one test, a request to classify 107 note titles was
> sent to Ollama unprompted; the local model was slow, and the task was completed
> on a **cloud** model instead. If you want delegation only on request, do not
> install the skill; call the tools by name. See [Known issues](#13-known-issues).

**If you want a particular model, name it in the request.** Left to itself, the
skill picks a model by its own selection ladder. Naming one is the only way to
steer that choice. The skill does not yet guarantee it will keep to the model you
named; an agent may still switch if it judges the model too slow or unfit.

| Client | Where the skill goes |
|---|---|
| **Claude Code** — every project | `~/.claude/skills/local-inference-delegation/` |
| **Claude Code** — one project | `.claude/skills/local-inference-delegation/` in that project |
| **Claude apps** (Desktop, web, Cowork) | **Customize → Skills → + → Create skill → Upload a skill**, using `local-inference-delegation.zip`. Code execution must be enabled |
| **Google Antigravity** — every workspace | `~/.gemini/config/skills/local-inference-delegation/` |
| **Google Antigravity** — one workspace | `.agents/skills/local-inference-delegation/` in that workspace |

**Copy the whole folder, not just `SKILL.md`.**

**Windows — PowerShell** (Claude Code, every project)

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
Copy-Item -Recurse -Force skills\local-inference-delegation "$HOME\.claude\skills\"
```

**macOS and Linux — bash**

```bash
mkdir -p ~/.claude/skills && cp -r skills/local-inference-delegation ~/.claude/skills/
```

**The ZIP for Claude apps.** Each release attaches `local-inference-delegation.zip`
under **Assets** on its GitHub release page — download that. To build one from a
clone instead (the same command on every platform):

```
python ci/build_skill_zip.py
```

It writes `dist/local-inference-delegation.zip` and checks the archive before you
upload it: one top-level `local-inference-delegation/` folder, `SKILL.md` at its
root, `/` separators, and the frontmatter a skills client reads. The release ZIP
is built by the same script and passes the same checks.

**Skills in Claude apps belong to your account and sync across devices.** Removing
one there removes it everywhere you are signed in.

**Change the skill in the repository, then reinstall.** The copy in `skills/` is
the source; an installed copy is a deployment of it. An edit made to the installed
copy has no history, and the next install silently overwrites it.

---

## 6. Verify the pair

Run these in order. Each proves something the one before it cannot.

| # | Do | Expect | Proves |
|---|---|---|---|
| 1 | `--selftest` (section 3.1) | Last line `SELFTEST PASSED` | The guards work on this Python |
| 2 | `--probe` (section 3.2) | `Reachable`, models listed and labelled | The server can reach your Ollama |
| 3 | Look at the client's server list | `ollama-delegate` connected, with its tools | The client launched it with your paths |
| 4 | Ask the agent to call `list_models` | Your models, each with a `location` | A call goes client → server → Ollama and back |
| 5 | Ask the agent to call `server_info` | The `version`, and what the server permits and refuses — write gates, allowlist, timeouts, retrieval | The release and the configuration you set are the ones running |
| 6 | Ask for one small delegated job, naming a **local** model from step 4 | An answer, and a response whose `location` is `local` | End to end, with content staying on the machine |

**The tool count in step 3 depends on semantic search:**

| `OLLAMA_MCP_INDEX_DIR` | Tools |
|---|---|
| unset | **9** |
| set to a directory | **13** — the nine plus four `index_*` tools |

**If you set the variable and see 9, it did not reach the server** — it is in
your shell, not the client's config (section 4).

**Use a model name from step 4, not one you remember.** Model names are
host-specific; guessing one produces a 404.

The full list of tools, their parameters and what each returns is in
[`docs/DESIGN.md`](DESIGN.md), section 6 *Interfaces — MCP tool surface* and
section 11 *Retrieval MCP tools*.

---

## 7. Configure

Every setting is an environment variable, set in the client's `env` block
(section 4).

| Variable | Default | Effect |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama's base URL. A bare `host:port` gets `http://` prepended |
| `OLLAMA_MCP_INDEX_DIR` | unset | Directory of `<name>.index.json` files. **Unset means the four `index_*` tools are not registered at all.** Set, it is also the allowlist: only indexes in that directory are reachable |
| `OLLAMA_MCP_ALLOW_DELETE` | off | `1` permits `delete_model` |
| `OLLAMA_MCP_ALLOW_PULL` | off | `1` permits `pull_model` |
| `OLLAMA_MCP_MODELS` | unset | Comma-separated model allowlist. Unset means any model |
| `OLLAMA_MCP_TIMEOUT` | `300` | Seconds for `generate`, `chat` and `embed` |
| `OLLAMA_MCP_PULL_TIMEOUT` | `3600` | Seconds for `pull_model` |
| `OLLAMA_MCP_LOGLEVEL` | `INFO` | The server's own diagnostics, on stderr. `DEBUG` is verbose |

**Every variable at its default**, to paste into `env` and edit:

```json
"env": {
  "OLLAMA_HOST": "http://localhost:11434",
  "OLLAMA_MCP_INDEX_DIR": "",
  "OLLAMA_MCP_ALLOW_DELETE": "0",
  "OLLAMA_MCP_ALLOW_PULL": "0",
  "OLLAMA_MCP_MODELS": "",
  "OLLAMA_MCP_TIMEOUT": "300",
  "OLLAMA_MCP_PULL_TIMEOUT": "3600",
  "OLLAMA_MCP_LOGLEVEL": "INFO"
}
```

**An empty `OLLAMA_MCP_INDEX_DIR` is not a directory.** The `index_*` tools register
only when it points at one — leave it empty or drop the line to keep them off.

### 7.1 The two write operations are off unless you turn them on

`pull_model` downloads a model and `delete_model` removes one — both change the
host. Each is refused unless its `OLLAMA_MCP_ALLOW_*` variable is set **in the
server's environment**. **A tool call cannot enable them, and neither can a
config file the server reads.** Capability comes from whoever starts the process.

`server_info` names the flag for each gate, so an agent can tell you what to set
rather than guessing.

### 7.2 Timeouts

`OLLAMA_MCP_TIMEOUT` is the **server's** limit. **Your client has its own**, often
shorter, and a cold model load can exceed it (section 10). Raising the server's
timeout does not raise the client's.

**This bites on CPU-only hosts with a reasoning model.** In testing, one local
`chat` call on such a host took 127 s at about 6 tokens/s. The next call ran past
the client's limit, about 3 minutes in Antigravity, and the operator saw the
client's timeout, not a verdict from the server. Use a smaller or non-reasoning
model, or smaller batches. This is **D-16** in the defect register.

---

## 8. Local and cloud models

Ollama can serve **hosted** models alongside local ones. They look almost the same
in a listing — a `:cloud` tag and a size of a few hundred bytes — and **content
sent to them leaves this machine.**

**Both are permitted.** A hosted frontier model is sometimes the right call. What
this server guarantees is that the choice is **visible**: every model in
`list_models`, every `show_model`, every `generate`, `chat` and `embed` response,
and `--probe` carries a `location`:

| `location` | Meaning |
|---|---|
| `local` | Runs on this machine. Content does not leave it |
| `cloud` | Tagged `:cloud`. Runs on Ollama's infrastructure. **Content leaves this machine** |
| `cloud?` | Stub-sized but not tagged. **Treated as not proven local**, rather than guessed either way |

**What the server does not do: choose for you, or stop an agent choosing.** There
is no setting that says *"never use cloud unless I ask"*. An agent that finds a
local model too slow can move a task to a cloud model on its own judgement, and
has (section 5). **To keep content on the machine, use `OLLAMA_MCP_MODELS` to
allow only local models.** See [Known issues](#13-known-issues).

---

## 9. Semantic search

`vault_index.py` ships with the server. **`build` embeds a folder of markdown into
a table of vectors; `search` queries it.** The vectors stay on this machine — an
agent doing this through tool calls would pull thousands of float arrays into its
context and run out almost immediately.

### 9.1 Build, search, status

**macOS and Linux — bash**

```bash
export OLLAMA_MCP_INDEX_DIR=~/.ollama-delegate/indexes

.venv/bin/python vault_index.py build /path/to/your-notes \
    --name notes --describe "engineering notes and runbooks" --rebuild
.venv/bin/python vault_index.py search notes "why did the GPUs slow down"
.venv/bin/python vault_index.py status notes
```

**Windows — PowerShell.** `$env:` rather than `export`, set in the same session
that runs the script:

```powershell
$env:OLLAMA_MCP_INDEX_DIR = "$HOME\.ollama-delegate\indexes"

.\.venv\Scripts\python.exe vault_index.py build C:\path\to\your-notes `
    --name notes --describe "engineering notes and runbooks" --rebuild
.\.venv\Scripts\python.exe vault_index.py search notes "why did the GPUs slow down"
.\.venv\Scripts\python.exe vault_index.py status notes
```

**Setting it in your shell does not set it for your client.** For the `index_*`
tools, the same directory goes in the client's `env` (section 4).

**An index is named, not located.** It is written as `<name>.index.json` in
`$OLLAMA_MCP_INDEX_DIR` (or `--dir`); `<name>` is `[a-z0-9_-]+` — no dots, no
separators, no path. **Several indexes is normal:** notes, code and a client's
documents are separate corpora, and `--describe` is what a caller chooses between
them on.

**`status`** re-hashes the corpus against the digests stored at build time and
names what changed — added, changed, removed. It exits **1** on drift, so it can
gate a script, and **2** when the source folder is not reachable from this host —
a different answer from "unchanged", not to be read as one.

**The embedding model matters.** The default is `nomic-embed-text`; `--model`
overrides it and must have `embedding` among its capabilities. `nomic-embed-text`
needs different prefixes for documents and queries, and the script applies them.
**For any other model it embeds text as-is and prints a warning** — which does not
stop you. Retrieval quality simply degrades, with everything still looking
healthy. If you change models, find out whether yours wants prefixes.

### 9.2 Reranking

Cosine similarity measures topical overlap, not whether a passage answers the
question. `--rerank` has a local model judge the candidates.

```bash
.venv/bin/python vault_index.py search notes "why did the GPUs slow down" --rerank
```

| Flag | Default | Notes |
|---|---|---|
| `--rerank [MODEL]` | `gpt-oss:20b` | `qwen3.6:35b` is more precise on ambiguous queries and about 3× slower. **Not `gemma3:4b`** — it scores about 45% of candidates as direct answers, which is embedding order with confident labels |
| `--rerank-pool N` | 20 | Candidates sent to the judge. Widen when the spread warning fires |
| `--rerank-batch N` | 5 | Passages per call. **A correctness setting, not a speed one** — 20 at once produced well-formed scores that did not match the passages |
| `--explain "text"` | — | Where chunks containing that literal text actually rank |

**Two diagnostics matter more than the ranking:**

- **The spread line.** `scored 20 of 20` measures participation; `spread: 2=9,
  1=6, 0=5` measures discrimination. A judge that scores most candidates alike has
  ranked nothing.
- **`--explain`** separates a **recall** failure (the answer never entered the pool
  — widen it) from a **ranking** failure (change model). They need opposite fixes.

These figures were measured on one corpus on one host. Treat model fitness and
batch size as portable; re-measure pool width and the spread threshold on your own
material.

### 9.3 Letting an agent search — the `index_*` tools

With `OLLAMA_MCP_INDEX_DIR` set in the client's `env`, four more tools register:

| Tool | Returns |
|---|---|
| `index_list()` | Every index: name, description, when built, counts, embedding model |
| `index_search(index, query, k, rerank)` | **Citations, not content** — chunk id, path, heading, line range, scores |
| `index_get(index, ids)` | The text of named chunks, from the index |
| `index_explain(index, query, text)` | Where a phrase you expect ranks, and whether a miss is recall or ranking |

**Anything that touches your files is a command-line operation; the tools read
only the index.** No tool takes a filesystem path.

**Three refusals you may meet:**

- **An index built by an older version is refused**, with the rebuild command
  named.
- **Chunk ids belong to one build.** An id from before the last rebuild is refused
  rather than resolved to a different passage.
- **A missing embedding model is refused, never substituted.**

**Staleness is reported, not estimated.** Responses carry the build time and name
`vault_index.py status <name>` as the check.

**Reranking through a client is limited by the client's timeout.** A pool of 40
can exceed it even with the model warm, so `index_search` defaults to 20 — which
can miss an answer ranked lower. The response says when the pool may have been too
narrow. **For a wider pool, use the command line:** each scoring batch there has
its own 120-second limit, and exceeding it gives a named cause rather than a stack
trace.

```bash
.venv/bin/python vault_index.py search notes "your question" --rerank --rerank-pool 40
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `python` not found (macOS/Linux) | `python` often does not exist outside a venv | Create the venv with `python3`; after that use `.venv/bin/python` |
| Config points at `python.exe` on macOS/Linux | A Windows path half-translated | `.venv/bin/python`, forward slashes, no doubled backslashes |
| Server refuses at startup naming a Python version | Python older than 3.10; `pip` did not stop you | Install 3.10+, recreate the venv |
| `--probe` cannot reach Ollama | The daemon is not running, or `OLLAMA_HOST` is wrong | `ollama list` should print a table; check the URL |
| Server does not appear in the client, or shows disconnected | A relative path, single backslashes on Windows, or the client was not restarted | Absolute paths; `\\` on Windows; restart the client |
| **9 tools where you expected 13** | `OLLAMA_MCP_INDEX_DIR` is in your shell, not the client's `env` — or it is empty | Put it in the config block, pointing at a directory; restart |
| Every call returns 404 / `model_not_found` | A model name guessed or remembered from another machine | Call `list_models` first and use a name from it |
| First call times out; the next one works | Cold load: the model was not in memory | Call again once warm. Models are evicted after about five minutes idle, so bursty use is cheaper than occasional use |
| Every call to one model times out | The model is too large for the machine | Choose a smaller model; `show_model` and `--probe` sizes help |
| Empty or cut-off output | The model ran out of output budget — common with reasoning models, which spend it thinking. The server reports this as a failure (`empty_generation`, truncated), never as an answer | Raise `max_tokens` on that `generate` or `chat` call |
| `context_exceeded` | Input longer than the model's context. Ollama would silently truncate and return a valid-looking result; the server refuses instead | Chunk the input, or pass `allow_truncation=true` to accept the loss knowingly |
| `not_permitted` from `pull_model` or `delete_model` | The write gates are off by default | Set `OLLAMA_MCP_ALLOW_PULL` / `_DELETE` in the client's `env` (section 7.1) |
| The skill never triggers | Server registered under a key other than `ollama-delegate` | Rename the key; restart the client |
| The skill delegates tasks you did not ask to delegate | Intended behaviour of the installed skill | See section 5; remove the skill for request-only delegation |
| A code change or config edit has no effect | The server process was not restarted | Restart the client. **In Antigravity, neither the toggle nor refresh restarts it** — quit and reopen |
| Antigravity asks to approve the tools again | The refresh button in Installed MCP Servers resets tool permissions | Approve them; expected |
| An index is refused | Built by an older version, stale chunk id, or embedding model missing | Rebuild with the command named in the refusal; `ollama pull` the embedding model |
| Search finds nothing relevant | The answer fell outside the rerank pool, or ranked low | `--explain "a phrase you expect"`, then widen the pool or change the judge (section 9.2) |

**When reporting a problem, include `--selftest` and `--probe` output and your one
server block — never the whole client config** (section 4).

---

## 11. Updating

1. **Pull the new code:** `git pull` in the clone.
2. **Reinstall dependencies:** `.venv/bin/python -m pip install mcp` — or on
   Windows `.\.venv\Scripts\python.exe -m pip install mcp`.
3. **Run `--version` and `--selftest`.** `--version` prints the release you now
   have; the last line of `--selftest` must be `SELFTEST PASSED`.
4. **Reinstall the skill** from `skills/`, if you use it (section 5). An installed
   copy does not update itself.
5. **Restart the client.** The running server keeps the old code until then — in
   Antigravity, quit and reopen (section 4.3).
6. **Verify** (section 6). Check the tool count.
7. **Rebuild indexes if a search refuses them** — the refusal names the command.

---

## 12. Uninstalling

Remove what you installed; nothing else was written.

| What | Where |
|---|---|
| **The server block** | Your client's MCP config (section 4). Remove only the `ollama-delegate` block |
| **The skill** | The folder you copied it to (section 5). In Claude apps: **Customize → Skills**, which removes it from your account on every device |
| **The code and its venv** | The clone. The venv is inside it |
| **Indexes** | The directory in `OLLAMA_MCP_INDEX_DIR`, e.g. `~/.ollama-delegate/indexes` |
| **Antigravity's schema cache** | `~/.gemini/antigravity/mcp/ollama-delegate/` |
| **Models** — optional | `ollama rm <model>`. They belong to Ollama and other tools may use them |

**Restart the client afterwards**, or the old server keeps running until you do.

---

## 13. Known issues

**Every defect this project knows about — including those already fixed — is in
[`docs/VERIFICATION.md`](VERIFICATION.md), section *Known defects, and what was
decided about each*,** with a severity and a release decision for each. That
register is the only list; it is not repeated here, so the two cannot disagree.
