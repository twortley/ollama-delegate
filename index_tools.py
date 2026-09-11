#!/usr/bin/env python3
"""
The agent-facing surface over `vault_index.py`: four read-only tools that let a
model drive semantic retrieval without a human pasting PowerShell output.

    index_list()                          what corpora exist, and how stale
    index_search(index, query, k)         citations, not content
    index_get(index, ids)                 hydrate the few that mattered
    index_explain(index, query, text)     recall failure or ranking failure

THE POINT: CONTEXT COST IS THE SIZE OF THE RETURN VALUE
-------------------------------------------------------
Nothing else. What a program reads on the host never touches the model's
window; only what the tool hands back does. Measured against a 403-chunk
corpus:

    reading it through the Obsidian MCP        ~200,000 tokens
    index_search returning 10 citations           ~300 tokens
    index_get on the 2 chunks that mattered     ~1,000 tokens

Three orders of magnitude, same information. And the reranking step spends a
DIFFERENT model's context: forty candidates are read and scored by a local chat
model, in its window, not the caller's.

WHY CITATIONS RATHER THAN CONTENT
---------------------------------
Returning content forces `k` to be a context-budget decision made before
anything is known about relevance -- ask for 10 and you pay for 10, including
the 7 that were noise. Citations move that decision after the evidence: search
cheaply, look at paths and scores, hydrate the two that matter. A citation also
composes, because it is a path and a line range: hydration can come from
index_get, from another MCP server, or from a human opening the file. Content
in a return value composes with nothing.

THE RULE THIS MODULE OBEYS
--------------------------
Anything that touches the corpus is a CLI operation; MCP reads the index. No
tool here accepts a filesystem path, and none opens a source file -- index_get
hydrates from chunk text stored inside the index. Building and the deep
staleness check are `vault_index.py` commands, because both read arbitrary
files under a folder the operator chose.

That is what makes "no MCP tool reads your filesystem" structural rather than
enforced. A guard that checks a parameter can be bypassed by a new tool that
forgets to call it; a parameter that does not exist cannot be.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import vault_index as vi
from ollama_server import Guard, Refused, _location, _ok, _refusal

# Bounds on what one call can hand back. `k` is the one genuinely unbounded
# parameter in this surface, and the byte cap is the backstop for the tool that
# returns text: a caller asking for forty chunks by id would otherwise flood
# its own context with a single well-formed, entirely successful call.
K_MAX = 25
RESPONSE_BYTE_CAP = 60_000

# Candidates sent to the reranker. This default is a COMPROMISE between two
# measured facts that contradict each other, and neither can be argued away:
#
#   * Pool 20 misses the answer on at least one real query. Baseline Run 3:
#     *why is CPU offload not an option* failed on every model at pool 20, and
#     not on judgement -- the answering passage sat at cosine rank 32 of 403 and
#     was never in the pool. Pool 40 put it at rank 1 with nothing else changed.
#   * Pool 40 does not fit inside an MCP client's request timeout. Baseline
#     Run 4: it timed out with the reranker already warm (7.8s cold load
#     confirmed separately), while pool 20 on the same model returned in time.
#
# So 20 is chosen because a default that always times out is worse than one that
# is sometimes short -- a tool whose first call fails is a tool nobody calls
# twice. The shortfall is made VISIBLE rather than accepted: the response carries
# the score distribution, and when nothing is judged a direct answer it says so
# and names the wider pool. index_explain then reports whether the passage you
# expected fell outside the boundary.
#
# **The CLI is not bound by the CLIENT's timeout**, which is what matters here:
# `vault_index.py search --rerank-pool 40` is the supported way to run the
# measured-correct width. It is NOT unbounded -- each scoring batch has its own
# 120s limit, and saying otherwise sent an operator to a path that then crashed
# (2026-08-31, ENV-2). Exceeding it now returns a stated cause and a remedy.
RERANK_POOL = 20

# The top of the 0/1/2 rubric. A rerank whose best score is below this judged
# nothing to be a direct answer, which is the signal that the pool may have been
# too narrow -- distinct from the reranker having looked and found nothing.
RUBRIC_TOP = 2.0

# Upper bound on a caller-supplied pool. Reranking is O(pool) model calls and
# the whole surface is meant to be cheap; past this, use the CLI.
POOL_MAX = 80

# What a caller gets from index_search without asking. Used by index_explain to
# tell "you would have seen this" apart from "the ranking buried it".
DEFAULT_K = 10

DEFAULT_RERANK_MODEL = "gpt-oss:20b"

# One slot, keyed on (path, mtime, size). An index is a single JSON file loaded
# whole, so a search-then-hydrate cycle would otherwise parse it twice; a
# 403-chunk index is a few tens of MB of vectors. One slot rather than a dict
# because holding several indexes resident is a memory decision nobody made.
# This state belongs to this module and is shared with nothing in the bridge.
_CACHE: dict[str, Any] = {"key": None, "index": None}


def _load(index_dir: Path, name: str) -> dict[str, Any]:
    """Read an index by name, from cache when the file has not moved."""
    path = vi.index_path(name, index_dir)
    if not path.exists():
        raise vi.IndexerError(
            f"No index named {name!r} in {index_dir}.",
            verdict="index_not_found",
            remedy="Call index_list to see what is available.")
    stat = path.stat()
    key = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
    if _CACHE["key"] != key:
        # vi.load_index refuses a v1 index. It is refused rather than degraded:
        # a v1 index has no headings, no line ranges and no generation, so every
        # result it could return would be a path and nothing else, and every id
        # would be positional. Reporting six fields as `unknown` and serving the
        # rest is a truthful label on an answer that cannot be used.
        _CACHE["index"] = vi.load_index(path)
        _CACHE["key"] = key
    return _CACHE["index"]


def _header(index: dict[str, Any]) -> dict[str, Any]:
    """
    The index's own account of itself. Every field is READ, never inferred.

    A missing field reports `unknown`. A fabricated `built_at` would be a wrong
    fact with good provenance, and staleness is precisely where that does
    damage -- it is the field a caller uses to decide whether to trust the rest.
    """
    def got(key: str) -> Any:
        value = index.get(key)
        return value if value not in (None, "") else "unknown"

    return {
        "name": got("name"),
        "description": got("description"),
        # What was indexed. UR-12 names it: an index built without a
        # description is otherwise identifiable only by name, model and date.
        "source_root": got("source_root"),
        "built_at": got("built_at"),
        "generation": got("generation"),
        "files": got("file_count"),
        "chunks": got("chunk_count"),
        "model": got("model"),
        "builder_version": got("builder_version"),
    }


def _fits(payload: dict[str, Any]) -> bool:
    return len(json.dumps(payload, ensure_ascii=False).encode()) <= RESPONSE_BYTE_CAP


def register(mcp: Any, config: Any) -> None:
    """Probe-registry shape: one register() per module."""
    guard = Guard(config)
    index_dir = Path(config.index_dir).expanduser().resolve()

    def _staleness_note(index: dict[str, Any]) -> str:
        """
        Report when it was built and name the command that checks. Never
        estimate. "Probably current" derived from a timestamp is a guess
        presented as a fact, and the deep check re-hashes the corpus, which is
        a CLI operation by the rule this module obeys.
        """
        return (f"Built {index.get('built_at', 'unknown')}. Whether that is "
                f"stale is not knowable from here: run "
                f"`vault_index.py status {index.get('name')}` to re-hash the "
                f"sources and see what changed.")

    def _embed(index: dict[str, Any], query: str) -> list[float]:
        """Guard the model, then embed with the index's own model and prefix."""
        guard.check("index_search", model=index["model"])
        return vi.embed_query(index, query, config.base_url)

    # ------------------------------------------------------------------ list

    @mcp.tool()
    def index_list() -> dict[str, Any]:
        """
        List the semantic indexes this server can read.

        The entry point when more than one index exists, and a few dozen tokens
        for the whole directory. Read `description` before choosing: separate
        corpora are the expected case, mixing them degrades retrieval, and
        results from an index built on a different embedding model are not
        comparable with results from this one.

        Every field comes from the index header. A field the index does not
        carry is reported as "unknown" rather than guessed.
        """
        try:
            if not index_dir.is_dir():
                return _refusal(Refused(
                    "not_configured",
                    f"OLLAMA_MCP_INDEX_DIR points at {index_dir}, which is not a "
                    "directory on this host.",
                    "Create it, or point the variable at the directory holding "
                    "your <name>.index.json files, and restart the MCP client."))

            indexes, unreadable = [], []
            for path in sorted(index_dir.glob(f"*{vi.INDEX_SUFFIX}")):
                name = path.name[: -len(vi.INDEX_SUFFIX)]
                try:
                    index = _load(index_dir, name)
                except vi.IndexerError as exc:
                    # Named, not skipped. A caller told an index does not exist
                    # will rebuild the wrong thing; one told it exists and cannot
                    # be read has the actual problem in front of it.
                    unreadable.append({"name": name, "verdict": exc.verdict,
                                       "error": str(exc), "remedy": exc.remedy})
                    continue
                entry = _header(index)
                entry["location"] = _location(index.get("model", ""))
                indexes.append(entry)

            return _ok(
                index_dir=str(index_dir),
                indexes=indexes,
                unreadable=unreadable,
                note=("`location` is where the EMBEDDING model ran. An index built "
                      "through a cloud model means the corpus already left this "
                      "host. Staleness: each entry carries `built_at`; "
                      "`vault_index.py status <name>` is the check."),
            )
        except vi.IndexerError as exc:
            return _refusal(Refused(exc.verdict, str(exc), exc.remedy))
        except Exception as exc:                      # noqa: BLE001
            # index_list had NO handler at all until 2026-08-31 -- the fourth
            # instance of the same class found that day. Globbing a directory
            # can raise PermissionError or OSError, and an index_list that
            # crashes is worse than the others: it is the tool a caller uses to
            # find out what went wrong.
            return _refusal(Refused(
                "internal_error",
                f"{type(exc).__name__}: {exc}",
                "This is a defect in ollama-delegate, not a configuration "
                "problem. The verdict and message above are what a report needs."))

    # ---------------------------------------------------------------- search

    @mcp.tool()
    def index_search(index: str, query: str, k: int = 10,
                     rerank: bool = True,
                     rerank_model: str = DEFAULT_RERANK_MODEL,
                     pool: int = RERANK_POOL) -> dict[str, Any]:
        """
        Semantic search over an index. Returns CITATIONS, not content.

        Each result is a chunk id, a path, the nearest heading, a line range
        and its scores -- enough to judge relevance, and enough to hydrate
        afterwards with index_get on just the ones that matter. That ordering
        is the point: asking for content up front spends context on the results
        that turn out to be noise.

        Args:
            index: an index name from index_list. Not a path.
            query: a real question, not keywords. The embedding model was
                trained on natural language and the reranker is asked which
                passages ANSWER the question.
            k: how many citations to return, 1..25.
            rerank: score the candidate pool with a local chat model. Cosine
                measures topical overlap, not answerhood -- a chunk densely
                about GPUs scores as well as the one explaining why they
                throttled. Costs ~30s on a 20B model and is usually worth it.
            rerank_model: the scoring model. Prefer one WITHOUT a thinking
                wrapper; a small model will comply with the output format long
                after it has stopped tracking which passage is which.
            pool: how many embedding candidates the reranker sees, 1..80.
                **This is the recall boundary and no reranker can recover what
                it never saw.** Widen it when index_explain reports that the
                passage you wanted fell outside the pool. Narrow it only to buy
                latency, knowing what it costs.
        """
        try:
            if not isinstance(k, int) or not 1 <= k <= K_MAX:
                raise Refused(
                    "invalid_request",
                    f"k must be between 1 and {K_MAX}; got {k!r}.",
                    "Search cheaply and hydrate deliberately: a large k spends "
                    "context on results you have not looked at yet.")
            if not isinstance(pool, int) or not 1 <= pool <= POOL_MAX:
                raise Refused(
                    "invalid_request",
                    f"pool must be between 1 and {POOL_MAX}; got {pool!r}.",
                    "The pool is the recall boundary, not a throughput knob. "
                    "Past this, use the vault_index.py CLI.")
            idx = _load(index_dir, vi.validate_name(index))
            qvec = _embed(idx, query)
            rows = vi.score_all(idx, qvec)
            if not rows:
                return _ok(index=index, results=[],
                           note=f"{index} contains no chunks.")

            generation = idx["generation"]
            spread = rows[0]["cosine"] - rows[min(k, len(rows)) - 1]["cosine"]
            reranked_by = None
            order = rows[:k]

            if rerank:
                guard.check("index_search", model=rerank_model)
                candidates = [(r["cosine"], r["file"], r["text"])
                              for r in rows[:pool]]
                # Filled by rerank() from the FULL pool. Computing these here,
                # from the returned rows, was a bug: the return is truncated to
                # k, so "judged" could never exceed k and reported 5 of 20 when
                # the model had scored twenty.
                run: dict[str, Any] = {}
                scored = vi.rerank(config.base_url, rerank_model, query,
                                   candidates, k, stats=run)
                # The sixth element is the candidate's position in `rows`.
                # Matching results back by text would pick the wrong copy
                # wherever the corpus repeats a passage, and a citation to the
                # wrong copy is indistinguishable from a correct one.
                order = [rows[item[5]] for item in scored]
                judgements = [item[3] for item in scored if item[3] is not None]
                reranked_by = {
                    "model": rerank_model,
                    "location": _location(rerank_model),
                    "judged": run.get("judged"),
                    "of_pool": run.get("of_pool", len(candidates)),
                    # The load-bearing diagnostic, and it was missing entirely.
                    # A count measures PARTICIPATION; only the distribution says
                    # whether anything was ranked. `gemma3:4b` once returned
                    # twenty well-formed scores in 1.5s with nine tied at the
                    # top, so the cosine tiebreak ordered the visible results
                    # and the output looked like a complete success.
                    "distribution": run.get("distribution"),
                }
                if run.get("failures"):
                    reranked_by["failures"] = run["failures"]
                spent = run.get("distribution") or {}
                top_value = max(spent, key=lambda v: float(v)) if spent else None
                if top_value and spent[top_value] > (run.get("judged") or 0) / 2:
                    reranked_by["weak_discrimination"] = (
                        f"{spent[top_value]} of {run.get('judged')} candidates "
                        f"scored {top_value}. Ties fall back to embedding order, "
                        "so treat the ranking within that group as unreranked.")
                scale_top = (run.get("scale") or [0, RUBRIC_TOP])[1]
                if (judgements and scale_top <= RUBRIC_TOP
                        and 0 < max(judgements) < RUBRIC_TOP
                        and len(candidates) < POOL_MAX):
                    # Something was partially relevant but nothing answered.
                    # That is the shape of a pool cut too narrow -- the answer
                    # is often just outside it, which is precisely the Run 3
                    # failure. Say so rather than presenting partial matches as
                    # the best available answer.
                    reranked_by["pool_may_be_short"] = (
                        f"Nothing in the top {len(candidates)} was judged a "
                        "direct answer, only partial matches. The answering "
                        "passage is often just outside a narrow pool. Retry "
                        f"with pool={min(len(candidates) * 2, POOL_MAX)} (slower, "
                        "and may exceed your client's timeout), use "
                        "index_explain with a phrase you expect to find, or run "
                        f"`vault_index.py search <index> \"{query}\" --rerank "
                        f"--rerank-pool {min(len(candidates) * 2, POOL_MAX)}` on "
                        "the CLI, which is not bounded by the client's timeout "
                        "but does apply its own 120s per scoring batch.")
                if judgements and max(judgements) == 0:
                    # "Nothing here answers the question" is a real answer and
                    # has to be said. Rows of zeros without a word implies a
                    # ranking was produced when the model said there was
                    # nothing to rank.
                    reranked_by["verdict"] = (
                        "The reranker scored EVERY candidate 0: it judges that "
                        f"nothing in the top {len(candidates)} answers this "
                        "question. The order below is embedding order.")
                for item in scored:
                    rows[item[5]]["rerank"] = item[3]

            results = [{
                "id": vi.chunk_id(generation, r["ordinal"]),
                "path": r["file"],
                "heading": r.get("heading", ""),
                "lines": r.get("lines"),
                "cosine": round(r["cosine"], 3),
                "rerank": r.get("rerank"),
            } for r in order]

            payload = _ok(
                index=index,
                generation=generation,
                built_at=idx.get("built_at", "unknown"),
                model=idx["model"],
                location=_location(idx["model"]),
                chunks_searched=len(rows),
                spread=round(spread, 3),
                reranked_by=reranked_by,
                results=results,
                note=_staleness_note(idx),
            )
            if spread < 0.06:
                # nomic vectors are not centred, so absolute scores sit high
                # even for unrelated text. Only the SPREAD carries information:
                # a flat top-k is not a ranking, and a confident-looking 0.58
                # would otherwise imply a good match.
                payload["weak_ranking"] = (
                    f"Top-{len(results)} cosine spread is only {spread:.3f}. "
                    "These results are barely distinguished; treat the order "
                    "as weak and consider index_explain with a phrase you "
                    "expect to find.")
            return payload
        except Refused as exc:
            return _refusal(exc)
        except vi.IndexerError as exc:
            return _refusal(Refused(exc.verdict, str(exc), exc.remedy))
        except Exception as exc:                      # noqa: BLE001
            # UR-06 BY CONSTRUCTION, NOT BY ENUMERATION.
            #
            # The two clauses above are the enumeration, and on 2026-08-31 a
            # TimeoutError -- a sibling of URLError, not a subclass -- walked
            # past both and reached an operator as a bare tool error. The
            # specific hole is fixed in vault_index._post. This clause exists
            # because the NEXT unenumerated type is not knowable, and the
            # requirement is that a failure states a cause and a remedy.
            #
            # It names the exception class rather than swallowing it: a bug
            # reported as `internal_error: KeyError: 'model'` is diagnosable,
            # and one reported as "something went wrong" is not.
            return _refusal(Refused(
                "internal_error",
                f"{type(exc).__name__}: {exc}",
                "This is a defect in ollama-delegate, not a configuration "
                "problem. The verdict and message above are what a report "
                "needs. Retrieval itself is unaffected: re-run without "
                "--rerank, or use index_explain, to keep working."))

    # ------------------------------------------------------------------- get

    @mcp.tool()
    def index_get(index: str, ids: list[str]) -> dict[str, Any]:
        """
        Return the text of named chunks, from the index itself.

        The only tool here that returns corpus text, and it is meant to be
        called after a search, on the two or three citations that turned out to
        matter. It opens no file under your corpus: chunk text is stored inside
        the index.

        Ids are generation-scoped. An id from a build before the last rebuild is
        REFUSED, not resolved -- the same ordinal in a rebuilt index is a
        different passage under the same path, and hydrating it would be
        confidently wrong with no error anywhere.

        Args:
            index: an index name from index_list.
            ids: chunk ids exactly as index_search returned them.
        """
        try:
            idx = _load(index_dir, vi.validate_name(index))
            generation = idx["generation"]
            if not ids:
                raise Refused("invalid_request", "No ids given.",
                              "Call index_search first and pass the ids it "
                              "returned.")

            wanted: dict[int, str] = {}
            stale, malformed = [], []
            for raw in ids:
                gen, _, ordinal = str(raw).partition(":")
                if not ordinal.isdigit():
                    malformed.append(raw)
                elif gen != generation:
                    stale.append(raw)
                else:
                    wanted[int(ordinal)] = raw

            if stale:
                raise Refused(
                    "stale_id",
                    f"{len(stale)} id(s) are scoped to a different generation "
                    f"of {index!r} (this index is {generation}): "
                    f"{', '.join(stale[:5])}. The index has been rebuilt since "
                    "those were issued, so the same ordinals now address "
                    "different passages.",
                    f"Re-run index_search on {index!r} and use the ids it "
                    "returns now.")
            if malformed:
                raise Refused(
                    "invalid_request",
                    f"Not chunk ids: {', '.join(str(m) for m in malformed[:5])}.",
                    "Ids look like `a91f3c7d2e04:0187` and come from "
                    "index_search.")

            found, chunks, truncated = set(), [], []
            for ordinal, path, chunk in vi.iter_chunks(idx):
                if ordinal not in wanted:
                    continue
                found.add(ordinal)
                candidate = {
                    "id": wanted[ordinal],
                    "path": path,
                    "heading": chunk.get("heading", ""),
                    "lines": chunk.get("lines"),
                    "text": chunk["text"],
                }
                # Cap the response, and SAY SO. Returning fewer chunks than
                # asked for without a word is the failure; naming the ones that
                # did not fit is not.
                if _fits({"chunks": chunks + [candidate]}):
                    chunks.append(candidate)
                else:
                    truncated.append(wanted[ordinal])

            missing = [wanted[o] for o in sorted(set(wanted) - found)]
            payload = _ok(index=index, generation=generation, chunks=chunks)
            if missing:
                payload["not_in_index"] = missing
            if truncated:
                payload["omitted_for_size"] = truncated
                payload["note"] = (
                    f"{len(truncated)} chunk(s) were left out to keep the "
                    f"response under {RESPONSE_BYTE_CAP:,} bytes. Ask for them "
                    "in a second call.")
            return payload
        except Refused as exc:
            return _refusal(exc)
        except vi.IndexerError as exc:
            return _refusal(Refused(exc.verdict, str(exc), exc.remedy))
        except Exception as exc:                      # noqa: BLE001
            # UR-06 BY CONSTRUCTION, NOT BY ENUMERATION.
            #
            # The two clauses above are the enumeration, and on 2026-08-31 a
            # TimeoutError -- a sibling of URLError, not a subclass -- walked
            # past both and reached an operator as a bare tool error. The
            # specific hole is fixed in vault_index._post. This clause exists
            # because the NEXT unenumerated type is not knowable, and the
            # requirement is that a failure states a cause and a remedy.
            #
            # It names the exception class rather than swallowing it: a bug
            # reported as `internal_error: KeyError: 'model'` is diagnosable,
            # and one reported as "something went wrong" is not.
            return _refusal(Refused(
                "internal_error",
                f"{type(exc).__name__}: {exc}",
                "This is a defect in ollama-delegate, not a configuration "
                "problem. The verdict and message above are what a report "
                "needs. Retrieval itself is unaffected: re-run without "
                "--rerank, or use index_explain, to keep working."))

    # --------------------------------------------------------------- explain

    @mcp.tool()
    def index_explain(index: str, query: str, text: str) -> dict[str, Any]:
        """
        Where does a passage you KNOW exists actually rank for this query?

        Use this when a search did not return something you expected. A missing
        result is ambiguous in the worst way: the passage may be absent from the
        index, or present and out-ranked. Those are a RECALL failure and a
        RANKING failure, they need opposite fixes, and ordinary search output
        cannot tell them apart.

        Matching is literal substring, deliberately -- this is the one place in
        the surface where the question is "where is THIS text", not "what is
        similar to this".

        Args:
            index: an index name from index_list.
            query: the query that disappointed you.
            text: an exact phrase you expect to be indexed.
        """
        try:
            idx = _load(index_dir, vi.validate_name(index))
            qvec = _embed(idx, query)
            rows = vi.score_all(idx, qvec)
            needle = (text or "").lower()
            if not needle:
                raise Refused("invalid_request",
                              "No text to look for.",
                              "Pass an exact phrase you expect to find.")

            hits = [{
                "rank": rank,
                "of": len(rows),
                "id": vi.chunk_id(idx["generation"], r["ordinal"]),
                "path": r["file"],
                "heading": r.get("heading", ""),
                "lines": r.get("lines"),
                "cosine": round(r["cosine"], 3),
                "in_rerank_pool": rank <= RERANK_POOL,
            } for rank, r in enumerate(rows, 1) if needle in r["text"].lower()]

            if not hits:
                return _ok(
                    index=index, query=query, hits=[],
                    diagnosis="recall_failure_or_not_indexed",
                    explanation=(
                        "NO CHUNK CONTAINS THIS TEXT. Either the file is not "
                        "in this index, or chunking split the phrase across a "
                        "boundary. No amount of reranking can fix either. "
                        "Check the file is under the indexed folder and "
                        "rebuild before blaming retrieval."))

            best = hits[0]
            if best["rank"] <= DEFAULT_K:
                # NOT a failure. Reporting "the ordering is the problem" for a
                # passage already at rank 1 was this tool's own version of the
                # trap it exists to catch: a confident diagnosis of a condition
                # that is not present. Observed 2026-08-30 on the real corpus.
                diagnosis, explanation = "no_failure", (
                    f"The passage ranks {best['rank']} of {len(rows)} and a "
                    f"default search returns {DEFAULT_K}, so index_search "
                    "already surfaces it. Nothing here needs fixing -- if you "
                    "did not see it, check the k you asked for, or whether the "
                    "reranker demoted it below what you read.")
            elif best["in_rerank_pool"]:
                diagnosis, explanation = "ranking_failure", (
                    f"The passage is indexed and ranks {best['rank']} of "
                    f"{len(rows)} -- below the {DEFAULT_K} a default search "
                    f"returns, but inside the top {RERANK_POOL} sent to the "
                    "reranker. Recall is fine; the ordering is the problem. A "
                    "better reranking model, or a sharper query, is the lever.")
            else:
                diagnosis, explanation = "recall_failure", (
                    f"The passage is indexed but ranks {best['rank']} of "
                    f"{len(rows)}, OUTSIDE the top {RERANK_POOL} sent to the "
                    "reranker. No reranker can fix this -- it never sees the "
                    "candidate. The fix is chunking or a wider pool, not a "
                    "better scoring model. The CLI takes `--rerank-pool "
                    f"{RERANK_POOL * 2}`.")

            return _ok(index=index, query=query, hits=hits[:10],
                       diagnosis=diagnosis, explanation=explanation)
        except Refused as exc:
            return _refusal(exc)
        except vi.IndexerError as exc:
            return _refusal(Refused(exc.verdict, str(exc), exc.remedy))
        except Exception as exc:                      # noqa: BLE001
            # UR-06 BY CONSTRUCTION, NOT BY ENUMERATION.
            #
            # The two clauses above are the enumeration, and on 2026-08-31 a
            # TimeoutError -- a sibling of URLError, not a subclass -- walked
            # past both and reached an operator as a bare tool error. The
            # specific hole is fixed in vault_index._post. This clause exists
            # because the NEXT unenumerated type is not knowable, and the
            # requirement is that a failure states a cause and a remedy.
            #
            # It names the exception class rather than swallowing it: a bug
            # reported as `internal_error: KeyError: 'model'` is diagnosable,
            # and one reported as "something went wrong" is not.
            return _refusal(Refused(
                "internal_error",
                f"{type(exc).__name__}: {exc}",
                "This is a defect in ollama-delegate, not a configuration "
                "problem. The verdict and message above are what a report "
                "needs. Retrieval itself is unaffected: re-run without "
                "--rerank, or use index_explain, to keep working."))
