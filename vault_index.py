#!/usr/bin/env python3
"""
Semantic search index over a folder of markdown, using a local Ollama
embedding model. Nothing leaves the machine.

    python vault_index.py build  ~/vault --out index.json
    python vault_index.py search index.json "why did the NAS fill up"

Dependencies: none. stdlib only, same as the MCP server it ships with.

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
from pathlib import Path
from typing import Any

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "nomic-embed-text:latest"

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


# ---------------------------------------------------------------- transport


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
        sys.exit(f"Cannot reach Ollama at {host}: {e.reason}\n"
                 f"Is it running? Try: ollama serve")


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
        sys.exit(f"Could not determine a context limit for {model!r}. "
                 "Refusing to guess -- a wrong limit here corrupts the index.")
    if arch and num_ctx and arch != num_ctx:
        print(f"  note: {model} reports context_length={arch} but num_ctx="
              f"{num_ctx}; using {min(candidates)} as the safe bound.")
    return min(candidates)


def embed_batch(host: str, model: str, texts: list[str]) -> list[list[float]]:
    d = _post(host, "/api/embed", {"model": model, "input": texts})
    vecs = d.get("embeddings", [])
    if len(vecs) != len(texts):
        # One vector per input, always. A short return means some inputs were
        # dropped, and an index missing rows is worse than one that failed.
        sys.exit(f"Asked for {len(texts)} embeddings, got {len(vecs)}. Aborting "
                 "rather than writing a partial index.")
    return vecs


# ----------------------------------------------------------------- chunking


def chunk_text(text: str, max_chars: int) -> list[str]:
    """
    Split on paragraph boundaries, packing paragraphs into chunks under the
    limit, with a little overlap between them.

    Splitting on paragraphs rather than a fixed character count matters: a chunk
    that starts mid-sentence embeds to a muddled vector that retrieves badly for
    everything. Prose has natural seams; use them.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paras:
        # A single paragraph over the limit has to be split on its own.
        if len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            step = max_chars - CHUNK_OVERLAP
            for i in range(0, len(para), step):
                piece = para[i:i + max_chars]
                if i > 0:  # not the first slice -- trim a leading part-word
                    cut = piece.find(" ")
                    piece = piece[cut + 1:] if cut != -1 else piece
                chunks.append(piece)
            continue

        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            chunks.append(current)
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
            current = f"{tail}\n\n{para}"

    if current:
        chunks.append(current)
    return chunks


# -------------------------------------------------------------------- build


def build(args) -> None:
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")

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
    out_path = Path(args.out)
    existing: dict[str, Any] = {}
    if out_path.exists() and not args.rebuild:
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8"))
            if prior.get("model") == args.model:
                existing = {e["file"]: e for e in prior.get("files", [])}
                print(f"  reusing {len(existing)} previously indexed files")
        except (json.JSONDecodeError, KeyError):
            print("  existing index unreadable; rebuilding from scratch")

    files = sorted(p for p in root.rglob("*.md") if p.is_file())
    if not files:
        sys.exit(f"No .md files under {root}")

    entries: list[dict[str, Any]] = []
    embedded = reused = 0

    for path in files:
        rel = str(path.relative_to(root))
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
            batch = [doc_prefix + c for c in chunks[i:i + args.batch]]
            vectors.extend(embed_batch(args.host, args.model, batch))

        entries.append({
            "file": rel,
            "digest": digest,
            "chunks": [{"text": c, "vector": v} for c, v in zip(chunks, vectors)],
        })
        embedded += 1
        print(f"  {rel} -> {len(chunks)} chunk(s)")

    total_chunks = sum(len(e["chunks"]) for e in entries)
    out_path.write_text(
        json.dumps({"model": args.model,
                    "doc_prefix": doc_prefix, "query_prefix": query_prefix,
                    "chunk_chars": max_chars,
                    "dimensions":
                    len(entries[0]["chunks"][0]["vector"]) if entries else 0,
                    "files": entries}, ensure_ascii=False),
        encoding="utf-8", newline="\n",
    )
    print(f"\n{embedded} file(s) embedded, {reused} reused, "
          f"{total_chunks:,} chunks -> {out_path}")


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
    except SystemExit:
        return {}, {"error": "unreachable"}

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
           batch_size: int = 5, rubric: str = "plain") -> list[tuple]:
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
        print("  " + " | ".join(parts))

    for f in failures:
        print(f"  ! {f}")

    if not scores:
        print("  ! rerank produced no usable scores -- showing embedding order")
        return candidates[:keep]

    lo, hi = min(scores.values()), max(scores.values())
    if hi > 2 or lo < 0:
        print(f"  ! model used its own scale ({lo:g}-{hi:g}), not 0-2. "
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
    print(f"  scored {len(scores)} of {len(candidates)} candidates "
          f"(spread: {spread})")

    top_val = max(dist)
    if dist[top_val] > len(scores) / 2:
        print(f"  ! {dist[top_val]} of {len(scores)} scored {top_val:g} -- the "
              "model is barely discriminating, so ties fall back to embedding "
              "order. Treat the ranking within that group as unreranked.")

    # Stable sort: model score first, embedding score as tiebreak. Unscored
    # candidates keep their embedding position -- unscored is not rejected.
    ordered = sorted(
        enumerate(candidates),
        key=lambda t: (-scores.get(t[0], -1.0), -t[1][0]),
    )
    return [(c[0], c[1], c[2], scores.get(i), (lo, hi)) for i, c in ordered][:keep]


def search(args) -> None:
    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    model = index["model"]

    # The query MUST use the model that built the index. Vectors from different
    # models are not comparable -- the arithmetic still works and returns
    # confident nonsense, which is the worst kind of wrong.
    # Take the query prefix from the INDEX, not from the current code. If the
    # index was built before prefixes existed, it carries none and the query
    # gets none -- consistent with its documents, which is what matters.
    query_prefix = index.get("query_prefix", "")
    qvec = embed_batch(args.host, model, [query_prefix + args.query])[0]

    scored = [
        (cosine(qvec, ch["vector"]), entry["file"], ch["text"])
        for entry in index["files"]
        for ch in entry["chunks"]
    ]
    scored.sort(reverse=True, key=lambda t: t[0])

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
        hits = [(rank, sc, f, txt) for rank, (sc, f, txt) in enumerate(scored, 1)
                if needle in txt.lower()]
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
            score, file, text = item[0], item[1], item[2]
            rs = item[3] if len(item) > 3 else None
            bounds = item[4] if len(item) > 4 else None
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

    top = scored[:args.top]
    spread = (top[0][0] - top[-1][0]) if len(top) > 1 else 0.0
    print(f'"{args.query}"  ({model}, {len(scored):,} chunks)')
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
    for score, file, text in top:
        snippet = " ".join(text.split())[:220]
        print(f"  {score:.3f}  {file}")
        print(f"         {snippet}...\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build or refresh an index")
    b.add_argument("folder")
    b.add_argument("--out", default="index.json")
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
    s.add_argument("index")
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

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
