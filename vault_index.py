#!/usr/bin/env python3
"""
Semantic search index over a folder of markdown, using a local Ollama
embedding model. Nothing leaves the machine.

    python vault_index.py build  ~/vault --name notes --describe "my notes"
    python vault_index.py search notes "why did the NAS fill up"
    python vault_index.py status notes

Indexes are written as `<name>.index.json` into the directory named by
OLLAMA_MCP_INDEX_DIR (or --dir). That is the same directory the MCP `index_*`
tools read, and it is the only directory they can reach.

Dependencies: none. stdlib only, same as the MCP server it ships with.

WHAT IS A CLI OPERATION AND WHAT IS A TOOL
------------------------------------------
Anything that touches the corpus is a CLI operation; MCP reads the index. That
is why `build` and `status` live here and have no tool equivalent: both open
arbitrary files under a folder the operator chooses. The agent-facing surface
opens exactly one file, the index, which makes "no MCP tool reads your
filesystem" a structural claim rather than one needing a footnote.

THIS MODULE IS IMPORTED BY A LONG-LIVED SERVER
----------------------------------------------
`index_tools.py` imports the search half of this file into the MCP server
process, which changes two things that were safe in a script and are not safe
in a server:

  * Library functions raise IndexerError. They do NOT call sys.exit -- an exit
    inside a tool call takes the whole server down with it.
  * Library functions write diagnostics to STDERR. Under stdio MCP, stdout is
    the protocol channel: one stray print corrupts the stream and the failure
    surfaces as a client-side parse error a long way from its cause.

Only the command functions (build, search, status) print to stdout, and the
server never calls them.

WHY THIS IS A SCRIPT AND NOT A SERIES OF TOOL CALLS
---------------------------------------------------
Embedding vectors are 768 floats each. Passing thousands of them through an
agent's context to do arithmetic on them is enormously wasteful and hits
context limits almost immediately. The vectors should be produced, stored and
compared on the machine that holds them; the agent's job is to decide what to
index and to read the search RESULTS, which are small.

THE FAILURE THIS SCRIPT IS BUILT AROUND
---------------------------------------
Ollama does not error when input exceeds the embedding model's context window
-- it TRUNCATES and returns a perfectly valid-looking vector for the first
fragment. An index built from truncated vectors looks healthy, scores plausibly,
and quietly returns worse results forever. There is no later signal that
anything went wrong.

So this script reads the model's real limit and refuses to embed anything over
it, rather than trusting a hardcoded number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# Declared minimum supported interpreter. See ollama_server.py for why this is
# a runtime check and why the codebase avoids 3.10-only syntax.
MIN_PYTHON = (3, 10)
if sys.version_info < MIN_PYTHON:
    raise SystemExit(
        f"vault_index.py requires Python {'.'.join(map(str, MIN_PYTHON))} or "
        f"newer. This interpreter is {sys.version.split()[0]} "
        f"({sys.executable}).")

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "nomic-embed-text:latest"

# Index format version. Bumped from 1 (implicit, unversioned) when citations
# arrived: a v1 index carries no headings, no line ranges and no generation, so
# there is nothing in it a citation could be built from. v1 indexes are REFUSED
# with the rebuild command named, not read with six fields reported `unknown` --
# a search result carrying a path and no line range is not a citation, and
# handing one back labelled `unknown` is the wrong-fact-with-good-provenance
# failure wearing a disclaimer.
INDEX_FORMAT = 2

# Written into every index. A reader can tell which builder produced a file
# without inferring it from which fields happen to be present.
BUILDER_VERSION = "2.0.0"

INDEX_SUFFIX = ".index.json"

# An index NAME, not a path. No separators, no dots, no traversal -- `../../etc`
# is not a rejected path here, it is an invalid name. Validating as an
# identifier rather than filtering a path is what makes traversal impossible by
# construction: there is no expression of it for a filter to miss.
NAME_RE = re.compile(r"^[a-z0-9_-]+$")

INDEX_DIR_ENV = "OLLAMA_MCP_INDEX_DIR"

# Pessimistic, matching the MCP server: English averages ~4 chars/token, but
# code, paths and identifiers run denser. Erring low means we chunk smaller than
# strictly necessary, which is cheap. Erring high means silent truncation.
CHARS_PER_TOKEN = 3.2

# Overlap between adjacent chunks, in characters. Without overlap, a sentence
# spanning a chunk boundary is semantically split in half and neither piece
# retrieves well. This is the cheapest fix for the most common retrieval miss.
CHUNK_OVERLAP = 200

# Target chunk size for RETRIEVAL, which is much smaller than the size that
# merely FITS. An embedding is approximately an average of everything in the
# chunk, so a chunk spanning many topics produces a vector that matches all of
# them weakly and none of them strongly.
#
# Observed 2026-08-26 on a real 105-note vault: chunking at the model ceiling
# (6,553 chars) produced a top-5 spread of 0.577 to 0.538 -- a 0.04 range, which
# is noise rather than ranking. Large chunks were the main cause.
#
# ~1,500 chars is roughly a section: enough to carry meaning, small enough to be
# about one thing. Still hard-capped by the model's real window below.
TARGET_CHUNK_CHARS = 1500

# nomic-embed-text is trained ASYMMETRICALLY and requires task prefixes.
# Omitting them is a silent quality regression -- everything still runs, scores
# still look plausible, and retrieval is simply worse with nothing to indicate
# why. Documents and queries get DIFFERENT prefixes; using one for both, or
# neither, degrades results.
#
# The prefixes used are stored in the index so a later search cannot drift from
# the build. Models that do not use prefixes take the empty strings.
PREFIXES = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
    "nomic-embed-text:latest": ("search_document: ", "search_query: "),
    "mxbai-embed-large": ("", "Represent this sentence for searching relevant passages: "),
}


def prefixes_for(model: str) -> tuple[str, str]:
    """(document_prefix, query_prefix) for a model, or empty strings if unknown."""
    return PREFIXES.get(model, PREFIXES.get(model.split(":")[0], ("", "")))


# -------------------------------------------------------- errors and output


class IndexerError(RuntimeError):
    """
    A failure a caller can report. Raised, never exited.

    Every one of these was a `sys.exit` until this module was imported into the
    MCP server process, where an exit inside a tool call takes down the server
    and every other tool with it. The CLI turns these back into exits in main();
    the server turns them into refusals.

    It carries a `verdict` so index_tools does not have to infer one from the
    message text. Deciding what went wrong by matching on a human-readable
    string is a check that passes until someone improves the wording.
    """

    def __init__(self, message: str, verdict: str = "index_error",
                 remedy: str = ""):
        super().__init__(message)
        self.verdict = verdict
        self.remedy = remedy


def _note(message: str) -> None:
    """
    Library diagnostics. STDERR, always.

    Under stdio MCP, stdout carries the JSON-RPC frames. A print to stdout from
    inside a tool call does not fail loudly -- it corrupts the stream, and the
    client reports a parse error with no indication of which call produced it.
    Command functions print results to stdout; everything reachable from a tool
    call prints here.
    """
    print(message, file=sys.stderr)


# ---------------------------------------------------------------- transport


def _timed_out(host: str, timeout: float) -> "IndexerError":
    """
    One timeout message, so the remedy cannot drift between call sites.

    The remedy names the three levers in the order they cost: stop reranking,
    narrow the pool, use a smaller model. It does NOT send the operator to the
    CLI as an escape -- the CLI has the same timeout, and a remedy that moves
    the failure somewhere else is worse than none.
    """
    return IndexerError(
        f"Ollama at {host} accepted the connection but did not answer within "
        f"{timeout:.0f}s.",
        verdict="ollama_timeout",
        remedy="A model too large for this machine is the usual cause, and a "
               "cold load is slower than a warm one -- try the same call again "
               "first. Otherwise: drop --rerank, narrow --rerank-pool, or "
               "choose a smaller reranking model.")


def _post(host: str, path: str, payload: dict, timeout: float = 300) -> dict:
    req = urllib.request.Request(
        host.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        # A connect-time failure. `e.reason` is frequently itself an OSError,
        # and a connect timeout arrives wrapped this way rather than raw.
        if isinstance(e.reason, TimeoutError):
            raise _timed_out(host, timeout) from e
        raise IndexerError(f"Cannot reach Ollama at {host}: {e.reason}",
                           verdict="ollama_unreachable",
                           remedy="Is it running? Try: ollama serve") from e
    except TimeoutError as e:
        # THE READ TIMEOUT, and the reason this clause exists.
        #
        # `urllib.error.URLError` and `TimeoutError` are SIBLINGS under
        # OSError, not parent and child. A read timeout -- the connection was
        # accepted, the model then took longer than `timeout` to answer --
        # therefore walked straight past the URLError handler above, past
        # `_score_batch`'s `except IndexerError`, past rerank()'s per-batch
        # `failures` collector whose entire purpose is to absorb exactly this,
        # and reached the operator as a traceback. Found on ENV-2 2026-08-31,
        # having already been seen on ENV-5 the same day wearing a different
        # mask: through an MCP client the same escape surfaces as a bare tool
        # error with no verdict, reason or remedy.
        #
        # Two models failed identically before this was understood, which sent
        # the diagnosis after the models. The model was never the variable.
        raise _timed_out(host, timeout) from e
    except OSError as e:
        # Enumerating exception types is what failed above. Anything else the
        # socket layer raises becomes a stated cause rather than a trace.
        raise IndexerError(f"Network error talking to Ollama at {host}: {e}",
                           verdict="ollama_unreachable",
                           remedy="Check that Ollama is running and reachable: "
                                  "ollama serve") from e


def context_limit(host: str, model: str) -> int:
    """
    Read the model's real context window rather than assuming one.

    Two sources can disagree -- the architecture's context_length and the
    Modelfile's num_ctx. nomic-embed-text reports 2048 and 8192 respectively.
    Take the lower: being wrong toward smaller chunks costs a little speed,
    being wrong toward larger costs silent truncation.
    """
    d = _post(host, "/api/show", {"model": model})
    info = d.get("model_info") or {}
    arch = next((v for k, v in info.items() if k.endswith(".context_length")), None)

    num_ctx = None
    for line in (d.get("parameters") or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx":
            try:
                num_ctx = int(parts[1])
            except ValueError:
                pass

    candidates = [v for v in (arch, num_ctx) if v]
    if not candidates:
        raise IndexerError(
            f"Could not determine a context limit for {model!r}. "
            "Refusing to guess -- a wrong limit here corrupts the index.",
            verdict="context_unknown")
    if arch and num_ctx and arch != num_ctx:
        _note(f"  note: {model} reports context_length={arch} but num_ctx="
              f"{num_ctx}; using {min(candidates)} as the safe bound.")
    return min(candidates)


def model_installed(host: str, model: str) -> bool:
    """
    Is this exact model present on the host?

    Asked before embedding a query, because the index records the model that
    built it and substituting a different one is the single most expensive
    mistake available here: the arithmetic succeeds, the ranking is well formed,
    and it is meaningless. A refusal is the only correct response.
    """
    try:
        d = _post(host, "/api/show", {"model": model}, timeout=30)
    except IndexerError:
        raise
    return not (isinstance(d, dict) and d.get("error"))


def embed_batch(host: str, model: str, texts: list[str]) -> list[list[float]]:
    d = _post(host, "/api/embed", {"model": model, "input": texts})
    if isinstance(d, dict) and d.get("error"):
        # Ollama reports a missing model as a 200 with an `error` key. Without
        # this branch that arrives below as "asked for 3, got 0", which is a
        # true sentence about the wrong problem.
        raise IndexerError(f"Ollama refused to embed with {model!r}: {d['error']}",
                           verdict="ollama_error")
    vecs = d.get("embeddings", [])
    if len(vecs) != len(texts):
        # One vector per input, always. A short return means some inputs were
        # dropped, and an index missing rows is worse than one that failed.
        raise IndexerError(
            f"Asked for {len(texts)} embeddings, got {len(vecs)}. Aborting "
            "rather than writing a partial index.",
            verdict="bad_response")
    return vecs


# ----------------------------------------------------------------- chunking


# A markdown ATX heading. Matched only outside fenced code, because `# note` is
# a comment in half the languages a corpus contains and treating one as a
# section title attaches a confident, wrong heading to every chunk after it.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def paragraphs(text: str) -> list[dict[str, Any]]:
    """
    Blank-line-separated blocks, each carrying its 1-based inclusive line range
    and the nearest heading at or above it.

    This exists because a citation cannot be recovered after the fact. Once a
    chunk is a bare string, the line range it came from is gone -- searching the
    file for the text back-references the wrong copy whenever the text repeats,
    and fails outright for a chunk that starts mid-word after an overlap. The
    line numbers have to be carried through from here.

    A block whose first line is a heading carries that heading itself, so the
    citation for a section's opening paragraph names the section rather than the
    one before it.
    """
    blocks: list[dict[str, Any]] = []
    heading = ""
    buf: list[str] = []
    start = 0
    fenced = False

    def flush(end_line: int) -> None:
        nonlocal buf, heading
        if not buf:
            return
        body = "\n".join(buf).strip()
        if body:
            m = _HEADING_RE.match(buf[0].strip()) if not fenced else None
            if m:
                heading = m.group(2)
            blocks.append({"text": body, "start": start, "end": end_line,
                           "heading": heading})
        buf = []

    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        if _FENCE_RE.match(line):
            fenced = not fenced
        if line.strip():
            if not buf:
                start = n
            buf.append(line)
        else:
            flush(n - 1)
    flush(len(lines))
    return blocks


def _origin_of_tail(blocks: list[dict[str, Any]], joined: str,
                    tail: str) -> tuple[int, str]:
    """
    Which source line the overlap tail starts on, and the heading in force
    there.

    The tail is a suffix of the joined block text, so its start is a character
    offset backwards from the end. Walking the blocks to convert that offset to
    a line number is exact, which matters: an overlapped chunk begins partway
    into a paragraph, and reporting the paragraph's own first line would cite
    text the chunk does not contain.
    """
    offset = len(joined) - len(tail)
    pos = 0
    for block in blocks:
        block_end = pos + len(block["text"])
        if offset <= block_end:
            within = max(0, offset - pos)
            return block["start"] + block["text"].count("\n", 0, within), block["heading"]
        pos = block_end + 2  # the "\n\n" separator between blocks
    last = blocks[-1]
    return last["start"], last["heading"]


def chunk_text(text: str, max_chars: int) -> list[dict[str, Any]]:
    """
    Split on paragraph boundaries, packing paragraphs into chunks under the
    limit, with a little overlap between them.

    Splitting on paragraphs rather than a fixed character count matters: a chunk
    that starts mid-sentence embeds to a muddled vector that retrieves badly for
    everything. Prose has natural seams; use them.

    Returns dicts of {text, heading, lines}. The packing arithmetic below is
    UNCHANGED from the version measured on 2026-08-26 -- only the bookkeeping
    around it is new, and `selftest` asserts the emitted text is identical to
    what the string-only version produced. Retrieval quality was tuned against
    this exact behaviour and a quiet change to it would show up as worse results
    with nothing to point at.
    """
    blocks = paragraphs(text)
    chunks: list[dict[str, Any]] = []
    current = ""
    # INVARIANT: current == "\n\n".join(b["text"] for b in current_blocks).
    # An overlapped chunk therefore carries the tail as its own first block,
    # with the line the tail actually starts on. Without that, the offset
    # arithmetic in _origin_of_tail is measuring against a different string than
    # the one it was handed -- and it would still return a plausible line
    # number, which is the failure mode this project keeps finding.
    current_blocks: list[dict[str, Any]] = []

    def emit() -> None:
        chunks.append({
            "text": current,
            "heading": current_blocks[0]["heading"],
            "lines": [current_blocks[0]["start"], current_blocks[-1]["end"]],
        })

    for block in blocks:
        para = block["text"]

        # A single paragraph over the limit has to be split on its own. Each
        # piece cites the lines IT occupies, not the whole block's range.
        #
        # Measured on the real HOMELAB corpus, 2026-08-30: a 204-line note with
        # no blank line anywhere is one block, and citing the block for every
        # slice gave `lines: [1, 204]` on each -- a citation pointing at the
        # whole file, which is true and useless. The pieces are character slices
        # of a string whose newlines are all present, so the real offset is a
        # newline count and there is no reason to approximate it.
        if len(para) > max_chars:
            if current:
                emit()
                current = ""
                current_blocks = []
            step = max_chars - CHUNK_OVERLAP
            for i in range(0, len(para), step):
                piece = para[i:i + max_chars]
                offset = i
                if i > 0:  # not the first slice -- trim a leading part-word
                    cut = piece.find(" ")
                    if cut != -1:
                        piece = piece[cut + 1:]
                        offset = i + cut + 1
                stop = min(offset + len(piece), len(para))
                chunks.append({
                    "text": piece,
                    "heading": block["heading"],
                    "lines": [block["start"] + para.count("\n", 0, offset),
                              block["start"] + para.count("\n", 0, stop)],
                })
            continue

        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
            current_blocks.append(block)
        else:
            emit()
            # Snap the overlap tail to a WORD boundary. A raw character slice
            # starts every subsequent chunk mid-word ("s on this box feed GPUs"),
            # which degrades the embedding and makes the chunk unreadable to a
            # reranker. Observed 2026-08-26: qwen3.6:35b scored a passage 2
            # ("directly answers") when shown it cleanly, and 0 when shown the
            # same text starting mid-word.
            tail = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
            if len(tail) < len(current):
                cut = tail.find(" ")
                tail = tail[cut + 1:] if cut != -1 else tail
            tail_line, tail_heading = _origin_of_tail(current_blocks, current, tail)
            current = f"{tail}\n\n{para}"
            current_blocks = [
                {"text": tail, "start": tail_line, "end": tail_line,
                 "heading": tail_heading},
                block,
            ]

    if current:
        emit()
    return chunks


# ------------------------------------------------- index location and identity


def validate_name(name: str) -> str:
    """An index name, or IndexerError. See NAME_RE for why this is not a path."""
    if not NAME_RE.match(name or ""):
        raise IndexerError(
            f"Invalid index name {name!r}. Names are lowercase letters, digits, "
            "underscore and hyphen -- no dots, no separators, no path. An index "
            "is named, not located.",
            verdict="invalid_request")
    return name


def resolve_index_dir(explicit: str | None = None) -> Path:
    """
    Where indexes live: --dir, else OLLAMA_MCP_INDEX_DIR, else refuse.

    Refusing rather than defaulting to the working directory is deliberate. The
    CLI's old `--out index.json` was CWD-relative, and an MCP server's working
    directory is chosen by the client -- so the same relative path meant two
    different files depending on who was asking. One named directory, shared by
    the CLI and the tools, is what makes "the tools can only reach indexes in
    that directory" a statement about a real place.
    """
    raw = explicit or os.environ.get(INDEX_DIR_ENV, "")
    if not raw.strip():
        raise IndexerError(
            f"No index directory. Set {INDEX_DIR_ENV} in the environment, or "
            "pass --dir. This is the same directory the MCP index_* tools read, "
            "and setting it is what registers them.",
            verdict="not_configured")
    return Path(raw).expanduser().resolve()


def relative_key(path: Path, root: Path) -> str:
    """
    A file's key in the index: relative to the corpus root, POSIX separators.

    Its own function so the normalisation can be asserted without a filesystem
    and without depending on which platform the test runs on. Written inline as
    `str(path.relative_to(root))`, this produced `INVENTORY\\07 Models\\...` on
    A001 -- paths meaningful on exactly one machine -- and no test on Linux
    could ever have noticed, because there `str()` already yields the right
    thing.
    """
    return path.relative_to(root).as_posix()


def index_path(name: str, index_dir: Path) -> Path:
    return index_dir / f"{validate_name(name)}{INDEX_SUFFIX}"


def compute_generation(model: str, doc_prefix: str, query_prefix: str,
                       chunk_chars: int, entries: list[dict[str, Any]]) -> str:
    """
    A short hash identifying WHAT IS IN the index, so a chunk id from one build
    cannot silently address a chunk in another.

    Covers the things that change chunk content or position: the format, the
    embedding model and its prefixes, the chunk size, and every file's path and
    digest. It deliberately excludes `name`, `description`, `source_root` and
    `built_at` -- none of them moves a chunk, and a generation that changed when
    nothing did would refuse ids that were still perfectly good, which teaches a
    caller to ignore the refusal.

    Fields are NUL-separated. Without a separator, ("ab", "c") and ("a", "bc")
    hash identically -- the same reading-across-a-seam mistake this project made
    once already in a different medium.
    """
    h = hashlib.sha256()
    for part in (str(INDEX_FORMAT), model, doc_prefix, query_prefix,
                 str(chunk_chars)):
        h.update(part.encode())
        h.update(b"\x00")
    for entry in entries:
        h.update(entry["file"].encode())
        h.update(b"\x00")
        h.update(entry["digest"].encode())
        h.update(b"\x00")
    return h.hexdigest()[:12]


def chunk_id(generation: str, ordinal: int) -> str:
    """
    `<generation>:<ordinal>`, DERIVED rather than stored.

    A stored id can disagree with the header it claims to be scoped to; a
    derived one cannot. The ordinal is the chunk's position in the flattened
    file order, which is deterministic for a given file set -- and any change to
    that set changes a digest, which changes the generation, which invalidates
    every id. Conservative in the safe direction.
    """
    return f"{generation}:{ordinal:04d}"


def iter_chunks(index: dict[str, Any]):
    """(ordinal, file, chunk) in the one canonical order ids are numbered by."""
    ordinal = 0
    for entry in index.get("files", []):
        for chunk in entry.get("chunks", []):
            yield ordinal, entry["file"], chunk
            ordinal += 1


def load_index(path: Path) -> dict[str, Any]:
    """
    Read an index, refusing anything this build cannot honestly serve.

    A v1 index is refused rather than degraded. It has no headings, no line
    ranges and no generation, so every citation it could produce would be a path
    and nothing else, and every id would be positional -- the exact failure the
    generation scoping exists to prevent. Reporting six fields as `unknown` and
    serving the rest would be a truthful label on an unusable answer.
    """
    if not path.exists():
        raise IndexerError(f"No index at {path}", verdict="index_not_found",
                           remedy="Call index_list to see what is available.")
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise IndexerError(f"{path.name} is not readable JSON: {e}",
                           verdict="index_unreadable") from e
    if not isinstance(index, dict):
        raise IndexerError(f"{path.name} is not an index",
                           verdict="index_unreadable")

    fmt = index.get("format")
    if fmt != INDEX_FORMAT:
        name = path.name[: -len(INDEX_SUFFIX)] if path.name.endswith(INDEX_SUFFIX) \
            else path.stem
        described = f"format {fmt}" if fmt else "an older, unversioned builder"
        raise IndexerError(
            f"{path.name} was written by {described}; this build reads format "
            f"{INDEX_FORMAT} only. It carries no headings, line ranges or "
            f"generation, so it cannot produce a citation.",
            verdict="unsupported_index",
            remedy=f"python vault_index.py build <folder> --name {name} "
                   f"--describe \"<what this is for>\" --rebuild")
    return index


# -------------------------------------------------------------------- build


def build(args) -> None:
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        raise IndexerError(f"Not a directory: {root}", verdict="invalid_request")

    name = validate_name(args.name)
    out_path = index_path(name, resolve_index_dir(getattr(args, "dir", None)))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.describe:
        # Not fatal, but say it every time. With several indexes on a host, the
        # description is the field an agent chooses between them on; an index
        # without one is a filename, and index_list will report it as such.
        print("  ! no --describe given. With more than one index this is the "
              "field a caller picks between them on.")

    limit_tokens = context_limit(args.host, args.model)
    hard_cap = int(limit_tokens * CHARS_PER_TOKEN)
    # Chunk for retrieval quality, never above what the model can actually read.
    max_chars = min(args.chunk_chars, hard_cap)
    doc_prefix, query_prefix = prefixes_for(args.model)
    print(f"Model {args.model}: {limit_tokens:,} tokens (hard cap {hard_cap:,} chars)")
    print(f"  chunking at {max_chars:,} chars for retrieval quality")
    if doc_prefix:
        print(f"  document prefix: {doc_prefix!r}")
    else:
        print("  no task prefix known for this model -- embedding text as-is")

    # Incremental: keep vectors for files whose content has not changed.
    # Re-embedding an unchanged vault every run is the difference between a
    # 3-second refresh and a 20-minute one.
    #
    # Reuse requires the same FORMAT as well as the same model: a v1 entry has
    # no headings or line ranges, and carrying one forward into a v2 index would
    # produce a file that passes every format check and cannot cite half its own
    # chunks.
    existing: dict[str, Any] = {}
    if out_path.exists() and not args.rebuild:
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8"))
            if (prior.get("format") == INDEX_FORMAT
                    and prior.get("model") == args.model
                    and prior.get("chunk_chars") == max_chars):
                existing = {e["file"]: e for e in prior.get("files", [])}
                print(f"  reusing {len(existing)} previously indexed files")
            else:
                print("  existing index has different settings or format; "
                      "re-embedding everything")
        except (json.JSONDecodeError, KeyError):
            print("  existing index unreadable; rebuilding from scratch")

    files = sorted(p for p in root.rglob("*.md") if p.is_file())
    if not files:
        raise IndexerError(f"No .md files under {root}", verdict="invalid_request")

    entries: list[dict[str, Any]] = []
    embedded = reused = 0

    for path in files:
        rel = relative_key(path, root)
        text = path.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]

        prior = existing.get(rel)
        if prior and prior.get("digest") == digest:
            entries.append(prior)
            reused += 1
            continue

        chunks = chunk_text(text, max_chars)
        if not chunks:
            continue

        vectors = []
        for i in range(0, len(chunks), args.batch):
            batch = [doc_prefix + c["text"] for c in chunks[i:i + args.batch]]
            vectors.extend(embed_batch(args.host, args.model, batch))

        entries.append({
            "file": rel,
            "digest": digest,
            "chunks": [{"text": c["text"], "heading": c["heading"],
                        "lines": c["lines"], "vector": v}
                       for c, v in zip(chunks, vectors)],
        })
        embedded += 1
        print(f"  {rel} -> {len(chunks)} chunk(s)")

    total_chunks = sum(len(e["chunks"]) for e in entries)
    generation = compute_generation(args.model, doc_prefix, query_prefix,
                                    max_chars, entries)
    out_path.write_text(
        json.dumps({"format": INDEX_FORMAT,
                    "name": name,
                    "description": args.describe or "",
                    "source_root": str(root),
                    "built_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"),
                    "builder_version": BUILDER_VERSION,
                    "generation": generation,
                    "model": args.model,
                    "doc_prefix": doc_prefix, "query_prefix": query_prefix,
                    "chunk_chars": max_chars,
                    "dimensions":
                    len(entries[0]["chunks"][0]["vector"]) if entries else 0,
                    "file_count": len(entries),
                    "chunk_count": total_chunks,
                    "files": entries}, ensure_ascii=False),
        encoding="utf-8", newline="\n",
    )
    print(f"\n{embedded} file(s) embedded, {reused} reused, "
          f"{total_chunks:,} chunks -> {out_path}")
    print(f"  generation {generation} -- chunk ids from earlier builds of this "
          f"index no longer resolve, by design.")


# ------------------------------------------------------------------- search


def cosine(a: list[float], b: list[float]) -> float:
    """
    Cosine similarity: the cosine of the angle between two vectors.

    1.0 means identical direction, 0.0 means unrelated. Magnitude is divided
    out, which is what makes a two-line note comparable to a two-page one --
    only the DIRECTION in meaning-space is being compared, not the length.
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# Two rubrics, kept side by side so the wording can be A/B'd against real
# queries rather than argued about. Wording is not cosmetic here: with `plain`,
# gemma3:4b scored 9 of 20 passages as direct answers to "why did the
# GPUs slow down" -- including a TDP spec table and an NVMe upgrade note. Nine
# passages tied at the top means the cosine tiebreak, not the model, chose the
# order, and the output looks reranked while being embedding order in a hat.
RUBRICS = {
    "plain": (
        "Use ONLY these three values. Do not use any other scale:\n"
        "  0 = same subject but does not answer the question\n"
        "  1 = partially answers\n"
        "  2 = directly answers\n\n"
    ),
    # `strict` was the obvious fix and MEASURED WORSE. Kept, not deleted, so
    # the result is not rediscovered: on the same query gemma3:4b still scored
    # 6 of 13 as direct answers (the same ~45% rate), and one batch abandoned
    # the format entirely and returned "2 3 0 1 4" -- a PERMUTATION of the
    # indices, i.e. a ranking, costing 7 candidates to a parse failure.
    #
    # Two things learned. Rubric wording is not the lever on saturation; the
    # rate held across both. And prompt complexity trades against format
    # compliance in a small model -- the more carefully the task was explained,
    # the less reliably it produced the requested shape.
    "strict": (
        "Use ONLY these three values. Do not use any other scale:\n"
        "  2 = states the cause, reason, or mechanism the question asks for. "
        "A reader could answer the question from this passage alone.\n"
        "  1 = contains part of the answer, or evidence bearing on it, but "
        "not the answer itself.\n"
        "  0 = about the same subject but does not answer the question. "
        "Specifications, hardware options, inventory, configuration and "
        "background are 0 no matter how closely related they are.\n\n"
        "Most passages are 0. Be strict: 2 is for a passage that actually "
        "answers, not one that is merely relevant.\n\n"
    ),
}


def _score_batch(host: str, model: str, query: str,
                 batch: list[tuple[float, str, str]],
                 rubric: str = "plain") -> tuple[dict, dict]:
    """Score ONE small batch. Returns ({local_index: score}, stats)."""
    numbered = []
    for i, (_, f, text) in enumerate(batch):
        snippet = " ".join(text.split())
        numbered.append(f"[{i}] ({f})\n{snippet}")
    listing = "\n\n".join(numbered)

    n = len(batch)
    prompt = (
        f"Question: {query}\n\n"
        f"Below are {n} numbered passages. Score each on how well it ANSWERS "
        f"the question -- not merely whether it is on the same topic.\n\n"
        + RUBRICS[rubric] +
        f"{listing}\n\n"
        f"Output EXACTLY {n} lines and nothing else. One line per passage, in "
        f"order, formatted as index=score. Every index from 0 to {n - 1} must "
        "appear exactly once. No prose, no JSON, no blank lines."
    )

    try:
        # /api/chat, NOT /api/generate: `generate` skips the model's chat
        # template, and template-sensitive families (Gemma) then emit reserved
        # vocabulary such as <unused50> instead of text.
        d = _post(host, "/api/chat", {
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # NO `format: json`. Forcing a JSON grammar broke two models:
            # gpt-oss:20b leaked reasoning into the object, gemma4:12b could not
            # satisfy the grammar at all and returned empty.
            "options": {"temperature": 0, "num_predict": 2000},
        }, timeout=120)
    except IndexerError as e:
        # One unreachable batch is not a failed rerank -- the others still
        # score, and the caller is told which ones did not. This was `except
        # SystemExit` back when _post exited the process; that spelling worked
        # only because the exit was being caught, which is not a design.
        return {}, {"error": str(e).splitlines()[0]}

    # Ollama reports failures as a 200 with an `error` key, not an HTTP error.
    if isinstance(d, dict) and d.get("error"):
        return {}, {"error": d["error"]}

    msg = d.get("message") or {}
    raw = msg.get("content") or d.get("response") or ""
    if not raw.strip() and msg.get("thinking"):
        raw = msg["thinking"]

    stats = {"eval": d.get("eval_count"), "dur": d.get("eval_duration"),
             "prompt": d.get("prompt_eval_count"), "load": d.get("load_duration"),
             "done": d.get("done_reason"), "raw": raw}

    # LINE FORMAT FIRST: no braces to balance, and a truncated reply stays
    # valid up to the cut. JSON yielded 2 of 20 pairs where lines yielded 20.
    pairs = re.findall(r'^\s*\[?(\d+)\]?\s*[=:]\s*(-?\d+(?:\.\d+)?)\s*$',
                       raw, re.M)
    if not pairs:
        pairs = re.findall(r'"?(\d+)"?\s*:\s*(-?\d+(?:\.\d+)?)', raw)

    out = {}
    for k, val in pairs:
        try:
            idx = int(k)
        except ValueError:
            continue
        # Drop indices outside this batch. A hallucinated index is not a score
        # for some other passage -- silently keeping it would corrupt the
        # mapping, which is the exact failure this batching is meant to fix.
        if 0 <= idx < n:
            out[idx] = float(val)
    return out, stats


def rerank(host: str, model: str, query: str,
           candidates: list[tuple[float, str, str]], keep: int,
           batch_size: int = 5, rubric: str = "plain",
           stats: dict[str, Any] | None = None) -> list[tuple]:
    """
    Re-order embedding candidates by asking a local model which ones actually
    ANSWER the question.

    WHY THIS EXISTS
    ---------------
    Cosine similarity measures topical overlap, not answerhood. A chunk densely
    about "GPUs in this machine" scores as well as one explaining why they
    slowed down, because both are equally *about GPUs*. Embeddings do recall,
    the model does precision. Nothing leaves the machine.

    WHY IT IS BATCHED
    -----------------
    Scoring all 20 candidates in one call sent ~8,300 prompt tokens to a small
    model and produced well-formed scores that did not correspond to the
    passages -- a NAS reset-button chunk was rated a direct answer to a GPU
    question while the chunk naming the cause fell out of the top five. The
    format was perfect and the judgement was noise, which is the dangerous
    combination: nothing looks broken.

    Small batches keep each prompt short enough for the model to track which
    passage is which. The rubric is absolute (0/1/2), not a ranking, so scores
    from separate batches remain comparable.
    """
    batches = [candidates[i:i + batch_size]
               for i in range(0, len(candidates), batch_size)]
    scores: dict = {}
    tok = dur = prompt_tok = 0
    load = 0.0
    failures = []

    for b, batch in enumerate(batches):
        local, st = _score_batch(host, model, query, batch, rubric)
        if st.get("error"):
            failures.append(f"batch {b + 1}: {st['error']}")
            continue
        if not local:
            snippet = " ".join(st.get("raw", "").split())[:120] or "(empty)"
            failures.append(f"batch {b + 1}: unparseable ({snippet})")
            continue
        # Map local index -> global position. This offset is the whole reason
        # batching is safe; get it wrong and the ranking is silently scrambled.
        offset = b * batch_size
        for i, v in local.items():
            scores[offset + i] = v
        if st.get("eval"):
            tok += st["eval"]
        if st.get("dur"):
            dur += st["dur"]
        if st.get("prompt"):
            prompt_tok += st["prompt"]
        if st.get("load"):
            load = max(load, st["load"])

    if tok and dur:
        parts = [f"{tok} tok in {dur/1e9:.1f}s = {tok/(dur/1e9):.1f} tok/s",
                 f"{len(batches)} batches of {batch_size}"]
        if prompt_tok:
            parts.append(f"{prompt_tok} prompt tok total")
        if load > 1e8:
            parts.append(f"{load/1e9:.1f}s cold load")
        _note("  " + " | ".join(parts))

    for f in failures:
        _note(f"  ! {f}")

    if not scores:
        _note("  ! rerank produced no usable scores -- showing embedding order")
        if stats is not None:
            stats.update(judged=0, of_pool=len(candidates),
                         distribution={}, failures=failures)
        return [(c[0], c[1], c[2], None, None, i)
                for i, c in enumerate(candidates[:keep])]

    lo, hi = min(scores.values()), max(scores.values())
    if hi > 2 or lo < 0:
        _note(f"  ! model used its own scale ({lo:g}-{hi:g}), not 0-2. "
              "Ordering is still the model's judgement; labels show raw scores.")
    # REPORT THE SPREAD, not just the count. A reranker that gives most
    # passages the same score has not ranked anything: the sort below then
    # falls through to the cosine tiebreak, and the output looks reranked while
    # being the embedding order with confident labels on it. Observed
    # 2026-08-26: every result in the top 5 came back as a direct answer,
    # including one that plainly was not.
    dist: dict = {}
    for val in scores.values():
        dist[val] = dist.get(val, 0) + 1
    spread = ", ".join(f"{k:g}={n}" for k, n in sorted(dist.items(), reverse=True))
    _note(f"  scored {len(scores)} of {len(candidates)} candidates "
          f"(spread: {spread})")

    top_val = max(dist)
    if dist[top_val] > len(scores) / 2:
        _note(f"  ! {dist[top_val]} of {len(scores)} scored {top_val:g} -- the "
              "model is barely discriminating, so ties fall back to embedding "
              "order. Treat the ranking within that group as unreranked.")

    if stats is not None:
        # Reported from the FULL pool, before the caller truncates to k.
        # index_search computed this itself from the returned rows and could
        # therefore never report more than k judged -- "5 of 20" when the model
        # had scored all twenty. And it is the distribution, not the count, that
        # says whether anything was ranked: `scored 20 of 20` measures
        # participation, and nine-way ties were reported as complete success for
        # two runs on that basis.
        stats.update(judged=len(scores), of_pool=len(candidates),
                     distribution={f"{k:g}": n for k, n in
                                   sorted(dist.items(), reverse=True)},
                     scale=[lo, hi], failures=failures)

    # Stable sort: model score first, embedding score as tiebreak. Unscored
    # candidates keep their embedding position -- unscored is not rejected.
    ordered = sorted(
        enumerate(candidates),
        key=lambda t: (-scores.get(t[0], -1.0), -t[1][0]),
    )
    # The sixth element is the candidate's ORIGINAL position, and it is the only
    # way back to the chunk a result came from once reranking has re-ordered
    # them. Without it a citation has to be recovered by matching text, which is
    # ambiguous exactly where a corpus repeats itself.
    return [(c[0], c[1], c[2], scores.get(i), (lo, hi), i)
            for i, c in ordered][:keep]


def embed_query(index: dict[str, Any], query: str, host: str) -> list[float]:
    """
    Embed a query with the model and prefix THIS INDEX was built with.

    Never a parameter, never a default, and never a substitution. Vectors from
    two different models are not comparable, but the arithmetic does not know
    that: cosine over mismatched vectors returns a complete, well-ordered,
    confident ranking that means nothing. There is no downstream signal, which
    is why the check is a refusal here rather than a warning later.
    """
    model = index["model"]
    if not model_installed(host, model):
        raise IndexerError(
            f"This index was built with {model!r}, which is not installed on "
            f"{host}. Refusing to embed the query with a different model: "
            "cross-model vectors produce a confident, meaningless ranking.",
            verdict="model_not_found",
            remedy=f"ollama pull {model}")
    # Take the query prefix from the INDEX, not from the current code, so a
    # search cannot drift from the build that produced the documents.
    return embed_batch(host, model, [index.get("query_prefix", "") + query])[0]


def score_all(index: dict[str, Any], qvec: list[float]) -> list[dict[str, Any]]:
    """Every chunk, scored and sorted. Carries the citation fields through."""
    scored = [
        {"cosine": cosine(qvec, ch["vector"]), "ordinal": ordinal, "file": path,
         "heading": ch.get("heading", ""), "lines": ch.get("lines"),
         "text": ch["text"]}
        for ordinal, path, ch in iter_chunks(index)
    ]
    scored.sort(reverse=True, key=lambda r: r["cosine"])
    return scored


def _cite(row: dict[str, Any]) -> str:
    """`path:12-40 (Heading)` -- what a reader or a later hydration needs."""
    lines = row.get("lines")
    where = f"{row['file']}:{lines[0]}-{lines[1]}" if lines else row["file"]
    return f"{where}  ({row['heading']})" if row.get("heading") else where


def search(args) -> None:
    index_dir = resolve_index_dir(getattr(args, "dir", None))
    index = load_index(index_path(args.index, index_dir))
    model = index["model"]

    qvec = embed_query(index, args.query, args.host)
    rows = score_all(index, qvec)
    # The rerank half still works in (cosine, file, text) tuples, unchanged.
    # Its sixth return element carries the position back, which is how a
    # reranked row is matched to the chunk it came from.
    scored = [(r["cosine"], r["file"], r["text"]) for r in rows]

    # --explain: where does a KNOWN chunk actually rank?
    #
    # Without this, a missing result is ambiguous in the worst way: the chunk
    # may be absent from the index, or present but out-ranked. Those need
    # opposite fixes (chunking/embedding vs pool size/reranking) and the
    # visible output cannot tell them apart. Observed 2026-08-26: the passage
    # headed "why CPU offload is not an option" did not appear in the top 20
    # for the near-verbatim query "why is CPU offload not an option", and there
    # was no way to see whether it ranked 21st or 300th.
    #
    # Literal substring match, deliberately -- this is the one place in the
    # tool where the question is "where is THIS text", not "what is similar".
    if getattr(args, "explain", None):
        needle = args.explain.lower()
        hits = [(rank, r["cosine"], _cite(r), r["text"])
                for rank, r in enumerate(rows, 1)
                if needle in r["text"].lower()]
        print(f'\n  --explain "{args.explain}"')
        if not hits:
            print("    NO CHUNK CONTAINS THIS TEXT. Either the file is not "
                  "indexed, or chunking split the phrase across a boundary.")
            print("    Re-run `build` and check the file is in scope before "
                  "blaming retrieval.\n")
        else:
            print(f"    {len(hits)} chunk(s) contain it, out of {len(scored):,}:")
            for rank, sc, f, txt in hits[:10]:
                pool_note = ""
                if args.rerank:
                    pool_note = (" [IN rerank pool]" if rank <= args.rerank_pool
                                 else " [OUTSIDE rerank pool]")
                print(f"    rank {rank:>4} of {len(scored):,}  cosine {sc:.3f}"
                      f"{pool_note}  {f}")
                print(f"           {' '.join(txt.split())[:150]}...")
            top = hits[0][0]
            if args.rerank and top > args.rerank_pool:
                print(f"    -> best match is rank {top}, outside the top "
                      f"{args.rerank_pool} sent to the reranker. This is a "
                      f"RECALL failure: no reranker can fix it. Widen "
                      f"--rerank-pool or improve chunking.")
            print()

    if args.rerank:
        pool = scored[:args.rerank_pool]

        # A narrow cosine spread across the pool means the embeddings are
        # barely ranking, which makes the pool BOUNDARY close to arbitrary --
        # the answer is as likely to sit at rank 32 as rank 3. That is exactly
        # when a reranker is most valuable and most likely to be starved of the
        # right candidate, because reranking cannot recover what recall missed.
        #
        # Measured 2026-08-26, query "why is CPU offload not an option":
        # top-20 spread 0.036, answer at rank 32 (0.590) vs top hit 0.634.
        # --rerank-pool 40 put it at rank 1. Same model, same prompt.
        if len(pool) > 1:
            pool_spread = pool[0][0] - pool[-1][0]
            if pool_spread < 0.05:
                print(f"  ! pool cosine spread is only {pool_spread:.3f} -- "
                      f"embeddings are barely ranking, so the top "
                      f"{args.rerank_pool} cut is close to arbitrary.")
                print(f"    If the answer is missing, it is probably just "
                      f"outside the pool: try --rerank-pool "
                      f"{args.rerank_pool * 2}, or --explain \"some exact "
                      f"phrase\" to see where it actually ranks.")

        print(f"  reranking top {len(pool)} with {args.rerank} (local)...")
        reranked = rerank(args.host, args.rerank, args.query, pool, args.top,
                          batch_size=args.rerank_batch,
                          rubric=args.rerank_rubric)
        judged = [r[3] for r in reranked if len(r) > 3 and r[3] is not None]
        if judged and max(judged) == 0:
            # The non-answer rule: "nothing here answers the question" is a
            # real answer and must be stated. Five rows of zeros without a word
            # implies a ranking was produced when the model said there was
            # nothing to rank.
            print(f"  ! the reranker scored EVERY candidate 0 -- it judges that "
                  f"nothing in the top {len(pool)} answers this question.")
            print("    Rows below are embedding order. Consider rephrasing, or "
                  "accept that the corpus may not contain the answer.\n")
        print(f'"{args.query}"  ({model}, {len(scored):,} chunks, reranked)\n')
        for item in reranked:
            score, text = item[0], item[2]
            rs = item[3] if len(item) > 3 else None
            bounds = item[4] if len(item) > 4 else None
            # item[5] is the candidate's position in `rows`, which is where the
            # heading and line range live. Matching on the text instead would
            # pick the wrong copy wherever a corpus repeats a passage.
            file = _cite(rows[item[5]]) if len(item) > 5 else item[1]
            snippet = " ".join(text.split())[:220]

            if rs is None:
                # Genuinely not scored by the model -- distinct from scored-zero,
                # and it must not be reported as a judgement that was never made.
                mark = "not judged"
            elif bounds and bounds[1] > 2:
                # Model used its own scale. Show the raw number rather than
                # forcing it into a vocabulary it never agreed to.
                mark = f"{rs:g}/{bounds[1]:g}"
            else:
                mark = {2.0: "ANSWERS", 1.0: "partial", 0.0: "topic only"}[rs]

            print(f"  {score:.3f}  [{mark:^11}]  {file}")
            print(f"         {snippet}...\n")
        return

    top = rows[:args.top]
    spread = (top[0]["cosine"] - top[-1]["cosine"]) if len(top) > 1 else 0.0
    print(f'"{args.query}"  ({model}, {len(rows):,} chunks, '
          f'generation {index["generation"]}, built {index["built_at"]})')
    if not index.get("query_prefix") and prefixes_for(model)[1]:
        print("  ! index built WITHOUT task prefixes -- rebuild for better ranking")
    if spread < 0.06:
        # nomic vectors are not centred, so absolute scores sit high even for
        # unrelated text. Only the SPREAD carries information, and a flat top-N
        # means the ranking is not discriminating -- report that rather than
        # letting a confident-looking 0.58 imply a good match.
        print(f"  ! top-{len(top)} spread is only {spread:.3f} -- these results are "
              "barely distinguished. Treat the ranking as weak.")
    print()
    for r in top:
        snippet = " ".join(r["text"].split())[:220]
        print(f"  {r['cosine']:.3f}  {chunk_id(index['generation'], r['ordinal'])}"
              f"  {_cite(r)}")
        print(f"         {snippet}...\n")


# ------------------------------------------------------------------- status


def status(args) -> None:
    """
    Re-hash the corpus and name what changed since the index was built.

    THE DEEP CHECK, and it is a CLI command rather than a tool for one reason:
    it opens every source file. `index_list` and `index_search` report
    `built_at` and point here; neither of them estimates staleness, because
    "probably current" derived from a timestamp is a guess presented as a fact,
    and it is a guess about exactly the thing a caller is trusting.

    Exits 1 on drift so it can gate a script, 2 when the corpus cannot be
    reached from this host -- which is a different answer from "unchanged" and
    must not collapse into it.
    """
    index_dir = resolve_index_dir(getattr(args, "dir", None))
    index = load_index(index_path(args.index, index_dir))

    root = Path(index.get("source_root", ""))
    print(f"{index['name']}  generation {index['generation']}  "
          f"built {index['built_at']}")
    print(f"  source_root: {root}")
    if not root.is_dir():
        # An absolute path recorded on the machine that built the index. On any
        # other host it is a fact about somewhere else, and saying so is the
        # whole answer -- reporting "no changes" here would be true of nothing.
        print("  ! source_root is not a directory on this host. Staleness "
              "cannot be checked from here; this is not the same as unchanged.")
        raise SystemExit(2)

    stored = {e["file"]: e["digest"] for e in index.get("files", [])}
    current: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*.md") if p.is_file()):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Same normalisation as build(), or every file reads as removed-and-added.
        current[relative_key(path, root)] = hashlib.sha256(
            text.encode()).hexdigest()[:16]

    added = sorted(set(current) - set(stored))
    removed = sorted(set(stored) - set(current))
    changed = sorted(f for f in set(stored) & set(current)
                     if stored[f] != current[f])

    print(f"  {len(stored)} indexed, {len(current)} on disk: "
          f"{len(changed)} changed, {len(added)} added, {len(removed)} removed")
    if not (added or removed or changed):
        print("  index matches the corpus.")
        return

    for label, group in (("changed", changed), ("added", added),
                         ("removed", removed)):
        for f in group[:20]:
            print(f"    {label:>7}  {f}")
        if len(group) > 20:
            print(f"    {'':>7}  ... and {len(group) - 20} more")

    print(f"\n  Rebuild:  python vault_index.py build {root} "
          f"--name {index['name']} --describe \"{index.get('description', '')}\"")
    raise SystemExit(1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    # `--dir` is repeated on each subcommand rather than factored into a helper.
    # Per-subcommand because `vault_index.py --dir X build ...` is a shape people
    # get wrong; written out because generate_ds_sections.py reads THIS
    # construction to produce the DS's CLI table, and an add_argument call made
    # through a helper is invisible to it -- the flag vanished from the document
    # while --check still passed.

    b = sub.add_parser("build", help="build or refresh an index")
    b.add_argument("--dir", default=None,
                   help="directory holding <name>.index.json files "
                        "(default: $OLLAMA_MCP_INDEX_DIR)")
    b.add_argument("folder")
    b.add_argument("--name", required=True,
                   help="index name: lowercase letters, digits, _ and - only. "
                        "Written as <name>.index.json in the index directory")
    b.add_argument("--describe", default="",
                   help="what this corpus is for, in a sentence. With more than "
                        "one index this is the field a caller chooses between "
                        "them on; an index without one is a filename")
    b.add_argument("--model", default=DEFAULT_MODEL)
    b.add_argument("--host", default=DEFAULT_HOST)
    b.add_argument("--batch", type=int, default=16)
    b.add_argument("--chunk-chars", type=int, default=TARGET_CHUNK_CHARS,
                   dest="chunk_chars",
                   help="target chunk size in characters (default %(default)s); "
                        "capped by the model's real context window")
    b.add_argument("--rebuild", action="store_true",
                   help="ignore any existing index and re-embed everything")
    b.set_defaults(func=build)

    s = sub.add_parser("search", help="query an index")
    s.add_argument("--dir", default=None,
                   help="directory holding <name>.index.json files "
                        "(default: $OLLAMA_MCP_INDEX_DIR)")
    s.add_argument("index", metavar="NAME", help="index name, not a path")
    s.add_argument("query")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--explain", metavar="SUBSTRING", default=None,
                   help="report where chunks containing this literal text rank, "
                        "to separate a recall failure from a ranking failure")
    s.add_argument("--host", default=DEFAULT_HOST)
    # Bare `--rerank` uses gpt-oss:20b. It was gemma3:4b until measurement said
    # otherwise: the 4B model scores ~45% of candidates as direct answers, so
    # its output is embedding order with confident labels on it. gpt-oss:20b
    # ranks correctly at ~28s; qwen3.6:35b is more precise on ambiguous queries
    # at ~102s. Those timings are from one host; re-measure on your own.
    s.add_argument("--rerank", metavar="MODEL", nargs="?", const="gpt-oss:20b",
                   default=None,
                   help="rerank results with a local chat model, e.g. gemma3:4b. "
                        "Prefer a model WITHOUT the `thinking` capability -- a "
                        "reasoning wrapper breaks the JSON response")
    s.add_argument("--rerank-rubric", choices=sorted(RUBRICS), default="plain",
                   dest="rerank_rubric",
                   help="scoring rubric wording (default %(default)s). "
                        "`strict` was measured WORSE -- see RUBRICS.")
    s.add_argument("--rerank-batch", type=int, default=5, dest="rerank_batch",
                   help="passages scored per model call (default %(default)s). "
                        "Large batches give well-formed but unreliable scores.")
    s.add_argument("--rerank-pool", type=int, default=20, dest="rerank_pool",
                   help="how many embedding candidates to rerank (default %(default)s)")
    s.set_defaults(func=search)

    st = sub.add_parser("status",
                        help="re-hash the corpus and name what changed")
    st.add_argument("--dir", default=None,
                   help="directory holding <name>.index.json files "
                        "(default: $OLLAMA_MCP_INDEX_DIR)")
    st.add_argument("index", metavar="NAME", help="index name, not a path")
    st.set_defaults(func=status)

    args = p.parse_args()
    try:
        args.func(args)
    except IndexerError as exc:
        # The CLI turns a raised failure back into an exit. The server does not:
        # it turns the same exception into a refusal with a verdict. One failure
        # path, two presentations.
        sys.exit(f"{exc}\n  {exc.remedy}" if exc.remedy else str(exc))


if __name__ == "__main__":
    main()
