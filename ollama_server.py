#!/usr/bin/env python3
"""
Ollama delegation MCP server.

A local stdio MCP server that lets Claude delegate work to an Ollama instance
running on this machine. Claude orchestrates; the local model does the grunt
work; nothing sent to it leaves the host.

Design rules this server holds itself to:

  No new dependencies beyond the MCP SDK. HTTP is stdlib urllib.

  A single enforcement point. Here it is `Guard.check()`. Concentrating every
  permission decision in one function means a new tool cannot bypass the rules
  by forgetting to check, and the whole enforcement surface reads at a glance.

  Tolerate both MCP SDK generations (FastMCP / MCPServer).

  THE NON-ANSWER RULE. A non-answer must never be reported as a good answer.
  Every failure returns a distinguishable verdict, never an empty success --
  and "I did not check" is reported as itself, never collapsed into "fine".
  This is the rule most of the code below is shaped by, and it is referred to
  by name throughout.

One rule departed from, deliberately:

  Read-only does NOT hold here. `pull_model` and `delete_model` change host
  state, so BOTH are refused unless explicitly enabled in the server process
  environment. Config cannot enable either; only the environment can.
  See `Guard.check()`.

  A wrong `delete` remains worse than a wrong `pull` -- they keep distinct
  remedies -- but on a build a stranger may run, "recoverable" is not a property
  this code can assume about someone else's disk or connection.

Environment:
  OLLAMA_HOST                base URL, default http://localhost:11434
  OLLAMA_MCP_ALLOW_DELETE    "1" to permit delete_model. Default off.
  OLLAMA_MCP_ALLOW_PULL      "1" to permit pull_model. Default off.
  OLLAMA_MCP_MODELS          optional comma-separated allowlist of model names.
                             Unset means any locally-present model is callable.
  OLLAMA_MCP_TIMEOUT         seconds for generate/chat/embed. Default 300.
  OLLAMA_MCP_PULL_TIMEOUT    seconds for pull. Default 3600.

Run:
  python ollama_server.py            # stdio MCP server
  python ollama_server.py --selftest # assertions, exits non-zero on failure
  python ollama_server.py --probe    # live check against the configured host
  python ollama_server.py --version  # the release this file belongs to
"""

from __future__ import annotations

# The release this file belongs to. server_info reports it, so a result
# captured through a client can name the code that produced it. A checkout
# between releases reports the last release it descends from.
__version__ = "1.1.0"

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Logging. stderr only -- anything on stdout corrupts the MCP stdio framing.
# --------------------------------------------------------------------------

log = logging.getLogger("ollama-mcp")
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
log.addHandler(_handler)
log.setLevel(os.environ.get("OLLAMA_MCP_LOGLEVEL", "INFO"))
log.propagate = False


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


# Declared minimum supported interpreter. Checked at runtime rather than left to
# fail on its own, because the natural failure is a SyntaxError or an ImportError
# from somewhere inside the file, which tells a user nothing about what to do.
#
# This check can only speak if the file PARSES, so the codebase deliberately uses
# no 3.10-only syntax: no `match`/`case`, no PEP 604 unions outside annotations,
# and `from __future__ import annotations` in every module.
MIN_PYTHON = (3, 10)
if sys.version_info < MIN_PYTHON:
    raise SystemExit(
        f"ollama-delegate requires Python {'.'.join(map(str, MIN_PYTHON))} or "
        f"newer. This interpreter is {sys.version.split()[0]} "
        f"({sys.executable}).")


# This file is normally executed as a script, which means it is imported under
# the name `__main__`. `index_tools` imports `ollama_server` for Guard and the
# response helpers -- and without the alias below that import would EXECUTE THE
# FILE A SECOND TIME, producing a second Refused class that `except Refused`
# does not catch. Two module objects, identical source, silently different
# identity. Registering the alias makes the later import resolve to this module.
sys.modules.setdefault("ollama_server", sys.modules[__name__])


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    base_url: str = "http://localhost:11434"
    allow_delete: bool = False
    allow_pull: bool = False
    model_allowlist: tuple[str, ...] = field(default_factory=tuple)
    timeout: float = 300.0
    pull_timeout: float = 3600.0
    # Directory of `<name>.index.json` files. Unset means the index_* tools are
    # NOT REGISTERED AT ALL, so a default clone exposes exactly the ten-tool
    # bridge the README documents. Setting it does three jobs at once: it opts
    # the tools in, it is the allowlist (only indexes in here are reachable),
    # and it answers "where does the index live" -- which the CLI previously
    # left relative to a working directory an MCP client chooses.
    index_dir: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        raw_models = os.environ.get("OLLAMA_MCP_MODELS", "").strip()
        models = tuple(m.strip() for m in raw_models.split(",") if m.strip())
        raw_index_dir = os.environ.get("OLLAMA_MCP_INDEX_DIR", "").strip()
        return cls(
            base_url=_normalise_base_url(
                os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ),
            allow_delete=_env_flag("OLLAMA_MCP_ALLOW_DELETE", False),
            allow_pull=_env_flag("OLLAMA_MCP_ALLOW_PULL", False),
            model_allowlist=models,
            timeout=float(os.environ.get("OLLAMA_MCP_TIMEOUT", "300")),
            pull_timeout=float(os.environ.get("OLLAMA_MCP_PULL_TIMEOUT", "3600")),
            index_dir=raw_index_dir or None,
        )


# --------------------------------------------------------------------------
# Cloud-model detection
# --------------------------------------------------------------------------

# Ollama can register hosted models that execute on Ollama's infrastructure
# rather than locally. Their manifest is a pointer stub of a few hundred bytes
# rather than weights, so they are indistinguishable from local models in a
# plain listing -- same name shape, same call, same response.
#
# These are NOT refused. Calling one is a legitimate choice the owner makes for
# workloads that need a frontier model, and Ollama supports it natively.
#
# They ARE labelled, on every listing and every inference response, because
# "where did this run" is exactly the kind of fact the non-answer rule says
# must be
# distinguishable rather than assumed. The risk was never that cloud models
# exist; it is an agent picking one off a list because it sounded capable,
# without anyone intending content to leave the host. Labelling makes the
# choice informed; a gate would have made it someone else's.
_CLOUD_TAGS = frozenset({"cloud"})

# A real model's weights are megabytes at minimum; a manifest stub is a few
# hundred bytes. Used only to CORROBORATE the tag, never to override it -- if
# the two ever disagree that is a finding worth surfacing, not a tiebreak.
_STUB_SIZE_CEILING = 1_048_576  # 1 MiB


def _is_cloud_model(model: str) -> bool:
    """
    True if the model name marks it as Ollama-hosted rather than local.

    Detection is on the tag, not on size: size is not available everywhere a
    name is (the caller passes a name, not a listing), and a future stub could
    be padded. The tag is what Ollama itself uses to mark these.
    """
    _, _, tag = model.strip().partition(":")
    return tag.lower() in _CLOUD_TAGS


def _location(model: str, size_bytes: int | None = None) -> str:
    """
    Where this model runs: "local", "cloud", or "cloud?" when the tag and the
    size disagree.

    The third value exists because collapsing a disagreement into a confident
    answer is the non-answer failure. A stub-sized model without a cloud tag is not
    a local model and must not be reported as one.
    """
    tagged_cloud = _is_cloud_model(model)
    stub_sized = size_bytes is not None and size_bytes < _STUB_SIZE_CEILING
    if tagged_cloud:
        return "cloud"
    if stub_sized:
        return "cloud?"
    return "local"


def _normalise_base_url(raw: str) -> str:
    """
    Accept the shapes people actually type. Ollama's own docs and shell exports
    use a bare host:port, so `OLLAMA_HOST=localhost:11434` is common and must
    not silently produce a broken URL.

    Refuses any scheme other than http/https -- fail closed rather than hand a
    file:// or gopher:// URL to urlopen.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("OLLAMA_HOST is empty")
    if "://" not in raw:
        raw = "http://" + raw
    # No rstrip("/") here, deliberately: it turns a scheme-only "http://" into
    # "http:", which then fails the "://" test and gets a SECOND scheme glued
    # on, yielding the valid-looking "http://http:". Returning scheme://netloc
    # below already discards any path or trailing slash, so the strip was never
    # doing work -- only hiding this case.
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"OLLAMA_HOST scheme {parsed.scheme!r} refused; "
            "only http and https are permitted"
        )
    if not parsed.hostname:
        raise ValueError(f"OLLAMA_HOST {raw!r} has no host")
    return f"{parsed.scheme}://{parsed.netloc}"


# --------------------------------------------------------------------------
# The enforcement function (one place, auditable at a glance)
# --------------------------------------------------------------------------


class Refused(Exception):
    """Raised when the guard refuses a call. Carries a self-explaining reason."""

    def __init__(self, verdict: str, reason: str, remedy: str = ""):
        self.verdict = verdict
        self.reason = reason
        self.remedy = remedy
        super().__init__(reason)


# The two write gates, their tool names and the flag that opens each. ONE source:
# Guard.check raises with `_gate_remedy`, and server_info reports the same text.
# A state with no remedy is what sent a client inventing
# ALLOW_MODEL_PULL and a `confirm` parameter, both plausible and neither real.
WRITE_GATES: dict[str, tuple[str, str]] = {
    "pull": ("pull_model", "OLLAMA_MCP_ALLOW_PULL"),
    "delete": ("delete_model", "OLLAMA_MCP_ALLOW_DELETE"),
}


def _gate_remedy(flag: str) -> str:
    """How to open a gate, worded once and used wherever it is reported."""
    return (f"Set {flag}=1 in the server environment and "
            "restart the MCP client. This is deliberately not settable "
            "from a tool call or a config file.")


class Guard:
    """
    Every state-changing or model-naming call routes through here.

    Concentrating it means a new tool cannot quietly bypass the model by
    forgetting to check.
    """

    def __init__(self, config: Config):
        self.config = config

    def check(self, op: str, model: str | None = None) -> None:
        # -- write surface gating -------------------------------------------
        if op == "delete" and not self.config.allow_delete:
            raise Refused(
                "not_permitted",
                "delete_model is disabled on this server.",
                _gate_remedy(WRITE_GATES["delete"][1]),
            )
        if op == "pull" and not self.config.allow_pull:
            raise Refused(
                "not_permitted",
                "pull_model is disabled on this server.",
                _gate_remedy(WRITE_GATES["pull"][1]),
            )

        # -- model allowlist -------------------------------------------------
        if model is not None:
            if not model or not model.strip():
                raise Refused(
                    "invalid_request",
                    "A model name is required and was empty.",
                    "Call list_models to see what is installed.",
                )
            if self.config.model_allowlist:
                # Ollama treats "llama3" and "llama3:latest" as the same model.
                # Compare on both forms so an allowlist entry without a tag
                # does not fail closed against its own :latest.
                if _base_name(model) not in {
                    _base_name(m) for m in self.config.model_allowlist
                }:
                    raise Refused(
                        "not_permitted",
                        f"Model {model!r} is not on this server's allowlist.",
                        "Allowed: " + ", ".join(self.config.model_allowlist),
                    )


def _base_name(model: str) -> str:
    """Strip an explicit :latest so tagged and untagged names compare equal."""
    m = model.strip()
    return m[: -len(":latest")] if m.endswith(":latest") else m


# --------------------------------------------------------------------------
# HTTP transport (stdlib only -- no new dependencies)
# --------------------------------------------------------------------------


def _request(
    config: Config,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """
    One HTTP call to Ollama, returning parsed JSON.

    Raises Refused with a *distinguishable* verdict for every failure mode
    (the non-answer rule). "Could not reach Ollama" and "that model does not
    exist" send the
    reader to completely different problems and must never collapse into one
    generic error.
    """
    url = config.base_url + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()

    try:
        with urllib.request.urlopen(req, timeout=timeout or config.timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        if exc.code == 404:
            raise Refused(
                "model_not_found",
                f"Ollama returned 404 for {path}: {detail}",
                "Call list_models to see what is installed, or pull_model first.",
            ) from exc
        raise Refused(
            "ollama_error",
            f"Ollama returned HTTP {exc.code} for {path}: {detail}",
            "",
        ) from exc
    except urllib.error.URLError as exc:
        raise Refused(
            "ollama_unreachable",
            f"Could not reach Ollama at {config.base_url}: {exc.reason}",
            "Check that Ollama is running (`ollama serve`) and that "
            "OLLAMA_HOST points at it.",
        ) from exc
    except TimeoutError as exc:
        raise Refused(
            "timeout",
            f"Ollama did not respond within "
            f"{timeout or config.timeout:.0f}s for {path}.",
            "Raise OLLAMA_MCP_TIMEOUT, or use a smaller model.",
        ) from exc

    elapsed = time.monotonic() - started
    log.debug("%s %s -> %d bytes in %.1fs", method, path, len(body), elapsed)

    if not body.strip():
        # An empty 200 is exactly the shape the non-answer rule warns about: it would
        # otherwise be reported as a successful call that returned nothing.
        raise Refused(
            "empty_response",
            f"Ollama returned an empty body for {path} with no error.",
            "This usually means the model was evicted mid-request. Retry once.",
        )

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise Refused(
            "bad_response",
            f"Ollama returned non-JSON for {path}: {body[:300]}",
            "",
        ) from exc

    # Ollama reports some failures as HTTP 200 with an `error` key rather than
    # an error status. Without this, the symptom is an instant empty response
    # and a null done_reason -- indistinguishable from a fast model that
    # generated nothing, and giving no hint that the server explained itself.
    if isinstance(parsed, dict) and parsed.get("error"):
        raise Refused(
            "ollama_error",
            f"Ollama returned an error for {path}: {parsed['error']}",
            "The server accepted the request and refused it. The message above "
            "is verbatim from Ollama.",
        )

    return parsed


def _retrieval_tools_note(config: Config) -> str:
    """
    What server_info says about the index_* tools, including when they are off.

    A005, 2026-08-29: a diligent client reads server_info and never makes the
    refused call, which makes this the load-bearing surface and the verdict
    strings the backstop. An agent that cannot see the tools has no way to tell
    "not configured on this host" from "not supported by this server", and
    those have different remedies -- one is an environment variable, the other
    is a different server.
    """
    if config.index_dir:
        return (f"registered, reading {config.index_dir}. index_list, "
                "index_search, index_get, index_explain.")
    return ("not registered. Set OLLAMA_MCP_INDEX_DIR to a directory of "
            "<name>.index.json files and restart the MCP client. Indexes are "
            "built with the vault_index.py CLI; no tool builds one, and no "
            "tool opens a corpus file.")


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"verdict": "ok", **kwargs}


def _refusal(exc: Refused) -> dict[str, Any]:
    out = {"verdict": exc.verdict, "error": exc.reason}
    if exc.remedy:
        out["remedy"] = exc.remedy
    return out


# --------------------------------------------------------------------------
# Context budgeting
# --------------------------------------------------------------------------

# Ollama does NOT error when input exceeds a model's context. It TRUNCATES and
# returns a normal-looking response. For generation that yields an answer to a
# question the model only half saw; for embeddings it yields a valid 768-dim
# vector representing the first fragment of a document. Both read as success.
#
# This is the non-answer rule's worst case: not a non-answer reported as good, but a
# PARTIAL answer reported as complete, with no signal anywhere in the response.
# The limit is per-model and spans two orders of magnitude on this host alone
# (nomic-embed-text 2,048 vs gemma4:12b 262,144), so it cannot be assumed.

# Characters per token. English averages ~4; code, JSON, non-Latin scripts and
# long identifiers run denser. 3.2 is deliberately pessimistic -- an estimate
# used as a safety limit must err toward refusing a call that would have fit,
# never toward allowing one that silently truncates.
_CHARS_PER_TOKEN = 3.2

# Fraction of the window reserved for the response and chat template overhead.
_OUTPUT_HEADROOM = 0.15


def _estimate_tokens(text: str) -> int:
    """
    Rough upper-bound token count. This is an ESTIMATE and is labelled as one
    everywhere it surfaces -- Ollama exposes no tokenizer endpoint, so an exact
    count is not available to this server. Being wrong in the safe direction is
    the whole design.
    """
    return int(len(text) / _CHARS_PER_TOKEN) + 1


class ContextCache:
    """
    Per-model context limits, fetched once per process from /api/show.

    A stdio server lives for one MCP client session and models do not
    change size mid-session, so a process-lifetime cache needs no invalidation.
    """

    def __init__(self, config: Config):
        self.config = config
        self._limits: dict[str, dict[str, Any]] = {}

    def limit_for(self, model: str) -> dict[str, Any]:
        """
        Returns {"limit": int|None, "source": str, "disagreement": str|None,
        "capabilities": list[str]|None}.

        `limit` is None when it genuinely could not be determined. Callers must
        treat that as "unknown", never as "unlimited" -- defaulting an unknown
        limit to a generous number produces a wrong answer wearing good
        provenance, which is the non-answer rule's most dangerous shape.
        `capabilities` rides on the same /api/show call, and None means the
        same thing: not read, so no check may pass or fail against it.
        """
        if model in self._limits:
            return self._limits[model]

        entry: dict[str, Any] = {"limit": None, "source": "unavailable",
                                 "disagreement": None, "capabilities": None}
        try:
            d = _request(self.config, "POST", "/api/show", {"model": model})
        except Refused as exc:
            entry["source"] = f"unavailable ({exc.verdict})"
            self._limits[model] = entry
            return entry

        caps = d.get("capabilities")
        if isinstance(caps, list):
            entry["capabilities"] = [str(c) for c in caps]

        info = d.get("model_info") or {}
        arch_limit = next(
            (v for k, v in info.items() if k.endswith(".context_length")), None
        )

        # The Modelfile may set num_ctx, which can DISAGREE with the
        # architecture value -- nomic-embed-text reports context_length 2048
        # and num_ctx 8192 on one host. Surface the disagreement, do
        # not silently pick a winner. The lower value is used because it is the
        # safe one, and the conflict is reported alongside it.
        num_ctx = None
        for line in (d.get("parameters") or "").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "num_ctx":
                try:
                    num_ctx = int(parts[1])
                except ValueError:
                    pass

        if arch_limit is not None and num_ctx is not None and arch_limit != num_ctx:
            entry["disagreement"] = (
                f"model_info reports {arch_limit} but num_ctx is {num_ctx}; "
                f"using the lower value ({min(arch_limit, num_ctx)}) as the "
                "safe bound. Verify before relying on the larger figure."
            )

        candidates = [v for v in (arch_limit, num_ctx) if v]
        if candidates:
            entry["limit"] = min(candidates)
            entry["source"] = "model_info/num_ctx"

        self._limits[model] = entry
        return entry


def _check_context(
    cache: ContextCache,
    model: str,
    text: str,
    allow_truncation: bool,
    headroom: float = _OUTPUT_HEADROOM,
) -> dict[str, Any] | None:
    """
    Pre-flight budget check. Returns a refusal dict, or None to proceed.

    Refuses by DEFAULT rather than warning, because the harm is silent: a
    truncated call succeeds and its output is indistinguishable from a whole
    one. `allow_truncation=True` makes proceeding an explicit, recorded choice
    -- the same "chosen versus assumed" line drawn for cloud models above.
    """
    info = cache.limit_for(model)
    limit = info["limit"]
    estimate = _estimate_tokens(text)

    if limit is None:
        # Unknown is reported, never defaulted. The call proceeds because
        # refusing every model whose limit cannot be read would be worse, but
        # the caller is told the check did not happen (the non-answer rule:
        # distinguish
        # "fine" from "did not ask").
        return None if allow_truncation else {
            "verdict": "context_unknown",
            "error": (
                f"Could not determine the context limit for {model!r} "
                f"({info['source']}), so the {estimate:,}-token estimate could "
                "not be checked against it."
            ),
            "remedy": (
                "Call show_model to inspect it, or pass allow_truncation=true "
                "to send anyway and accept possible silent truncation."
            ),
            "estimated_input_tokens": estimate,
        }

    usable = int(limit * (1 - headroom))
    if estimate <= usable or allow_truncation:
        return None

    over = estimate - usable
    return {
        "verdict": "context_exceeded",
        "error": (
            f"Input is approximately {estimate:,} tokens; {model!r} has a "
            f"{limit:,}-token context, leaving about {usable:,} for input "
            f"after response headroom. Over by roughly {over:,} tokens."
        ),
        "remedy": (
            "Ollama does not error on overflow -- it silently truncates and "
            "returns a normal-looking result. Split the input into chunks "
            f"under ~{int(usable * _CHARS_PER_TOKEN):,} characters, choose a "
            "model with a larger context (call list_models with "
            "include_details=true), or pass allow_truncation=true to accept "
            "truncation deliberately."
        ),
        "estimated_input_tokens": estimate,
        "context_limit": limit,
        "usable_input_tokens": usable,
        "estimate_note": (
            "Token count is estimated from character length, not tokenized -- "
            "Ollama exposes no tokenizer. The estimate is deliberately "
            "pessimistic."
        ),
        **({"context_disagreement": info["disagreement"]}
           if info["disagreement"] else {}),
    }


# --------------------------------------------------------------------------
# Capability routing
# --------------------------------------------------------------------------

# A decision model (capabilities ["decision"], sometimes with "vision") is not a
# text generator, but /api/generate does not refuse one. On 2026-11-07 `nimble`
# through `generate` returned "Thinking Process: 1. **Analyze the Request:**"
# and ran into the token limit: generated prose that reads like an answer,
# without the probabilities that are the reason to use a decision model. That
# is the non-answer rule's shape, so the tool refuses and names the right one.
#
# The check fires ONLY when the capability list was read, is non-empty, lacks
# the capability the tool needs, and names one that another tool serves. An
# unread or empty list proceeds: a capability Ollama did not report is not
# evidence the model lacks it, and refusing on absence would block every model
# on an Ollama too old to report capabilities at all.
_TOOL_FOR_CAPABILITY = {"decision": "decide", "embedding": "embed",
                        "completion": "generate or chat"}


def _capability_refusal(
    cache: "ContextCache", model: str, tool: str, needs: str
) -> dict[str, Any] | None:
    """Refuse a model that reports a different capability. None to proceed."""
    caps = cache.limit_for(model).get("capabilities")
    if not caps or needs in caps:
        return None
    served = [c for c in caps if c in _TOOL_FOR_CAPABILITY and c != needs]
    if not served:
        return None
    other = _TOOL_FOR_CAPABILITY[served[0]]
    return {
        "verdict": "wrong_capability",
        "error": (f"{model!r} reports capabilities {caps}, without {needs!r}, "
                  f"so {tool} cannot use it. Ollama would run it anyway and "
                  "return text that looks like an answer and is not one."),
        "remedy": f"Call {other} with this model, or pick a model whose "
                  f"capabilities include {needs!r} (list_models with "
                  "include_details=true).",
        "model": model,
        "capabilities": caps,
    }


# --------------------------------------------------------------------------
# Decision questions and answers
# --------------------------------------------------------------------------

_QUESTION_TYPES = ("choice", "noul", "score")

# A distribution whose probabilities do not sum to one is not a distribution.
# Ollama rounds to four places, so a little slack is needed and no more.
_PROBABILITY_SLACK = 0.02


def _question_problem(questions: Any) -> str | None:
    """
    The first thing wrong with a questions map, or None.

    Checks shape only -- what makes the request meaningless or the answer
    uncheckable. Upper limits (how many questions, labels or levels) are left
    to Ollama, which reports its own; a limit copied from a release note into
    this file goes stale silently.
    """
    if not isinstance(questions, dict) or not questions:
        return "questions must be a non-empty map of {id: question}."
    for qid, q in questions.items():
        if not isinstance(q, dict):
            return f"questions[{qid!r}] must be an object."
        kind = q.get("type")
        if kind not in _QUESTION_TYPES:
            return (f"questions[{qid!r}].type is {kind!r}; it must be one of "
                    + ", ".join(_QUESTION_TYPES) + ".")
        if not isinstance(q.get("instructions"), str) or \
                not q["instructions"].strip():
            return f"questions[{qid!r}].instructions must be a non-empty string."
        crit = q.get("criteria")
        if kind == "choice" and (not isinstance(crit, dict) or len(crit) < 2):
            return (f"questions[{qid!r}] is a choice and needs criteria: a map "
                    "of at least two labels to what each means.")
        if kind == "score" and (not isinstance(crit, list) or len(crit) < 2):
            return (f"questions[{qid!r}] is a score and needs criteria: an "
                    "ordered list of at least two levels, lowest first.")
    return None


def _answer_problems(questions: dict[str, Any], answers: Any) -> list[str]:
    """
    Everything about the answers that does not match the questions asked.

    Typed output guarantees an interface, not that the interface was honoured.
    A choice outside the offered labels, a probability outside [0, 1], or an
    answer missing for a question asked is a non-answer, and is reported as
    one rather than passed through as a decision.
    """
    if not isinstance(answers, dict):
        return ["the response has no `answers` object"]
    out: list[str] = []
    for qid, q in questions.items():
        a = answers.get(qid)
        if not isinstance(a, dict):
            out.append(f"no answer for {qid!r}")
            continue
        kind = q.get("type")
        if a.get("type") not in (None, kind):
            out.append(f"{qid!r} asked as {kind}, answered as {a.get('type')}")
            continue
        if kind == "choice":
            labels = set(q.get("criteria") or {})
            if a.get("choice") not in labels:
                out.append(f"{qid!r} chose {a.get('choice')!r}, which was not "
                           "one of the labels offered")
            out.extend(_distribution_problems(qid, a.get("probabilities"),
                                              labels))
        elif kind == "noul":
            p = a.get("noul")
            if not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
                out.append(f"{qid!r} noul is {p!r}, not a probability")
        elif kind == "score":
            n = len(q.get("criteria") or [])
            s = a.get("score")
            if not isinstance(s, (int, float)) or not 0.0 <= s <= n - 1:
                out.append(f"{qid!r} score is {s!r}, outside 0..{n - 1}")
            if "probabilities" in a:
                out.extend(_distribution_problems(
                    qid, a.get("probabilities"), {str(i) for i in range(n)}))
    return out


def _distribution_problems(qid: str, probs: Any, keys: set[str]) -> list[str]:
    if not isinstance(probs, dict):
        return [f"{qid!r} has no probabilities"]
    out = []
    if set(map(str, probs)) != keys:
        out.append(f"{qid!r} probabilities cover {sorted(map(str, probs))}, "
                   f"not the options asked {sorted(keys)}")
    vals = list(probs.values())
    if not all(isinstance(v, (int, float)) and 0.0 <= v <= 1.0 for v in vals):
        out.append(f"{qid!r} has a probability outside [0, 1]")
    elif abs(sum(vals) - 1.0) > _PROBABILITY_SLACK:
        out.append(f"{qid!r} probabilities sum to {sum(vals):.3f}, not 1")
    return out


# --------------------------------------------------------------------------
# Ollama's own version
# --------------------------------------------------------------------------

# /v1/systemone arrived in Ollama 0.35.0. On an older server the endpoint does
# not exist and returns 404 -- which `_request` reports as `model_not_found`,
# sending the reader to the wrong problem. So `decide` asks for the version when
# it gets a 404, and server_info reports it, so the requirement is visible
# before a call rather than only after one fails.
DECIDE_MIN_OLLAMA = (0, 35, 0)


def _parse_version(raw: Any) -> tuple[int, int, int] | None:
    """'0.40.15' -> (0, 40, 15). Anything unparseable is None, never a guess."""
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", str(raw or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _ollama_version(config: Config) -> dict[str, Any]:
    """{"version": str|None, "source": str}. Short timeout: it is a probe."""
    try:
        raw = _request(config, "GET", "/api/version", timeout=5)
    except Refused as exc:
        return {"version": None, "source": f"unavailable ({exc.verdict})"}
    v = raw.get("version") if isinstance(raw, dict) else None
    if not v:
        return {"version": None, "source": "unavailable (no version field)"}
    return {"version": str(v), "source": "/api/version"}


def _decide_supported(version: str | None) -> str:
    """"yes", "no", or "unknown" -- three states, because unknown is not no."""
    parsed = _parse_version(version)
    if parsed is None:
        return "unknown"
    return "yes" if parsed >= DECIDE_MIN_OLLAMA else "no"


def _throughput(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Extract generation throughput from an Ollama response.

    `eval_count` is generated tokens and `eval_duration` the nanoseconds spent
    generating them, excluding model load -- so tokens/sec computed from these
    is the steady-state rate. `load_duration` is reported separately because a
    cold load dominates a short generation and would otherwise make a fast
    model look slow.

    This is the per-host performance figure the model inventory records.
    """
    out: dict[str, Any] = {}
    ev, ed = raw.get("eval_count"), raw.get("eval_duration")
    if ev and ed:
        out["eval_count"] = ev
        out["tokens_per_second"] = round(ev / (ed / 1e9), 1)
    if raw.get("prompt_eval_count"):
        out["prompt_eval_count"] = raw["prompt_eval_count"]
    ld = raw.get("load_duration")
    if ld and ld > 1e8:  # >0.1s means the model was actually loaded, not warm
        out["cold_load_seconds"] = round(ld / 1e9, 1)
    return out


def _apply_completion_verdict(
    result: dict[str, Any], raw: dict[str, Any], text: str
) -> None:
    """
    Decide whether a generation actually finished, and say so.

    The non-answer rule requires a check to distinguish *fine* from *did not
    ask*. The first
    version of this logic could not: it tested `done_reason == "length"` for
    truncation and treated everything else as complete. A live call on
    2026-08-26 returned `done_reason: null`, `eval_count: null` and text that
    stopped mid-sentence into runs of whitespace -- and was reported `ok` with
    no flag, because null is not "length".

    Absent metadata is not evidence of completion. Three states, not two:
      done_reason "stop"    -> finished
      done_reason "length"  -> truncated, and we know it
      done_reason missing   -> UNVERIFIED. Do not claim either way.
    """
    if not text.strip():
        result["verdict"] = "empty_generation"
        if result.get("thinking_chars"):
            # The answer is empty but the model wrote reasoning: the budget went
            # on thinking. A different remedy from "the model said nothing".
            result["remedy"] = (
                f"The model thought ({result['thinking_chars']:,} characters) "
                "and returned no answer, usually because thinking used the "
                "whole max_tokens budget. Raise max_tokens, or pass a lower "
                "think level (or think=false) for a task that does not need it."
            )
            return
        result["remedy"] = (
            "The model returned no text. Check max_tokens is not 0, or try a "
            "different model."
        )
        return

    done_reason = raw.get("done_reason")

    if done_reason == "length":
        result["verdict"] = "truncated"
        result["truncated"] = True
        result["remedy"] = (
            "Output hit the token limit and is incomplete. Raise max_tokens."
        )
        return

    if done_reason is None:
        # The server told us nothing about how this ended. A trailing run of
        # whitespace is the usual fingerprint of a generation that stopped
        # without saying why, so it corroborates -- but the verdict does not
        # depend on the heuristic. Absent metadata is enough on its own.
        result["verdict"] = "completion_unverified"
        result["remedy"] = (
            "Ollama returned no `done_reason`, so this response cannot be "
            "confirmed complete. Treat the text as possibly truncated: do not "
            "parse it as whole JSON or store it as a finished result without "
            "checking. Retrying usually returns proper metadata."
        )
        if text != text.rstrip():
            result["trailing_whitespace"] = True
        return

    # done_reason present and not "length" -- "stop", or a future value we do
    # not recognise. Record what it was rather than silently assuming success.
    if done_reason != "stop":
        result["verdict"] = "completion_unverified"
        result["remedy"] = (
            f"Unrecognised done_reason {done_reason!r}. Treat completeness as "
            "unconfirmed rather than assuming this response finished normally."
        )


def _thinking_report(think: Any, thinking: Any) -> dict[str, Any]:
    """
    What a response says about thinking: the level asked for, if any, and how
    much reasoning came back. The reasoning text itself is not returned -- it
    can be many times the answer's size and the caller asked for the answer --
    but its length is, because a thinking model that spends its budget there
    looks, from the answer alone, like a model that said nothing.
    """
    out: dict[str, Any] = {}
    if think is not None:
        out["think"] = think
    if isinstance(thinking, str) and thinking:
        out["thinking_chars"] = len(thinking)
    return out


def _human_size(n: int | None) -> str:
    if not n:
        return "unknown"
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"


# --------------------------------------------------------------------------
# Tool registration
# --------------------------------------------------------------------------


def register(mcp: Any, config: Config) -> None:
    """Probe-registry shape: one register() per module."""
    guard = Guard(config)
    ctx = ContextCache(config)

    # ---------------------------------------------------------------- models

    @mcp.tool()
    def list_models(include_details: bool = False) -> dict[str, Any]:
        """
        List every model installed on the local Ollama server.

        Call this before delegating anything -- model names are host-specific,
        guessing one produces a 404, and the name alone says nothing about
        where the model runs or how much context it has.

        Args:
            include_details: also fetch context_length and capabilities for
                every model. Costs one extra request per model, so it is off by
                default -- but a parameter count is NOT a context window, and
                guessing one is how a delegation silently overflows. Use this
                whenever choosing a model for a job with real input volume.
        """
        try:
            raw = _request(config, "GET", "/api/tags")
        except Refused as exc:
            return _refusal(exc)

        models = []
        for m in raw.get("models", []):
            details = m.get("details") or {}
            name = m.get("name") or m.get("model")
            models.append(
                {
                    "name": name,
                    "location": _location(name, m.get("size")),
                    "size": _human_size(m.get("size")),
                    "size_bytes": m.get("size"),
                    "family": details.get("family"),
                    "parameter_size": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                    "modified": m.get("modified_at"),
                }
            )
        models.sort(key=lambda x: (x["name"] or "").lower())

        if include_details:
            # One /api/show per model. A model whose details cannot be
            # fetched reports WHY, and never silently reports a default or
            # omits the field -- an absent context length that reads as
            # "unknown" is safe; one that reads as 4096 is a wrong answer with
            # good provenance.
            for m in models:
                try:
                    d = _request(config, "POST", "/api/show", {"model": m["name"]})
                except Refused as exc:
                    m["context_length"] = None
                    m["capabilities"] = None
                    m["details_error"] = exc.verdict
                    continue
                info = d.get("model_info") or {}
                m["context_length"] = next(
                    (v for k, v in info.items() if k.endswith(".context_length")),
                    None,
                )
                m["capabilities"] = d.get("capabilities", [])
                if m["context_length"] is None:
                    m["details_error"] = "no_context_length_reported"

        hosted = [m["name"] for m in models if m["location"] != "local"]
        result = _ok(
            count=len(models),
            local_count=len(models) - len(hosted),
            models=models,
            host=config.base_url,
        )
        if include_details:
            failed = [m["name"] for m in models if m.get("details_error")]
            if failed:
                # Report one row per selection and say what was
                # NOT answered, rather than quietly returning fewer facts.
                result["details_unavailable"] = failed
        else:
            result["details_note"] = (
                "context_length and capabilities not fetched. Call with "
                "include_details=true before choosing a model for a job with "
                "real input volume -- parameter size is not context size."
            )

        if hosted:
            # Stated once, plainly, at the point of selection -- not as a
            # warning to be dismissed, but because "where does this run" is a
            # property of the choice and is invisible in the name alone.
            result["hosted_models"] = hosted
            result["note"] = (
                f"{len(hosted)} of {len(models)} models run on Ollama's hosted "
                "infrastructure, not this machine. Content sent to them leaves "
                "the host. They are a deliberate option for workloads that need "
                "a frontier model; choose one on purpose, not by accident. "
                "Every model's `location` field says which it is."
            )
        if config.model_allowlist:
            result["allowlist"] = list(config.model_allowlist)
            result["note"] = (
                "An allowlist is active; only the listed models are callable."
            )
        if not models:
            # Distinguishable from "could not reach Ollama".
            result["verdict"] = "no_models_installed"
            result["remedy"] = "Run `ollama pull <model>` or call pull_model."
        return result

    @mcp.tool()
    def show_model(model: str) -> dict[str, Any]:
        """
        Show a model's capabilities, context length and parameters.

        Use this to choose between installed models -- context length and
        declared capabilities (tools, vision, embedding) decide what a model
        can actually be delegated.

        Args:
            model: model name as returned by list_models.
        """
        try:
            guard.check("show", model)
            raw = _request(config, "POST", "/api/show", {"model": model})
        except Refused as exc:
            return _refusal(exc)

        info = raw.get("model_info") or {}
        context_length = next(
            (v for k, v in info.items() if k.endswith(".context_length")), None
        )
        # Ollama 0.34.3+ reports {"thinking": {"values": [...], "default": ...}}
        # for a model with selectable levels (observed on 0.40.12, gpt-oss:20b:
        # low/medium/high, default medium). Absent means not reported -- not
        # "no levels" -- so the keys are only present when Ollama said so.
        thinking = raw.get("thinking")
        levels: dict[str, Any] = {}
        if isinstance(thinking, dict) and isinstance(thinking.get("values"), list):
            levels["think_levels"] = thinking["values"]
            levels["think_default"] = thinking.get("default")
        return _ok(
            model=model,
            location=_location(model),
            capabilities=raw.get("capabilities", []),
            context_length=context_length,
            details=raw.get("details", {}),
            parameters=raw.get("parameters"),
            template_present=bool(raw.get("template")),
            **levels,
        )

    @mcp.tool()
    def list_running() -> dict[str, Any]:
        """
        List models currently loaded in memory, with their VRAM footprint and
        expiry. Useful for judging whether a call will be fast (already warm)
        or slow (cold load).
        """
        try:
            raw = _request(config, "GET", "/api/ps")
        except Refused as exc:
            return _refusal(exc)

        running = [
            {
                "name": m.get("name") or m.get("model"),
                "size": _human_size(m.get("size")),
                "size_vram": _human_size(m.get("size_vram")),
                "expires_at": m.get("expires_at"),
            }
            for m in raw.get("models", [])
        ]
        if not running:
            return _ok(
                count=0,
                running=[],
                note="No model is loaded. The next call will pay a cold-start load.",
            )
        return _ok(count=len(running), running=running)

    # ------------------------------------------------------------ inference

    @mcp.tool()
    def generate(
        model: str,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        allow_truncation: bool = False,
        think: bool | str | None = None,
    ) -> dict[str, Any]:
        """
        Single-turn completion. The delegation workhorse.

        WHERE THIS RUNS DEPENDS ON THE MODEL. A model whose `location` is
        "local" processes the prompt on this machine. A model tagged `:cloud`
        runs on Ollama's hosted infrastructure and the prompt LEAVES THIS
        MACHINE. Both are permitted; call list_models and read `location`
        before sending anything you would not send off the host.

        Check the `verdict` on the way back: "ok" means complete, "truncated"
        means it hit the token limit, and "completion_unverified" means Ollama
        did not say how the generation ended -- do not parse that as whole JSON.

        Args:
            model: model name from list_models.
            prompt: the instruction or content to process.
            system: optional system prompt setting role and constraints.
            temperature: 0.0 for deterministic extraction, higher for prose.
            max_tokens: cap on generated tokens (num_predict). On a thinking
                model the thinking counts against it.
            json_mode: force syntactically valid JSON output.
            think: thinking level for a model that supports it -- true/false,
                or a level the model names (show_model -> think_levels). Unset
                leaves the model's default. A bulk classification rarely needs
                thinking; switching it off is cheaper.

        A model whose capabilities are `decision` is refused here: use decide.
        """
        try:
            guard.check("generate", model)
        except Refused as exc:
            return _refusal(exc)

        if not prompt or not prompt.strip():
            return {
                "verdict": "invalid_request",
                "error": "prompt is empty; nothing to generate from.",
            }

        refusal = (_capability_refusal(ctx, model, "generate", "completion")
                   or _check_context(ctx, model, prompt + system, allow_truncation))
        if refusal:
            return refusal

        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if system.strip():
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"
        if think is not None:
            payload["think"] = think

        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if options:
            payload["options"] = options

        try:
            raw = _request(config, "POST", "/api/generate", payload)
        except Refused as exc:
            return _refusal(exc)

        text = raw.get("response", "")
        result = _ok(
            model=model,
            location=_location(model),
            response=text,
            duration_s=round((raw.get("total_duration") or 0) / 1e9, 2),
            done_reason=raw.get("done_reason"),
            **_throughput(raw),
            **_thinking_report(think, raw.get("thinking")),
        )
        _apply_completion_verdict(result, raw, text)
        return result

    @mcp.tool()
    def chat(
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        allow_truncation: bool = False,
        think: bool | str | None = None,
    ) -> dict[str, Any]:
        """
        Multi-turn conversation, preserving history.

        Use this over generate when the model needs prior turns for context --
        iterative refinement, follow-up questions on the same document.

        WHERE THIS RUNS DEPENDS ON THE MODEL, and the whole history is sent on
        every call. With a `:cloud` model the entire conversation leaves this
        machine each turn, not just the latest message. Check `location`.

        Args:
            model: model name from list_models.
            messages: list of {"role": "system"|"user"|"assistant",
                     "content": "..."} in order.
            temperature: sampling temperature.
            max_tokens: cap on generated tokens, thinking included.
            json_mode: force syntactically valid JSON output.
            think: thinking level, as for generate. Unset keeps the default.

        A model whose capabilities are `decision` is refused here: use decide.
        """
        try:
            guard.check("chat", model)
        except Refused as exc:
            return _refusal(exc)

        if not messages:
            return {
                "verdict": "invalid_request",
                "error": "messages is empty; nothing to respond to.",
            }

        valid_roles = {"system", "user", "assistant", "tool"}
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                return {
                    "verdict": "invalid_request",
                    "error": f"messages[{i}] must have 'role' and 'content' keys.",
                }
            if msg["role"] not in valid_roles:
                return {
                    "verdict": "invalid_request",
                    "error": f"messages[{i}] role {msg['role']!r} is not one of "
                    + ", ".join(sorted(valid_roles)),
                }

        # The WHOLE history is re-sent every turn, so the budget is the sum of
        # all messages, not the newest one. A conversation that fits on turn 3
        # can overflow on turn 9 with no change in behaviour from the caller.
        refusal = _capability_refusal(ctx, model, "chat", "completion") or \
            _check_context(
                ctx,
                model,
                "".join(str(m.get("content", "")) for m in messages),
                allow_truncation,
            )
        if refusal:
            return refusal

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"
        if think is not None:
            payload["think"] = think

        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if options:
            payload["options"] = options

        try:
            raw = _request(config, "POST", "/api/chat", payload)
        except Refused as exc:
            return _refusal(exc)

        message = raw.get("message") or {}
        content = message.get("content", "")
        result = _ok(
            model=model,
            location=_location(model),
            role=message.get("role", "assistant"),
            content=content,
            duration_s=round((raw.get("total_duration") or 0) / 1e9, 2),
            done_reason=raw.get("done_reason"),
            **_throughput(raw),
            **_thinking_report(think, message.get("thinking")),
        )
        # Same completion logic as generate. chat previously checked only for
        # empty content, so it had the identical blind spot.
        _apply_completion_verdict(result, raw, content)
        return result

    @mcp.tool()
    def embed(
        model: str, texts: list[str], allow_truncation: bool = False
    ) -> dict[str, Any]:
        """
        Generate embedding vectors.

        With a model whose `location` is "local" -- nomic-embed-text is local
        on this host -- the content never leaves the machine, which makes this
        the safe path for indexing private material. A `:cloud` embedding model
        would send it off the host instead. Check `location` in the response.

        Needs an embedding model (nomic-embed-text, mxbai-embed-large). A chat
        model will refuse or return nonsense -- check show_model capabilities.

        Args:
            model: an embedding-capable model name.
            texts: one or more strings to embed.
        """
        try:
            guard.check("embed", model)
        except Refused as exc:
            return _refusal(exc)

        if not texts:
            return {
                "verdict": "invalid_request",
                "error": "texts is empty; nothing to embed.",
            }

        # THE most consequential check in this server. Embedding models have
        # small windows -- nomic-embed-text is 2,048 tokens, ~6.5KB of text --
        # and an over-length input is truncated, not refused. The result is a
        # structurally perfect vector representing the first fragment of a
        # document, which then sits in an index being trusted for months.
        #
        # No output headroom: embeddings generate no tokens, so the whole
        # window is available for input.
        info = ctx.limit_for(model)
        limit = info["limit"]
        if limit and not allow_truncation:
            oversized = [
                {
                    "index": i,
                    "estimated_tokens": _estimate_tokens(t),
                    "chars": len(t),
                }
                for i, t in enumerate(texts)
                if _estimate_tokens(t) > limit
            ]
            if oversized:
                # Name every input that fails, not just the count.
                return {
                    "verdict": "context_exceeded",
                    "error": (
                        f"{len(oversized)} of {len(texts)} inputs exceed the "
                        f"{limit:,}-token context of {model!r}."
                    ),
                    "oversized_inputs": oversized,
                    "context_limit": limit,
                    "remedy": (
                        "Ollama truncates over-length embedding input silently "
                        "and returns a valid-looking vector for the first "
                        "fragment only -- an index built from these would be "
                        "quietly wrong. Chunk each input below roughly "
                        f"{int(limit * _CHARS_PER_TOKEN):,} characters and "
                        "embed the chunks separately, or pass "
                        "allow_truncation=true to accept the loss."
                    ),
                    "estimate_note": (
                        "Estimated from character length; Ollama exposes no "
                        "tokenizer. Deliberately pessimistic."
                    ),
                    **({"context_disagreement": info["disagreement"]}
                       if info["disagreement"] else {}),
                }

        try:
            raw = _request(config, "POST", "/api/embed", {"model": model, "input": texts})
            vectors = raw.get("embeddings", [])
        except Refused as exc:
            if exc.verdict != "model_not_found":
                return _refusal(exc)
            # Older Ollama builds only have /api/embeddings, one string at a
            # time. Fall back rather than reporting a version skew as a
            # missing model -- that would send the reader to the wrong problem.
            try:
                vectors = [
                    _request(
                        config, "POST", "/api/embeddings", {"model": model, "prompt": t}
                    ).get("embedding", [])
                    for t in texts
                ]
            except Refused as inner:
                return _refusal(inner)

        # Assert one vector per input rather than trusting the count.
        if len(vectors) != len(texts):
            return {
                "verdict": "incomplete",
                "error": f"Asked for {len(texts)} embeddings, got {len(vectors)}.",
                "remedy": "Do not use a partial result for indexing.",
            }
        if any(not v for v in vectors):
            return {
                "verdict": "incomplete",
                "error": "At least one embedding came back empty.",
                "remedy": f"Check that {model!r} is an embedding model "
                "(show_model -> capabilities).",
            }

        return _ok(
            model=model,
            location=_location(model),
            count=len(vectors),
            dimensions=len(vectors[0]),
            embeddings=vectors,
        )

    @mcp.tool()
    def decide(
        model: str,
        state: str,
        questions: dict[str, dict[str, Any]],
        allow_truncation: bool = False,
    ) -> dict[str, Any]:
        """
        Ask a local DECISION model typed questions about some content, and get
        probabilities back instead of generated text. For labelling, yes/no
        checks and grading in bulk, where a label parsed out of prose would
        otherwise have to be trusted.

        Needs a model whose capabilities include `decision` (list_models with
        include_details=true) and Ollama 0.35.0 or newer (server_info).

        Args:
            model: a decision model name from list_models.
            state: the content the questions are about. Plain text, or JSON
                serialised to text when it has several named parts.
            questions: {id: question}. Each question is one of
                {"type": "choice", "instructions": "...",
                 "criteria": {"label": "what the label means", ...}}
                    -> `choice`, a probability per label, `confidence`
                {"type": "noul", "instructions": "Does ...?"}
                    -> `noul`, the probability the answer is yes
                {"type": "score", "instructions": "...",
                 "criteria": ["lowest level", ..., "highest level"]}
                    -> `score`, a probability-weighted position on the levels
                Every question sees the same state and none sees another's
                answer. Ids are for your code; put the whole meaning in the
                instructions. Include a "none of these" label where nothing
                may fit.

        `confidence` is how concentrated the model's distribution is. It is NOT
        the probability that the answer is right: set any threshold on your own
        labelled examples, and send items under it back for a closer look.
        """
        try:
            guard.check("decide", model)
        except Refused as exc:
            return _refusal(exc)

        if not state or not str(state).strip():
            return {"verdict": "invalid_request",
                    "error": "state is empty; there is nothing to decide about."}
        problem = _question_problem(questions)
        if problem:
            return {"verdict": "invalid_request", "error": problem,
                    "remedy": "See decide's description for the three question "
                              "shapes."}

        refusal = (_capability_refusal(ctx, model, "decide", "decision")
                   or _check_context(ctx, model,
                                     str(state) + json.dumps(questions),
                                     allow_truncation, headroom=0.05))
        if refusal:
            return refusal

        payload = {"model": model, "state": str(state), "questions": questions}
        started = time.monotonic()
        try:
            raw = _request(config, "POST", "/v1/systemone", payload)
        except Refused as exc:
            if exc.verdict != "model_not_found":
                return _refusal(exc)
            # A 404 here means either no such model or no such ENDPOINT, and
            # the two have different remedies. Ask Ollama which.
            ollama = _ollama_version(config)
            supported = _decide_supported(ollama["version"])
            known = ctx.limit_for(model).get("capabilities") is not None
            if supported == "no" or known:
                return {
                    "verdict": "endpoint_unavailable",
                    "error": (f"Ollama at {config.base_url} returned 404 for "
                              f"/v1/systemone (Ollama "
                              f"{ollama['version'] or ollama['source']})."),
                    "remedy": ("Decision models need Ollama "
                               f"{'.'.join(map(str, DECIDE_MIN_OLLAMA))} or "
                               "newer. Upgrade Ollama, then retry."),
                }
            return _refusal(exc)
        elapsed = round(time.monotonic() - started, 2)

        answers = raw.get("answers") if isinstance(raw, dict) else None
        issues = _answer_problems(questions, answers)
        result = _ok(
            model=model,
            location=_location(model),
            answers=answers if isinstance(answers, dict) else {},
            usage=raw.get("usage") if isinstance(raw, dict) else None,
            duration_s=elapsed,
        )
        if issues:
            # Only missing answers: incomplete. Anything answered wrongly:
            # bad_response, because a wrong answer is worse than none.
            if all(i.startswith("no answer") for i in issues):
                result["verdict"] = "incomplete"
            else:
                result["verdict"] = "bad_response"
            result["problems"] = issues
            result["remedy"] = ("Do not use these answers as decided. The "
                                "response did not match the questions asked; "
                                "the problems above say where.")
        return result

    # ----------------------------------------------------- model management

    @mcp.tool()
    def pull_model(model: str) -> dict[str, Any]:
        """
        Download a model to the local Ollama server.

        This writes to the host and can take a long time and many gigabytes.
        Prefer naming an explicit tag (llama3.1:8b) over a bare name, which
        resolves to :latest and may be far larger than expected.

        Disabled unless OLLAMA_MCP_ALLOW_PULL=1 is set in the server
        environment.

        Args:
            model: model name to pull, ideally with an explicit tag.
        """
        try:
            guard.check("pull", model)
            raw = _request(
                config,
                "POST",
                "/api/pull",
                {"model": model, "stream": False},
                timeout=config.pull_timeout,
            )
        except Refused as exc:
            return _refusal(exc)

        status = raw.get("status", "")
        if status != "success":
            return {
                "verdict": "incomplete",
                "error": f"Pull of {model!r} ended with status {status!r}.",
                "remedy": "Check `ollama list` on the host.",
            }
        return _ok(model=model, status=status, note="Model is now available locally.")

    @mcp.tool()
    def delete_model(model: str) -> dict[str, Any]:
        """
        Delete a model from the local Ollama server. Destructive and
        irreversible -- the model must be re-downloaded to be used again.

        Disabled unless OLLAMA_MCP_ALLOW_DELETE=1 is set in the server
        environment.

        Args:
            model: model name to delete.
        """
        try:
            guard.check("delete", model)
            _request(config, "DELETE", "/api/delete", {"model": model})
        except Refused as exc:
            return _refusal(exc)
        return _ok(model=model, deleted=True)

    @mcp.tool()
    def server_info() -> dict[str, Any]:
        """
        Report what this MCP server is configured to do and what it refuses.

        Self-explaining, so a refused call can be understood without reading
        the source. Also reports the Ollama server's own version, which
        decides whether decide can work at all.
        """
        ollama = _ollama_version(config)
        return _ok(
            version=__version__,
            host=config.base_url,
            # Ollama's version, not this server's. Asked live, with a short
            # timeout; unreachable is reported as itself, never as a version.
            ollama_version=ollama["version"] or ollama["source"],
            decide_supported=(
                f"{_decide_supported(ollama['version'])} -- decide needs Ollama "
                f">= {'.'.join(map(str, DECIDE_MIN_OLLAMA))} and a model whose "
                "capabilities include `decision`"),
            write_operations={
                "pull_model": "enabled" if config.allow_pull else "disabled",
                "delete_model": "enabled" if config.allow_delete else "disabled",
            },
            # Reporting "disabled" without naming the flag left the
            # reader to guess, and a second client guessed ALLOW_MODEL_PULL and a
            # per-call `confirm` argument. A state with no remedy is the same
            # defect UR-06 names for failures, in the configuration report.
            write_gates={
                tool: _gate_remedy(flag) for tool, flag in WRITE_GATES.values()
            },
            model_allowlist=list(config.model_allowlist) or "none (any local model)",
            timeout_s=config.timeout,
            pull_timeout_s=config.pull_timeout,
            # UR-03 says every variable's effect is visible here. Without this
            # line, OLLAMA_MCP_LOGLEVEL was the one exception -- readable from
            # the environment and invisible in the report.
            log_level=logging.getLevelName(log.getEffectiveLevel()),
            # A005, 2026-08-29: a diligent client reads server_info and never
            # makes the refused call, which makes this the load-bearing surface
            # and the verdict strings the backstop. So the index tools have to
            # be described HERE, including when they are absent -- an agent that
            # cannot see them has no way to tell "not configured" from "not
            # supported by this server", and those have different remedies.
            retrieval_tools=_retrieval_tools_note(config),
            note=(
                "Content sent to a model whose `location` is \"local\" stays on "
                "this host. Models tagged `:cloud` run on Ollama's hosted "
                "infrastructure and content sent to them does not. Both are "
                "permitted; every listing and every inference response carries "
                "a `location` field so the choice is explicit."
            ),
        )


# --------------------------------------------------------------------------
# Selftest -- every assertion below can actually fail. An assertion that
# cannot fail is the non-answer rule in test form: a check reporting fine.
# --------------------------------------------------------------------------


def selftest() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        if condition:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}")
            failures.append(label)

    print("URL normalisation")
    check(
        "bare host:port gains an http:// scheme",
        _normalise_base_url("localhost:11434") == "http://localhost:11434",
    )
    check(
        "trailing slash is stripped",
        _normalise_base_url("http://localhost:11434/") == "http://localhost:11434",
    )
    # NOTE: the obvious case here is "file:///etc/passwd", and it is a trap.
    # That URL has no hostname, so it is refused by the *hostname* check even
    # with the scheme check deleted -- a test that passes for the wrong reason
    # and cannot fail. Mutation testing caught it. Every scheme case below
    # therefore carries a hostname, so only the scheme check can refuse it.
    for bad in ("ftp://localhost:11434", "file://localhost/etc/passwd"):
        try:
            _normalise_base_url(bad)
            check(f"{bad.split('://')[0]}:// scheme is refused", False)
        except ValueError as exc:
            check(
                f"{bad.split('://')[0]}:// scheme is refused",
                "scheme" in str(exc),
            )
    try:
        _normalise_base_url("")
        check("empty host is refused", False)
    except ValueError:
        check("empty host is refused", True)
    try:
        _normalise_base_url("http://")
        check("scheme with no host is refused", False)
    except ValueError as exc:
        check("scheme with no host is refused", "no host" in str(exc))

    print("Tool descriptions answer the questions agents ask of them")
    # An agent reasoning from the schemas alone once predicted the OPPOSITE
    # of the truth -- that pull_model was ungated and a multi-gigabyte download
    # was about to start -- because only delete_model documented its flag. A
    # description that understates enforcement is read as a promise, so these
    # assertions treat the docstrings as a shipped surface, not as comments.
    registered: dict[str, Any] = {}

    class _CollectMCP:
        def tool(self):
            def decorate(fn):
                registered[fn.__name__] = fn
                return fn
            return decorate

    # A closed port, not the default host: server_info now asks Ollama for its
    # version, and the selftest must not depend on -- or reach -- a live host.
    register(_CollectMCP(), Config(base_url="http://127.0.0.1:1", timeout=2.0))
    # Adding a gated tool means adding it here. The pairing is explicit so that
    # a new gate with no documentation fails this suite rather than shipping.
    for tool_name, flag in (("pull_model", "OLLAMA_MCP_ALLOW_PULL"),
                            ("delete_model", "OLLAMA_MCP_ALLOW_DELETE")):
        doc = registered[tool_name].__doc__ or ""
        check(f"{tool_name}'s description names its own gate", flag in doc)

    # The server cannot know which client is connected, so naming one in text a
    # caller reads is wrong for every other client. Observed live on ENV-3: a
    # Gemini-based client displayed "restart Claude Desktop" as the way out of a
    # refusal.
    clients = ("Claude Desktop", "Claude Code", "Cowork", "Antigravity")
    named_in_docs = sorted(
        f"{name}: {client}"
        for name, fn in registered.items()
        for client in clients
        if client in (fn.__doc__ or "")
    )
    check("no tool description names a specific client", not named_in_docs)

    for op, flag in (("pull", "OLLAMA_MCP_ALLOW_PULL"),
                     ("delete", "OLLAMA_MCP_ALLOW_DELETE")):
        try:
            Guard(Config()).check(op, "llama3")
            check(f"{op} refusal carries a client-agnostic remedy", False)
        except Refused as exc:
            check(f"{op} refusal carries a client-agnostic remedy",
                  "MCP client" in exc.remedy
                  and not any(c in exc.remedy for c in clients))

    # server_info is the surface agents read instead of calling, and it once
    # reported a state with no remedy. These assertions tie the report to the
    # refusal so the two cannot drift, and so a new gate cannot ship without one.
    info = registered["server_info"]()
    # .get, not [] -- a missing key must FAIL these assertions, not crash the
    # suite. A mutant caught by a traceback is caught for the wrong reason, and
    # the next one that changes wording rather than shape would slip past.
    gates = info.get("write_gates", {})
    check("every gated operation in write_operations has a gate remedy",
          set(gates) == set(info["write_operations"]))
    for op, (tool, flag) in WRITE_GATES.items():
        check(f"{tool}'s gate remedy names its own flag", flag in gates[tool])
        try:
            Guard(Config()).check(op, "llama3")
            check(f"{tool}'s reported remedy is the refusal's own wording", False)
        except Refused as exc:
            # Not "both mention the flag" -- IDENTICAL. Two nearly-equal strings
            # is how a remedy goes stale in one place only.
            check(f"{tool}'s reported remedy is the refusal's own wording",
                  gates[tool] == exc.remedy)
    check("no gate remedy names a specific client",
          not any(c in text for text in gates.values() for c in clients))
    check("server_info reports the log level, so every variable's effect shows",
          isinstance(info.get("log_level"), str) and info["log_level"] != "")
    check("server_info reports the release version",
          info.get("version") == __version__
          and re.fullmatch(r"\d+\.\d+\.\d+", str(info.get("version"))) is not None)
    check("server_info reports an unreachable Ollama as unavailable, not a version",
          str(info.get("ollama_version", "")).startswith("unavailable")
          and str(info.get("decide_supported", "")).startswith("unknown"))

    print("Configuration is discoverable without reading source")
    # UR-03: the variables must be findable in the documented configuration.
    # Since 1.0.0 that is docs/MANUAL.md; the README is the short front door.
    # OLLAMA_MCP_LOGLEVEL was read by this file and named nowhere in it -- the
    # same class the DS generator caught in August, found again by asking the
    # question mechanically instead of by eye.
    _here = Path(__file__).resolve().parent
    _sources = [_here / name for name in
                ("ollama_server.py", "index_tools.py", "vault_index.py")]
    # Built in two pieces so this pattern does not match itself in this file.
    _var = re.compile("OLLAMA" + r"(?:_MCP)?_[A-Z][A-Z_]*[A-Z]")
    read_names = sorted({
        name
        for src in _sources if src.exists()
        for name in _var.findall(src.read_text(encoding="utf-8"))
    })
    check("the environment scan finds the variables at all", len(read_names) >= 6)
    _readme = _here / "docs" / "MANUAL.md"
    if not _readme.exists():
        # An installed copy without the repository cannot be checked. Say so
        # rather than passing quietly: a skipped check that prints nothing is
        # indistinguishable from one that held.
        print("  ! docs/MANUAL.md is not beside this file, so the documentation "
              "check did NOT run")
    else:
        _text = _readme.read_text(encoding="utf-8")
        undocumented = [name for name in read_names if name not in _text]
        check("every environment variable the code reads is named in the manual",
              not undocumented)
        if undocumented:
            print(f"       undocumented: {', '.join(undocumented)}")

    print("Delete gating")
    locked = Guard(Config(allow_delete=False))
    try:
        locked.check("delete", "llama3")
        check("delete refused when flag is off", False)
    except Refused as exc:
        check("delete refused when flag is off", exc.verdict == "not_permitted")
        check("refusal names the remedy", "OLLAMA_MCP_ALLOW_DELETE" in exc.remedy)

    opened = Guard(Config(allow_delete=True))
    try:
        opened.check("delete", "llama3")
        check("delete permitted when flag is on", True)
    except Refused:
        # A gate that never opens is indistinguishable from a broken gate.
        check("delete permitted when flag is on", False)

    print("Pull gating")
    # Both write operations are refused on a DEFAULT config. Asserting against
    # Config() rather than Config(allow_pull=False) is the point: the earlier
    # suite only ever tested an explicitly-disabled flag, so it would have
    # passed unchanged whichever way the default pointed.
    try:
        Guard(Config()).check("pull", "llama3")
        check("pull refused on DEFAULT config", False)
    except Refused as exc:
        check("pull refused on DEFAULT config", exc.verdict == "not_permitted")
        # The remedy is asserted, not just its presence. A remedy that names the
        # wrong action is worse than none -- it survives a default change while
        # silently becoming false, which is exactly what happened here: the text
        # said "unset OLLAMA_MCP_ALLOW_PULL to re-enable", which stopped being
        # true the moment the default flipped to refused.
        check("pull remedy names the variable",
              "OLLAMA_MCP_ALLOW_PULL" in exc.remedy)
        check("pull remedy says SET, not unset",
              "OLLAMA_MCP_ALLOW_PULL=1" in exc.remedy
              and "Unset" not in exc.remedy)

    try:
        Guard(Config(allow_pull=True)).check("pull", "llama3")
        check("pull permitted when flag is on", True)
    except Refused:
        # A gate that never opens is indistinguishable from a broken gate.
        check("pull permitted when flag is on", False)

    try:
        Guard(Config()).check("delete", "llama3")
        check("delete refused on DEFAULT config", False)
    except Refused as exc:
        check("delete refused on DEFAULT config", exc.verdict == "not_permitted")

    print("Model allowlist")
    listed = Guard(Config(model_allowlist=("llama3.1:8b", "qwen2.5")))
    try:
        listed.check("generate", "mistral")
        check("model outside allowlist is refused", False)
    except Refused as exc:
        check("model outside allowlist is refused", exc.verdict == "not_permitted")
        check("refusal lists what IS allowed", "llama3.1:8b" in exc.remedy)
    try:
        listed.check("generate", "qwen2.5:latest")
        check(":latest matches its untagged allowlist entry", True)
    except Refused:
        check(":latest matches its untagged allowlist entry", False)
    try:
        listed.check("generate", "llama3.1:8b")
        check("exact allowlist match is permitted", True)
    except Refused:
        check("exact allowlist match is permitted", False)

    print("Empty input")
    # Named for what it is: a default config, which refuses BOTH write
    # operations. It is unrestricted only as to model names,
    # which is what this block exercises.
    defaults = Guard(Config())
    try:
        defaults.check("generate", "   ")
        check("blank model name is refused", False)
    except Refused as exc:
        check("blank model name is refused", exc.verdict == "invalid_request")

    print("Context budgeting")

    class _StubCache:
        def __init__(self, limit, disagreement=None, source="model_info/num_ctx"):
            self._e = {"limit": limit, "source": source,
                       "disagreement": disagreement}

        def limit_for(self, model):
            return self._e

    # Real context limits, read off a live installation.
    nomic = _StubCache(2048)
    gemma = _StubCache(262144)

    small = "x" * 1000                 # ~313 tokens
    huge = "x" * 1_000_000             # ~312,500 tokens -- the 1MB case

    check(
        "1MB input is refused against a 2,048-token model",
        (_check_context(nomic, "nomic-embed-text", huge, False) or {}).get("verdict")
        == "context_exceeded",
    )
    check(
        "1MB input is refused even against a 262,144-token model",
        (_check_context(gemma, "gemma4:12b", huge, False) or {}).get("verdict")
        == "context_exceeded",
    )
    check(
        "a small input passes the 2,048-token model",
        _check_context(nomic, "nomic-embed-text", small, False) is None,
    )
    check(
        "refusal reports the estimate and the limit",
        all(
            k in (_check_context(nomic, "n", huge, False) or {})
            for k in ("estimated_input_tokens", "context_limit",
                      "usable_input_tokens")
        ),
    )
    check(
        "allow_truncation=True lets an oversized call proceed",
        _check_context(nomic, "n", huge, True) is None,
    )
    check(
        "headroom is reserved, so a just-under-limit input still refuses",
        (_check_context(_StubCache(1000), "n", "x" * 3100, False) or {}).get(
            "verdict"
        )
        == "context_exceeded",
    )
    # Unknown must not be treated as unlimited.
    unknown = _StubCache(None, source="unavailable (ollama_unreachable)")
    check(
        "an unknown limit is reported, not assumed generous",
        (_check_context(unknown, "n", small, False) or {}).get("verdict")
        == "context_unknown",
    )
    check(
        "a context disagreement is surfaced in the refusal",
        "context_disagreement"
        in (_check_context(_StubCache(2048, "2048 vs 8192"), "n", huge, False) or {}),
    )
    check(
        "the token estimate errs pessimistic (more tokens than chars/4)",
        _estimate_tokens("x" * 4000) > 1000,
    )

    # ContextCache.limit_for was untested until mutation testing showed the
    # stub above bypassed it entirely -- the num_ctx parsing and disagreement
    # detection, which is what found nomic-embed-text's 2048-vs-8192 conflict
    # on a real host, had no coverage at all. Third instance of the same trap
    # in this project. Stub the TRANSPORT, not the cache.
    _saved_request = _request

    def _with_stubbed_show(show_response):
        def fake(config, method, path, payload=None, timeout=None):
            if path == "/api/show":
                return show_response
            raise AssertionError(f"unexpected path {path}")
        globals()["_request"] = fake
        return ContextCache(Config())

    try:
        # The real nomic-embed-text response shape, off a live installation.
        c = _with_stubbed_show(
            {
                "model_info": {"nomic-bert.context_length": 2048},
                "parameters": "num_ctx                        8192",
            }
        )
        e = c.limit_for("nomic-embed-text:latest")
        check("cache reads context_length from model_info", e["limit"] == 2048)
        check(
            "cache picks the LOWER of context_length and num_ctx",
            e["limit"] == 2048,
        )
        check("cache reports the 2048-vs-8192 disagreement",
              e["disagreement"] is not None and "8192" in e["disagreement"])

        # Agreement -> no disagreement reported.
        c2 = _with_stubbed_show(
            {
                "model_info": {"gemma4.context_length": 262144},
                "parameters": "num_ctx                        262144",
            }
        )
        e2 = c2.limit_for("gemma4:12b")
        check("no false disagreement when the two agree",
              e2["limit"] == 262144 and e2["disagreement"] is None)

        # No parameters block at all -- must still read model_info.
        c3 = _with_stubbed_show({"model_info": {"gptoss.context_length": 131072}})
        check("cache works with no num_ctx parameter",
              c3.limit_for("gpt-oss:20b")["limit"] == 131072)

        # Nothing usable -> None, never a default.
        c4 = _with_stubbed_show({"model_info": {}, "parameters": ""})
        check("cache returns None, not a default, when nothing is reported",
              c4.limit_for("mystery")["limit"] is None)

        # Cached: a second call must not re-request.
        c5 = _with_stubbed_show({"model_info": {"x.context_length": 4096}})
        c5.limit_for("m")
        globals()["_request"] = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("re-requested a cached model")
        )
        try:
            check("second lookup is served from cache",
                  c5.limit_for("m")["limit"] == 4096)
        except AssertionError:
            check("second lookup is served from cache", False)
        # embed's per-input check lives inline in the tool, so nothing above
        # reaches it -- mutation testing caught that too. Exercise the REAL
        # registered tool against a stubbed transport.
        def _embed_tool(show_response):
            reg: dict[str, Any] = {}

            class _Fake:
                def tool(self):
                    def d(f):
                        reg[f.__name__] = f
                        return f
                    return d

            def fake(config, method, path, payload=None, timeout=None):
                if path == "/api/show":
                    return show_response
                if path == "/api/embed":
                    return {"embeddings": [[0.1] * 768 for _ in payload["input"]]}
                raise AssertionError(f"unexpected path {path}")

            globals()["_request"] = fake
            register(_Fake(), Config())
            return reg["embed"]

        nomic_show = {
            "model_info": {"nomic-bert.context_length": 2048},
            "parameters": "num_ctx                        8192",
        }
        long_doc = "word " * 3000          # ~4,700 tokens, over 2,048

        r = _embed_tool(nomic_show)("nomic-embed-text", [long_doc])
        check("embed REFUSES an over-length input",
              r.get("verdict") == "context_exceeded")
        check("embed names which inputs were oversized",
              [o["index"] for o in r.get("oversized_inputs", [])] == [0])

        r2 = _embed_tool(nomic_show)("nomic-embed-text", ["short", long_doc, "ok"])
        check("embed reports the oversized input among valid ones",
              [o["index"] for o in r2.get("oversized_inputs", [])] == [1])

        r3 = _embed_tool(nomic_show)("nomic-embed-text", ["short text"])
        check("embed proceeds when every input fits",
              r3.get("verdict") == "ok" and r3.get("count") == 1)

        r4 = _embed_tool(nomic_show)(
            "nomic-embed-text", [long_doc], allow_truncation=True
        )
        check("embed allows truncation when asked explicitly",
              r4.get("verdict") == "ok")
    finally:
        globals()["_request"] = _saved_request

    print("Completion verdicts")
    # Regression: a live call on 2026-08-26 returned done_reason=None with
    # text that stopped mid-sentence, and was reported "ok". Absent metadata
    # must never be read as evidence of completion.
    def _verdict(raw: dict[str, Any], text: str) -> dict[str, Any]:
        r = {"verdict": "ok"}
        _apply_completion_verdict(r, raw, text)
        return r

    check(
        "done_reason 'stop' stays ok",
        _verdict({"done_reason": "stop"}, "A complete answer.")["verdict"] == "ok",
    )
    check(
        "done_reason 'length' is truncated",
        _verdict({"done_reason": "length"}, "Cut off here")["verdict"] == "truncated",
    )
    check(
        "MISSING done_reason is NOT reported ok",
        _verdict({}, "Stopped mid-sen")["verdict"] == "completion_unverified",
    )
    check(
        "explicit null done_reason is NOT reported ok",
        _verdict({"done_reason": None}, "Stopped mid-sen")["verdict"]
        == "completion_unverified",
    )
    check(
        "trailing whitespace is flagged when metadata is absent",
        _verdict({}, "Stopped   \n   \n   ").get("trailing_whitespace") is True,
    )
    check(
        "an unrecognised done_reason is not assumed successful",
        _verdict({"done_reason": "some_future_value"}, "text")["verdict"]
        == "completion_unverified",
    )
    check(
        "empty text beats every other verdict",
        _verdict({"done_reason": "stop"}, "   ")["verdict"] == "empty_generation",
    )

    print("Decision models, and capability routing")
    # 2026-11-07: `nimble` through generate returned generated prose and no
    # probabilities, reported by v1.0.0 as an ordinary (truncated) generation.
    # Every check below runs the REAL registered tool against a stubbed
    # transport, and records which paths were requested, so "refused before
    # the call" is observed rather than inferred from the verdict.
    _saved_request_d = _request
    _calls: list[str] = []

    def _decision_tools(show: dict[str, Any] | None, routes: dict[str, Any]):
        reg: dict[str, Any] = {}

        class _Fake:
            def tool(self):
                def d(f):
                    reg[f.__name__] = f
                    return f
                return d

        def fake(config, method, path, payload=None, timeout=None):
            _calls.append(path)
            if path == "/api/show":
                if show is None:
                    raise Refused("model_not_found", "404 /api/show", "")
                return show
            route = routes.get(path)
            if isinstance(route, Refused):
                raise route
            if callable(route):
                return route(payload)
            if route is None:
                raise AssertionError(f"unexpected path {path}")
            return route

        globals()["_request"] = fake
        _calls.clear()
        register(_Fake(), Config())
        return reg

    _show_decision = {"capabilities": ["decision", "vision"],
                      "model_info": {"qwen35.context_length": 262144}}
    _show_text = {"capabilities": ["completion", "tools", "thinking"],
                  "model_info": {"gemma4.context_length": 262144}}
    _gen_ok = {"response": "negative", "done_reason": "stop"}
    _q = {
        "label": {"type": "choice", "instructions": "Which category?",
                  "criteria": {"fault": "Equipment failed",
                               "routine": "Nothing unusual"}},
        "followup": {"type": "noul", "instructions": "Does it need follow-up?"},
        "urgency": {"type": "score", "instructions": "How urgent?",
                    "criteria": ["none", "today", "within the hour"]},
    }
    _good = {"model": "nimble", "answers": {
        "label": {"type": "choice", "choice": "fault",
                  "probabilities": {"fault": 0.93, "routine": 0.07},
                  "confidence": 0.86},
        "followup": {"type": "noul", "noul": 0.91},
        "urgency": {"type": "score", "score": 1.4, "confidence": 0.5,
                    "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}}},
        "usage": {"input_tokens": 170, "output_tokens": 1}}

    try:
        t = _decision_tools(_show_decision, {"/api/generate": _gen_ok})
        r = t["generate"](model="nimble", prompt="Classify this.")
        check("generate refuses a decision-only model",
              r.get("verdict") == "wrong_capability")
        check("the refusal names decide as the way out",
              "decide" in r.get("remedy", ""))
        check("the refusal is made before any generation is requested",
              "/api/generate" not in _calls)

        t = _decision_tools(_show_decision, {"/api/chat": {}})
        r = t["chat"](model="clef-flash",
                      messages=[{"role": "user", "content": "Classify this."}])
        check("chat refuses a decision-only model, before the call",
              r.get("verdict") == "wrong_capability"
              and "/api/chat" not in _calls)

        t = _decision_tools(_show_text, {"/api/generate": _gen_ok})
        check("generate still serves a completion model",
              t["generate"](model="gemma4:12b", prompt="x").get("verdict")
              == "ok")

        t = _decision_tools({"model_info": {"x.context_length": 4096}},
                            {"/api/generate": _gen_ok})
        check("unreported capabilities are not a refusal",
              t["generate"](model="old", prompt="x").get("verdict") == "ok")

        t = _decision_tools(_show_text, {"/v1/systemone": _good})
        r = t["decide"](model="gemma4:12b", state="log", questions=_q)
        check("decide refuses a completion-only model, naming generate",
              r.get("verdict") == "wrong_capability"
              and "generate" in r.get("remedy", "")
              and "/v1/systemone" not in _calls)

        bad_questions = {
            "an empty questions map": {},
            "an unknown question type": {"a": {"type": "maybe",
                                               "instructions": "x"}},
            "a choice with one label": {"a": {"type": "choice",
                                              "instructions": "x",
                                              "criteria": {"only": "one"}}},
            "a score whose levels are not a list": {
                "a": {"type": "score", "instructions": "x",
                      "criteria": {"lo": "a", "hi": "b"}}},
            "a question with no instructions": {"a": {"type": "noul"}},
        }
        for label, qs in bad_questions.items():
            t = _decision_tools(_show_decision, {"/v1/systemone": _good})
            r = t["decide"](model="nimble", state="log", questions=qs)
            check(f"decide refuses {label}, before any call",
                  r.get("verdict") == "invalid_request" and not _calls)

        t = _decision_tools(_show_decision, {"/v1/systemone": _good})
        r = t["decide"](model="nimble", state="log", questions=_q)
        check("decide returns ok, local, with every answer",
              r.get("verdict") == "ok" and r.get("location") == "local"
              and set(r.get("answers", {})) == set(_q))

        sent: dict[str, Any] = {}

        def _capture(payload):
            sent.update(payload)
            return _good

        t = _decision_tools(_show_decision, {"/v1/systemone": _capture})
        t["decide"](model="nimble", state="log", questions=_q)
        check("decide sends state and questions unaltered",
              sent.get("state") == "log" and sent.get("questions") == _q)

        def _mutate(qid: str, key: str, val: Any) -> dict[str, Any]:
            out = json.loads(json.dumps(_good))
            out["answers"][qid][key] = val
            return out

        wrong = {
            "a choice outside the offered labels":
                _mutate("label", "choice", "billing"),
            "probabilities that do not sum to one":
                _mutate("label", "probabilities",
                        {"fault": 0.9, "routine": 0.5}),
            "probabilities over the wrong labels":
                _mutate("label", "probabilities", {"fault": 0.9, "other": 0.1}),
            "a noul outside [0, 1]": _mutate("followup", "noul", 1.3),
            "a score beyond the top level": _mutate("urgency", "score", 2.5),
        }
        for label, resp in wrong.items():
            t = _decision_tools(_show_decision, {"/v1/systemone": resp})
            r = t["decide"](model="nimble", state="log", questions=_q)
            check(f"decide reports {label} as bad_response",
                  r.get("verdict") == "bad_response"
                  and bool(r.get("problems")))

        partial = json.loads(json.dumps(_good))
        del partial["answers"]["urgency"]
        t = _decision_tools(_show_decision, {"/v1/systemone": partial})
        r = t["decide"](model="nimble", state="log", questions=_q)
        check("a question left unanswered is incomplete, not ok",
              r.get("verdict") == "incomplete"
              and any("urgency" in p for p in r.get("problems", [])))

        _404 = Refused("model_not_found", "404 /v1/systemone", "")
        t = _decision_tools(_show_decision, {
            "/v1/systemone": _404, "/api/version": {"version": "0.34.4"}})
        r = t["decide"](model="nimble", state="log", questions=_q)
        check("a 404 from an Ollama older than 0.35.0 is endpoint_unavailable",
              r.get("verdict") == "endpoint_unavailable"
              and "0.35.0" in r.get("remedy", ""))
        t = _decision_tools(None, {
            "/v1/systemone": _404, "/api/version": {"version": "0.40.15"}})
        r = t["decide"](model="no-such-model", state="log", questions=_q,
                        allow_truncation=True)
        check("a 404 for an unknown model on a new Ollama stays model_not_found",
              r.get("verdict") == "model_not_found")

        check("version 0.35.0 supports decide",
              _decide_supported("0.35.0") == "yes")
        check("version 0.34.4 does not", _decide_supported("0.34.4") == "no")
        check("an unreadable version is unknown, not no",
              _decide_supported(None) == "unknown"
              and _decide_supported("dev") == "unknown")

        sent.clear()

        def _gen_capture(payload):
            sent.update(payload)
            return {"response": "", "thinking": "x" * 900,
                    "done_reason": "length"}

        t = _decision_tools(_show_text, {"/api/generate": _gen_capture})
        r = t["generate"](model="gpt-oss:20b", prompt="x", think="low")
        check("think is passed to Ollama and echoed back",
              sent.get("think") == "low" and r.get("think") == "low")
        check("an answer lost to thinking says so in the remedy",
              r.get("verdict") == "empty_generation"
              and r.get("thinking_chars") == 900
              and "think" in r.get("remedy", ""))
        sent.clear()
        t = _decision_tools(_show_text, {"/api/generate": _gen_capture})
        t["generate"](model="gpt-oss:20b", prompt="x")
        check("think left unset is not sent, so the model's default holds",
              "think" not in sent)

        # The /api/show shape observed on Ollama 0.40.12 for gpt-oss:20b.
        t = _decision_tools(dict(_show_text, thinking={
            "values": ["low", "medium", "high"], "default": "medium"}), {})
        r = t["show_model"](model="gpt-oss:20b")
        check("show_model reports the think levels Ollama names",
              r.get("think_levels") == ["low", "medium", "high"]
              and r.get("think_default") == "medium")
        t = _decision_tools(_show_text, {})
        check("show_model reports no think levels when Ollama names none",
              "think_levels" not in t["show_model"](model="gemma4:12b"))
    finally:
        globals()["_request"] = _saved_request_d

    print("Cloud-model labelling")
    # Realism is load-bearing here, but only for RESPONSE SHAPES -- invented
    # /api/show payloads agree with the code that reads them, and reality was
    # the only thing that ever disagreed. Model NAMES are different: these
    # assertions test name parsing, so the name only has to have the right
    # shape. Illustrative names are used wherever the shape is the whole point,
    # because a test fixture should not double as an inventory of somebody's
    # machine.
    check(
        "':cloud' tag is labelled cloud",
        _location("deepseek-v4-pro:cloud", 344) == "cloud",
    )
    check(
        "a real local model is labelled local",
        _location("gemma4:26b", 16_800_000_000) == "local",
    )
    check(
        "':latest' is not mistaken for a cloud tag",
        _location("nomic-embed-text:latest", 261_600_000) == "local",
    )
    check(
        "an untagged name is local",
        _location("orca-mini", 2_000_000_000) == "local",
    )
    check(
        "a slashed repo name with a normal tag stays local",
        _location("hf.co/example-org/example-model-7B-GGUF:Q8_0", 7_200_000_000)
        == "local",
    )
    # A stub-sized model with no cloud tag must NOT be reported local.
    check(
        "stub-sized without a cloud tag is 'cloud?', not 'local'",
        _location("something-odd:latest", 350) == "cloud?",
    )
    check(
        "tag wins over size when both point at cloud",
        _location("glm-5.2:cloud", 338) == "cloud",
    )
    # Size is unavailable wherever only a name is passed; must not fail open.
    check(
        "cloud tag is detected with no size available",
        _location("kimi-k2.7-code:cloud") == "cloud",
    )

    print("stdio hygiene")
    # A log handler on stdout would corrupt MCP framing on the first log line.
    stdout_handlers = [
        h
        for h in log.handlers
        if isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
    ]
    check("no log handler writes to stdout", not stdout_handlers)
    check("logger does not propagate to root", log.propagate is False)

    # ---------------------------------------------------------------------
    # Retrieval tools. Registered only when OLLAMA_MCP_INDEX_DIR is set, so
    # everything below builds a temporary index directory and a stubbed host.
    # ---------------------------------------------------------------------
    import io
    import json as _json
    import tempfile
    from contextlib import redirect_stdout

    import vault_index as vi
    import index_tools

    print("Declared Python floor")
    check("a minimum interpreter version is declared", MIN_PYTHON == (3, 10))
    check(
        "vault_index declares the same floor, not a second opinion",
        vi.MIN_PYTHON == MIN_PYTHON,
    )
    # The check is worthless if the file cannot parse on the version it refuses.
    # No `match`/`case`, no PEP 604 outside annotations, and every module carries
    # `from __future__ import annotations` so the guard can actually speak.
    _sources = [Path(__file__).parent / n for n in
                ("ollama_server.py", "vault_index.py", "index_tools.py")]
    _texts = [p.read_text(encoding="utf-8") for p in _sources if p.exists()]
    check("all three modules were found to inspect", len(_texts) == 3)
    check(
        "every module defers annotation evaluation, keeping the floor reachable",
        all("from __future__ import annotations" in t for t in _texts),
    )
    check(
        "no match/case statement, which would break before the guard runs",
        not any(line.strip().startswith(("match ", "case "))
                and line.rstrip().endswith(":")
                for t in _texts for line in t.splitlines()),
    )
    check(
        "the running interpreter satisfies the declared floor",
        sys.version_info >= MIN_PYTHON,
    )

    print("Retrieval: opt-in and configuration")
    check(
        "index_dir is None when the environment does not name one",
        Config(index_dir=None).index_dir is None,
    )
    check(
        "server_info says the tools are NOT registered when unset",
        "not registered" in _retrieval_tools_note(Config()),
    )
    check(
        "server_info names the directory when they are",
        "/tmp/ix" in _retrieval_tools_note(Config(index_dir="/tmp/ix")),
    )

    print("Retrieval: index format")
    check("a name with a separator is refused", not vi.NAME_RE.match("a/b"))
    check("a name with a dot is refused", not vi.NAME_RE.match("a.b"))
    check("traversal is not a name", not vi.NAME_RE.match("../etc"))
    check("an ordinary name is accepted", bool(vi.NAME_RE.match("homelab_2")))

    entries_a = [{"file": "a.md", "digest": "1111"},
                 {"file": "b.md", "digest": "2222"}]
    entries_b = [{"file": "a.md", "digest": "1111"},
                 {"file": "b.md", "digest": "3333"}]
    gen_a = vi.compute_generation("m", "d: ", "q: ", 1500, entries_a)
    check(
        "generation is stable for identical content",
        gen_a == vi.compute_generation("m", "d: ", "q: ", 1500, entries_a),
    )
    check(
        "generation changes when a file's digest changes",
        gen_a != vi.compute_generation("m", "d: ", "q: ", 1500, entries_b),
    )
    check(
        "generation changes when the embedding model changes",
        gen_a != vi.compute_generation("other", "d: ", "q: ", 1500, entries_a),
    )
    check(
        "generation changes when chunk size changes",
        gen_a != vi.compute_generation("m", "d: ", "q: ", 800, entries_a),
    )
    # Without a separator between hashed fields, ("ab","c") and ("a","bc")
    # collide -- the same read-across-a-seam mistake in a different medium.
    check(
        "field boundaries are hashed, not just the concatenation",
        vi.compute_generation("m", "ab", "c", 1500, entries_a)
        != vi.compute_generation("m", "a", "bc", 1500, entries_a),
    )

    print("Retrieval: chunk citations")
    doc = ("# Alpha\n\n" + "a " * 50 + "\n\n## Beta\n\n" + "b " * 400 + "\n")
    pieces = vi.chunk_text(doc, 300)
    check("chunking returns citation fields, not bare strings",
          all({"text", "heading", "lines"} <= set(p) for p in pieces))
    check("a chunk opening the first section carries its heading",
          pieces[0]["heading"] == "Alpha")
    # A chunk that begins inside the second section must not still be labelled
    # with the first. The heading is the one in force where the chunk STARTS,
    # which for an overlapped chunk is where its tail starts, not where its
    # first whole paragraph does.
    check("a chunk starting inside the second section carries ITS heading",
          any(p["heading"] == "Beta" for p in pieces))
    check("no chunk claims a heading from after where it starts",
          all(p["heading"] in ("", "Alpha", "Beta") for p in pieces))
    check(
        "cited lines contain the chunk's own last block",
        all(p["text"].split("\n\n")[-1].strip()[:30]
            in "\n".join(doc.splitlines()[p["lines"][0] - 1:p["lines"][1]])
            for p in pieces),
    )
    # A `#` comment inside a fence is not a section title. Getting this wrong
    # attaches a confident wrong heading to every chunk after it.
    fenced = "# Real\n\n```python\n# not a heading\n```\n\ntail paragraph\n"
    check("a comment inside a code fence is not read as a heading",
          all(p["heading"] == "Real" for p in vi.chunk_text(fenced, 200)))

    # A note with no blank line anywhere is ONE block. Citing the block for
    # every slice of it gave `lines: [1, 204]` on a real 204-line file --
    # a citation pointing at the whole document. Found 2026-08-30 by looking at
    # actual output, not by any check that existed.
    dense = "\n".join(f"line {n} of a note with no blank lines at all"
                      for n in range(1, 121))
    slices = vi.chunk_text(dense, 400)
    check("a blank-line-free note still splits into several chunks",
          len(slices) > 3)
    check("each slice of one long block cites its OWN lines, not the block's",
          len({tuple(p["lines"]) for p in slices}) == len(slices))
    check("slice line ranges advance through the file",
          all(a["lines"][0] <= b["lines"][0]
              for a, b in zip(slices, slices[1:])))
    check(
        "a slice's cited first line really contains its opening words",
        all(p["text"].split("\n")[0][-20:]
            in dense.splitlines()[p["lines"][0] - 1]
            for p in slices),
    )
    # Containment alone cannot catch an over-WIDE range: citing the whole block
    # for every slice still "contains" each slice. Mutation testing found this
    # -- the end of the range needs its own assertion, pinned to real text.
    check(
        "a slice's cited last line really contains its closing words",
        all(p["text"].split("\n")[-1][:20]
            in dense.splitlines()[p["lines"][1] - 1]
            for p in slices),
    )
    # NOT "all ends are distinct" -- that was asserted and was false. Slices
    # overlap, so the last two windows both clip to the end of the block and
    # legitimately share an end line. The discrimination lives in the check
    # above, which pins each end to real text; these two only bound the shape.
    check("slice cited ends advance through the file",
          all(a["lines"][1] <= b["lines"][1]
              for a, b in zip(slices, slices[1:])))
    check("slice cited ends are not all the same line",
          len({p["lines"][1] for p in slices}) > 1)

    # Platform-independent, and that is the point. Asserting this through a
    # built index passes trivially on Linux, where str() already yields forward
    # slashes -- an assertion that cannot fail in the environment that runs it.
    from pathlib import PureWindowsPath
    check(
        "a Windows corpus path still keys the index with POSIX separators",
        vi.relative_key(PureWindowsPath(r"D:\vault\INVENTORY\07 Models\Note.md"),
                        PureWindowsPath(r"D:\vault"))
        == "INVENTORY/07 Models/Note.md",
    )

    # The 2026-08-26 chunker, kept verbatim as a golden reference. Retrieval
    # quality was tuned against this exact packing arithmetic -- prefixes, chunk
    # size and overlap were all measured against its output -- so a change to it
    # would show up as worse results months later with nothing to point at.
    # Adding headings and line ranges was supposed to be pure bookkeeping; this
    # is what says so.
    import re as _re

    def _v1_chunks(text: str, max_chars: int) -> list[str]:
        paras = [p.strip() for p in _re.split(r"\n\s*\n", text) if p.strip()]
        out: list[str] = []
        current = ""
        for para in paras:
            if len(para) > max_chars:
                if current:
                    out.append(current)
                    current = ""
                step = max_chars - vi.CHUNK_OVERLAP
                for i in range(0, len(para), step):
                    piece = para[i:i + max_chars]
                    if i > 0:
                        cut = piece.find(" ")
                        piece = piece[cut + 1:] if cut != -1 else piece
                    out.append(piece)
                continue
            if len(current) + len(para) + 2 <= max_chars:
                current = f"{current}\n\n{para}" if current else para
            else:
                out.append(current)
                tail = (current[-vi.CHUNK_OVERLAP:]
                        if len(current) > vi.CHUNK_OVERLAP else current)
                if len(tail) < len(current):
                    cut = tail.find(" ")
                    tail = tail[cut + 1:] if cut != -1 else tail
                current = f"{tail}\n\n{para}"
        if current:
            out.append(current)
        return out

    corpus_shapes = [
        doc,
        dense,
        fenced,
        "---\ntitle: t\n---\n\n# H\n\n" + ("word " * 900) + "\n\ntail\n",
        "no headings at all\n\n" + "\n\n".join("para " * 40 for _ in range(8)),
        "# CRLF\r\n\r\nfirst\r\n\r\n" + ("x" * 3000) + "\r\n",
    ]
    identical = all(
        [c["text"] for c in vi.chunk_text(text, size)] == _v1_chunks(text, size)
        for text in corpus_shapes
        for size in (400, vi.TARGET_CHUNK_CHARS, 6553)
    )
    check("chunk TEXT is unchanged from the version retrieval was tuned on",
          identical)

    print("Retrieval: library half is safe inside a server process")
    check("IndexerError is raised, not exited",
          issubclass(vi.IndexerError, Exception)
          and not issubclass(vi.IndexerError, SystemExit))
    _saved_post = vi._post
    try:
        vi._post = lambda *a, **k: (_ for _ in ()).throw(
            vi.IndexerError("stubbed unreachable", verdict="ollama_unreachable"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            vi.rerank("http://stub", "m", "q", [(0.5, "a.md", "text")], 1)
        # stdout is the MCP protocol channel. One stray print corrupts the
        # stream and surfaces as a client-side parse error nowhere near here.
        check("rerank writes nothing to stdout", buf.getvalue() == "")
    finally:
        vi._post = _saved_post

    print("Retrieval: build and status, end to end")
    # A real build against a stubbed embedder. Everything above tests functions
    # in isolation; the Windows path separators that reached a live index on
    # 2026-08-30 were produced HERE, in the one step nothing exercised.
    with tempfile.TemporaryDirectory() as tmp:
        corpus = Path(tmp) / "corpus" / "sub dir"
        corpus.mkdir(parents=True)
        (corpus / "note.md").write_text(
            "# Title\n\nsome prose about fans\n", encoding="utf-8")
        indexes = Path(tmp) / "indexes"

        def _build_stub(host, path, payload, timeout=300):
            if path == "/api/show":
                return {"model_info": {"nomic-bert.context_length": 2048},
                        "parameters": "num_ctx 8192"}
            if path == "/api/embed":
                return {"embeddings": [[1.0, 0.0] for _ in payload["input"]]}
            raise AssertionError(path)

        vi._post = _build_stub
        try:
            class _Args:
                folder = str(Path(tmp) / "corpus")
                name = "built"
                describe = "an end-to-end fixture"
                model = vi.DEFAULT_MODEL
                host = "http://stub"
                batch = 16
                chunk_chars = vi.TARGET_CHUNK_CHARS
                rebuild = True
                dir = str(indexes)

            buf = io.StringIO()
            with redirect_stdout(buf):
                vi.build(_Args())
            written = indexes / f"built{vi.INDEX_SUFFIX}"
            check("build writes <name>.index.json into the index directory",
                  written.exists())
            body = _json.loads(written.read_text(encoding="utf-8"))
            paths = [e["file"] for e in body["files"]]
            # Weak on Linux by construction -- `relative_key` carries the real
            # assertion. This one only confirms build() actually calls it.
            check("build stores the normalised key it was given",
                  paths == ["sub dir/note.md"])
            # Compared RESOLVED, because build() records the resolved root.
            # GitHub's Windows runners put TEMP under an 8.3 short name
            # (C:\Users\RUNNER~1\...) that resolve() expands, so the unresolved
            # form failed there and passed on every machine without one --
            # red on windows-latest from 2026-09-26, the v1.0.0 tag included.
            check("the header records where it was built from",
                  body["source_root"] == str((Path(tmp) / "corpus").resolve()))
            check("the header records the description it was given",
                  body["description"] == "an end-to-end fixture")
            check("counts in the header match the body",
                  body["file_count"] == len(body["files"])
                  and body["chunk_count"] == sum(len(e["chunks"])
                                                 for e in body["files"]))

            _Args.index = "built"
            with redirect_stdout(buf):
                vi.status(_Args())          # clean: returns, does not exit
            check("status on an unchanged corpus does not signal drift", True)

            (corpus / "another.md").write_text("# New\n\nmore\n", encoding="utf-8")
            try:
                with redirect_stdout(buf):
                    vi.status(_Args())
                check("status exits non-zero once the corpus has drifted", False)
            except SystemExit as exc:
                check("status exits non-zero once the corpus has drifted",
                      exc.code == 1)
            check("status names the file that appeared",
                  "another.md" in buf.getvalue())

            # FS-20. Reuse is the difference between a 3-second refresh and a
            # 20-minute one, and it is keyed on the per-file DIGEST. Nothing
            # exercised it: reuse keyed on the path alone would carry a stale
            # vector forward, and the header would look identical either way.
            # `another.md` exists by now -- the drift check above wrote it.
            def _second(rebuild: bool) -> str:
                _Args.rebuild = rebuild
                out = io.StringIO()
                with redirect_stdout(out):
                    vi.build(_Args())
                return out.getvalue()

            incremental = _second(False)
            check("an unchanged file is reused rather than re-embedded",
                  "1 reused" in incremental
                  and "note.md ->" not in incremental)

            (corpus / "note.md").write_text(
                "# Title\n\nsome prose about fans, and now about pumps\n",
                encoding="utf-8")
            changed = _second(False)
            # The path did not change; only the content did. A reuse test that
            # only ever adds files passes with the digest comparison deleted.
            check("a changed file is re-embedded, though its path is the same",
                  "note.md ->" in changed and "1 reused" in changed)

            forced = _second(True)
            check("--rebuild ignores the prior index entirely",
                  "0 reused" in forced)
        finally:
            vi._post = _saved_post
            _Args.rebuild = True

    print("Retrieval: reranking is batched small enough to stay correct")
    # FS-27. Twenty candidates in one ~8,300-token call produced well-formed
    # scores that did not correspond to the passages -- the format was perfect
    # and the judgement was noise. Five per call fixed it, which makes the
    # batch size a CORRECTNESS parameter and not a throughput knob. Nothing
    # said so until here: raising the default back to 20 broke nothing.
    _saved_post = vi._post
    try:
        seen: list[int] = []

        def _batch_probe(host, path, payload, timeout=120):
            prompt = payload["messages"][0]["content"]
            n = int(re.search(r"Below are (\d+) numbered passages",
                              prompt).group(1))
            seen.append(n)
            return {"message": {"content":
                                "\n".join(f"{i}={i % 3}" for i in range(n))},
                    "eval_count": n, "eval_duration": 1_000_000}

        vi._post = _batch_probe
        pool = [(0.9 - i / 100, f"f{i}.md", f"passage {i}") for i in range(12)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            kept = vi.rerank("http://stub", "m", "q", pool, len(pool))
        check("no batch carries more than five passages",
              bool(seen) and max(seen) <= 5)
        check("every candidate is scored, not just the first batch",
              sum(seen) == len(pool) and len(kept) == len(pool))
    finally:
        vi._post = _saved_post

    print("Retrieval: the four tools")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        def _chunk(text, lines, heading, vector):
            return {"text": text, "heading": heading, "lines": lines,
                    "vector": vector}

        good = {
            "format": vi.INDEX_FORMAT, "name": "fixture",
            "description": "a fixture corpus", "source_root": tmp,
            "built_at": "2026-08-30T09:00:00-04:00",
            "builder_version": vi.BUILDER_VERSION, "generation": "abc123",
            "model": "nomic-embed-text:latest",
            "doc_prefix": "search_document: ", "query_prefix": "search_query: ",
            "chunk_chars": 1500, "dimensions": 4,
            "file_count": 2, "chunk_count": 3,
            "files": [
                {"file": "notes/why.md", "digest": "aaaa", "chunks": [
                    _chunk("the fans spin down when the room is cold",
                           [4, 9], "Thermals", [1.0, 0.0, 0.0, 0.0]),
                    _chunk("x" * 70_000, [11, 20], "Big", [0.9, 0.1, 0.0, 0.0]),
                ]},
                {"file": "notes/other.md", "digest": "bbbb", "chunks": [
                    _chunk("unrelated inventory listing",
                           [1, 3], "Inventory", [0.0, 1.0, 0.0, 0.0]),
                ]},
            ],
        }
        (tmp_path / f"fixture{vi.INDEX_SUFFIX}").write_text(
            _json.dumps(good), encoding="utf-8")
        # A v1 index: no format, no headings, no line ranges, no generation.
        (tmp_path / f"legacy{vi.INDEX_SUFFIX}").write_text(_json.dumps({
            "model": "nomic-embed-text:latest", "query_prefix": "search_query: ",
            "files": [{"file": "a.md", "digest": "c", "chunks": [
                {"text": "old", "vector": [1.0, 0.0, 0.0, 0.0]}]}],
        }), encoding="utf-8")

        reg: dict[str, Any] = {}

        class _FakeMCP:
            def tool(self):
                def d(f):
                    reg[f.__name__] = f
                    return f
                return d

        installed = {"ok": True}
        # The stub reranker promotes the candidate the embeddings ranked LAST.
        # Agreeing with embedding order would make the assertion below unable
        # to fail -- it would pass just as well if the reranked order were
        # thrown away, which is exactly the mutation that has to be caught.
        chat_reply = {"message": {"content": "0=0\n1=0\n2=2"}}

        def _stub_post(host, path, payload, timeout=300):
            if path == "/api/show":
                return {} if installed["ok"] else {"error": "model not found"}
            if path == "/api/embed":
                return {"embeddings": [[1.0, 0.0, 0.0, 0.0]
                                       for _ in payload["input"]]}
            if path == "/api/chat":
                return chat_reply
            raise AssertionError(f"unexpected path {path}")

        vi._post = _stub_post
        try:
            index_tools._CACHE.update(key=None, index=None)
            index_tools.register(_FakeMCP(), Config(index_dir=tmp))
            check("four index_* tools registered",
                  {"index_list", "index_search", "index_get",
                   "index_explain"} <= set(reg))

            listed = reg["index_list"]()
            names = [i["name"] for i in listed["indexes"]]
            check("index_list reports the readable index", names == ["fixture"])
            check("index_list reads the description from the header",
                  listed["indexes"][0]["description"] == "a fixture corpus")
            check("index_list reports source_root from the header",
                  listed["indexes"][0].get("source_root") == tmp)
            check("a v1 index is named as unreadable, not silently skipped",
                  [u["name"] for u in listed["unreadable"]] == ["legacy"])
            check("the v1 refusal verdict is unsupported_index",
                  listed["unreadable"][0]["verdict"] == "unsupported_index")
            check("the v1 refusal names the rebuild command",
                  "vault_index.py build" in listed["unreadable"][0]["remedy"])

            hit = reg["index_search"]("fixture", "why do the fans spin down",
                                      k=2, rerank=False)
            check("search succeeds", hit["verdict"] == "ok")
            check("results carry a generation-scoped id",
                  hit["results"][0]["id"] == "abc123:0000")
            check("results carry a line range",
                  hit["results"][0]["lines"] == [4, 9])
            check("results carry the nearest heading",
                  hit["results"][0]["heading"] == "Thermals")
            # The load-bearing decision: citations, not content. A `text` field
            # here would make k a context-budget decision taken before anything
            # is known about relevance.
            check("results carry NO chunk text",
                  all("text" not in r for r in hit["results"]))
            check("the response reports built_at",
                  hit["built_at"] == "2026-08-30T09:00:00-04:00")
            check("staleness is pointed at the CLI, not estimated",
                  "vault_index.py status fixture" in hit["note"])

            check("k above the cap is refused",
                  reg["index_search"]("fixture", "q", k=999)["verdict"]
                  == "invalid_request")
            check("k below 1 is refused",
                  reg["index_search"]("fixture", "q", k=0)["verdict"]
                  == "invalid_request")
            check("a traversal name is refused as an invalid name",
                  reg["index_search"]("../etc/passwd", "q")["verdict"]
                  == "invalid_request")
            check("an unknown index is not found, not crashed",
                  reg["index_search"]("nosuch", "q")["verdict"]
                  == "index_not_found")

            reranked = reg["index_search"]("fixture", "why do the fans spin "
                                           "down", k=2, rerank=True,
                                           rerank_model="stub-ranker")
            check("reranking reports which model judged, and where it ran",
                  reranked["reranked_by"]["model"] == "stub-ranker"
                  and reranked["reranked_by"]["location"] == "local")
            # The pool holds 3 and k is 2. Computing `judged` from the returned
            # rows capped it at k and reported "2 of 3" when the model scored
            # all three -- a participation figure that could not tell the truth.
            check("judged counts the whole pool, not the truncated result",
                  reranked["reranked_by"]["judged"] == 3
                  and reranked["reranked_by"]["of_pool"] == 3)
            check("the score distribution is reported, not just a count",
                  reranked["reranked_by"]["distribution"] == {"2": 1, "0": 2})
            check("a well-spread rerank is NOT flagged as weak",
                  "weak_discrimination" not in reranked["reranked_by"])
            # Saturation is the failure that looks like success: gemma3:4b
            # scored ~45% of candidates as direct answers, so the ties fell
            # through to the cosine tiebreak and the output was embedding order
            # wearing confident labels. It needs its own fixture -- one top
            # score out of three is correct behaviour, not saturation.
            chat_reply["message"] = {"content": "0=2\n1=2\n2=2"}
            saturated = reg["index_search"]("fixture", "q", k=2, rerank=True,
                                            rerank_model="stub-ranker")
            check("a reranker that scores everything alike is called out",
                  "unreranked" in
                  saturated["reranked_by"]["weak_discrimination"])
            check("saturation is visible in the distribution too",
                  saturated["reranked_by"]["distribution"] == {"2": 3})
            chat_reply["message"] = {"content": "0=0\n1=0\n2=2"}
            check("the default pool is one that completes within a client timeout",
                  index_tools.RERANK_POOL == 20)
            # A rerank with partial matches and no direct answer is the shape of
            # a pool cut too narrow. Presenting the partials as the best
            # available answer, silently, is the failure -- pool 20 is a
            # measured compromise and has to admit when it may have been short.
            chat_reply["message"] = {"content": "0=1\n1=1\n2=0"}
            short = reg["index_search"]("fixture", "q", k=2, rerank=True,
                                        rerank_model="stub-ranker")
            check("partial-only results warn that the pool may be short",
                  "pool_may_be_short" in short["reranked_by"])
            check("the warning names a wider pool AND the CLI escape",
                  "pool=6" in short["reranked_by"]["pool_may_be_short"]
                  and "--rerank-pool" in short["reranked_by"]["pool_may_be_short"])
            chat_reply["message"] = {"content": "0=0\n1=0\n2=2"}
            found = reg["index_search"]("fixture", "q", k=2, rerank=True,
                                        rerank_model="stub-ranker")
            check("a direct answer does NOT trigger the pool warning",
                  "pool_may_be_short" not in found["reranked_by"])
            check("an out-of-range pool is refused",
                  reg["index_search"]("fixture", "q", pool=999)["verdict"]
                  == "invalid_request")
            narrowed = reg["index_search"]("fixture", "q", k=2, rerank=True,
                                           rerank_model="stub-ranker", pool=1)
            check("a narrowed pool really reaches the reranker",
                  narrowed["reranked_by"]["of_pool"] == 1)
            check("the reranker's order wins over the embedding order",
                  reranked["results"][0]["id"] == "abc123:0002")
            # The citation must follow the chunk through the re-ordering. This
            # is the one that would break silently: a plausible path with the
            # wrong line range is indistinguishable from a correct one.
            check("a reranked citation still resolves to its own chunk",
                  reranked["results"][0]["path"] == "notes/other.md"
                  and reranked["results"][0]["lines"] == [1, 3])
            check("the reranker's score is reported alongside the citation",
                  reranked["results"][0]["rerank"] == 2.0)

            got = reg["index_get"]("fixture", ["abc123:0000"])
            check("index_get hydrates from the index",
                  got["chunks"][0]["text"].startswith("the fans spin down"))
            check("hydrated chunks carry their citation too",
                  got["chunks"][0]["lines"] == [4, 9])

            stale = reg["index_get"]("fixture", ["999999:0000"])
            check("an id from another generation is REFUSED",
                  stale["verdict"] == "stale_id")
            check("the stale-id refusal says what to do",
                  "index_search" in stale.get("remedy", ""))
            check("a malformed id is an invalid request, not a stale one",
                  reg["index_get"]("fixture", ["nonsense"])["verdict"]
                  == "invalid_request")
            check("an id past the end of the index is reported, not invented",
                  reg["index_get"]("fixture", ["abc123:0099"])
                  .get("not_in_index") == ["abc123:0099"])

            big = reg["index_get"]("fixture", ["abc123:0000", "abc123:0001"])
            check("an oversized hydration is capped",
                  big.get("omitted_for_size") == ["abc123:0001"])
            check("the cap is reported rather than silently applied",
                  "second call" in big.get("note", ""))

            ranking = reg["index_explain"]("fixture", "fans",
                                           "fans spin down")
            check("explain finds an indexed phrase",
                  ranking["hits"][0]["rank"] == 1)
            # Rank 1 is NOT a failure. Calling it a ranking failure was this
            # tool diagnosing a condition that was not present -- the exact
            # shape of error it exists to catch. Seen on the real corpus
            # 2026-08-30, after the selftest had passed.
            check("a passage already at rank 1 is diagnosed as no failure",
                  ranking["diagnosis"] == "no_failure")
            check("the no-failure explanation does not claim a problem",
                  "needs fixing" in ranking["explanation"])
            absent = reg["index_explain"]("fixture", "fans", "no such phrase")
            check("a phrase that is not indexed is diagnosed as recall",
                  absent["diagnosis"] == "recall_failure_or_not_indexed")

            installed["ok"] = False
            index_tools._CACHE.update(key=None, index=None)
            refused = reg["index_search"]("fixture", "q", rerank=False)
            check("a missing embedding model is REFUSED, not substituted",
                  refused["verdict"] == "model_not_found")
            check("the refusal names the model to install",
                  "nomic-embed-text" in refused.get("remedy", ""))
        finally:
            vi._post = _saved_post
            index_tools._CACHE.update(key=None, index=None)

    print("Unreachable host is distinguishable from a missing model")
    dead = Config(base_url="http://127.0.0.1:1", timeout=2.0)
    try:
        _request(dead, "GET", "/api/tags")
        check("dead endpoint raises", False)
    except Refused as exc:
        check("dead endpoint raises", True)
        check(
            "verdict is ollama_unreachable, not model_not_found",
            exc.verdict == "ollama_unreachable",
        )

    print("A timeout is a stated cause, not a traceback")
    # THE 2026-08-31 DEFECT, PINNED.
    #
    # urllib.error.URLError and TimeoutError are SIBLINGS under OSError. A read
    # timeout therefore walked past `except URLError`, past _score_batch's
    # `except IndexerError`, past rerank()'s `failures` collector -- whose only
    # purpose is to absorb exactly this -- and reached an operator as a stack
    # trace on ENV-2, and as a bare tool error through a client on ENV-5.
    #
    # These assertions raise the real exception rather than a stand-in, because
    # the defect was ENTIRELY about which exception type arrives.
    import vault_index as _vi

    class _TimingOutOpener:
        def __enter__(self): raise TimeoutError("timed out")
        def __exit__(self, *a): return False

    _real_urlopen = _vi.urllib.request.urlopen
    _vi.urllib.request.urlopen = lambda *a, **k: _TimingOutOpener().__enter__()
    try:
        _vi._post("http://127.0.0.1:1", "/api/chat", {}, timeout=1)
        check("a read timeout is converted, not propagated", False)
    except _vi.IndexerError as exc:
        check("a read timeout is converted, not propagated", True)
        check("the timeout verdict is ollama_timeout",
              exc.verdict == "ollama_timeout")
        check("the timeout remedy names a lever the operator can pull",
              any(w in (exc.remedy or "") for w in ("--rerank", "smaller")))
        check("the timeout message says the host ANSWERED too slowly, "
              "not that it was unreachable",
              "unreachable" not in str(exc).lower())
    except TimeoutError:
        check("a read timeout is converted, not propagated", False)
    finally:
        _vi.urllib.request.urlopen = _real_urlopen

    # A batch that times out must be COLLECTED, not fatal: the other batches
    # still score and the caller is told which did not.
    # Drive the REAL chain -- _post converts, _score_batch collects, rerank
    # absorbs -- by failing at the socket, the only place the defect lived.
    # Stubbing _score_batch instead would have tested the stub: the first
    # version of this assertion made it raise, which _score_batch never does,
    # and the failure was in the test rather than the code.
    _vi.urllib.request.urlopen = lambda *a, **k: _TimingOutOpener().__enter__()
    try:
        stats: dict = {}
        out = _vi.rerank("http://127.0.0.1:1", "m", "q",
                         [(0.5, "f.md", "text")], 1, stats=stats)
        check("a timing-out rerank returns rather than raising", True)
        check("the caller is told which batch failed",
              bool(stats.get("failures")))
        check("the failure text names the timeout, not a generic error",
              any("did not answer" in f for f in stats.get("failures", [])))
        check("embedding order survives a failed rerank", len(out) == 1)
    except Exception:
        for label in ("a timing-out rerank returns rather than raising",
                      "the caller is told which batch failed",
                      "the failure text names the timeout, not a generic error",
                      "embedding order survives a failed rerank"):
            check(label, False)
    finally:
        _vi.urllib.request.urlopen = _real_urlopen

    print("Documented vault_index.py commands parse against the real CLI")
    # 2026-09-26: the skill told agents to run `build <folder> --out index.json`
    # six weeks after --out became --name. Nothing tied the documents to the
    # parser, so every copy of the skill carried a command that could only
    # fail. This reads every vault_index.py line inside a code fence and parses
    # it with the CLI's own parser -- no second list of flags to go stale.
    import contextlib as _ctx
    import io as _io
    import shlex as _shlex
    import vault_index as _vcli
    _cli = _vcli.build_parser()
    _fence = re.compile(r"^\s*(```|~~~)")
    _cmd = re.compile(r"vault_index\.py\s+(.*)$")
    for _rel in ("README.md", "skills/local-inference-delegation/SKILL.md",
                 "docs/MANUAL.md"):
        _doc = _here / _rel
        if not _doc.exists():
            print(f"  ! {_rel} is not beside this file, so its commands were "
                  "NOT checked")
            continue
        _found = 0
        _inside = False
        _pending = ""
        for _line in _doc.read_text(encoding="utf-8").splitlines():
            if _fence.match(_line):
                _inside = not _inside
                _pending = ""
                continue
            if not _inside:
                continue
            if _pending:
                _line = _pending + " " + _line.strip()
                _pending = ""
            # A trailing `\` (bash) or backtick (PowerShell) continues the line.
            if _line.rstrip().endswith(("\\", "`")):
                _pending = _line.rstrip()[:-1]
                continue
            _m = _cmd.search(_line)
            if not _m:
                continue
            _found += 1
            _text = _m.group(1).split(";")[0].split("|")[0].strip()
            try:
                _argv = _shlex.split(_text, posix=True)
                with _ctx.redirect_stderr(_io.StringIO()), \
                        _ctx.redirect_stdout(_io.StringIO()):
                    _args = _cli.parse_args(_argv)
                # Parsing is not enough: `search index.json` parses, and fails
                # at run time because NAME is a name, not a path. Hold every
                # name the command carries to the CLI's own rule.
                _names = [getattr(_args, k) for k in ("name", "index")
                          if isinstance(getattr(_args, k, None), str)]
                _ok_parse = all(_vcli.NAME_RE.match(n) for n in _names)
            except (SystemExit, ValueError):
                _ok_parse = False
            check(f"{_rel}: `vault_index.py {_text}` is accepted by the CLI", _ok_parse)
        # A document with zero matches would pass every check above vacuously.
        check(f"{_rel}: at least one vault_index.py command was found and checked",
              _found > 0)

    print()
    if failures:
        print(f"SELFTEST FAILED: {len(failures)} assertion(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELFTEST PASSED")
    return 0


def probe() -> int:
    """Live check against the configured Ollama host."""
    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"Config error: {exc}")
        return 1

    print(f"Host: {config.base_url}")
    try:
        raw = _request(config, "GET", "/api/tags", timeout=10)
    except Refused as exc:
        print(f"  {exc.verdict}: {exc.reason}")
        if exc.remedy:
            print(f"  remedy: {exc.remedy}")
        return 1

    models = raw.get("models", [])
    if not models:
        print("  Reached Ollama, but no models are installed.")
        print("  remedy: run `ollama pull llama3.1:8b`")
        return 1

    hosted = 0
    print(f"  Reachable. {len(models)} model(s):")
    for m in sorted(models, key=lambda x: (x.get("name") or "").lower()):
        name = m.get("name") or m.get("model")
        loc = _location(name, m.get("size"))
        if loc != "local":
            hosted += 1
        print(f"    [{loc:<6}] {name:<48} {_human_size(m.get('size'))}")

    if hosted:
        print()
        print(
            f"  {hosted} of {len(models)} run on Ollama's hosted infrastructure, "
            "not this machine."
        )
        print(
            "  Content sent to those leaves the host. Permitted and sometimes "
            "the right choice --"
        )
        print("  the point is that it should be a choice, not a surprise.")
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_server() -> tuple[Any, Config]:
    """The SDK renamed FastMCP to MCPServer in v2.0. Accept both."""
    try:
        from mcp.server.fastmcp import FastMCP as ServerClass
    except ImportError:
        from mcp.server import MCPServer as ServerClass  # type: ignore[attr-defined]

    config = Config.from_env()
    mcp = ServerClass("ollama")
    register(mcp, config)

    # Opt-in, and the import is inside the branch on purpose: an unset
    # OLLAMA_MCP_INDEX_DIR means the retrieval module is never even loaded, so
    # "the default clone exposes ten tools" is a fact about what runs rather
    # than a promise about what is registered.
    if config.index_dir:
        import index_tools

        index_tools.register(mcp, config)
    return mcp, config


def main() -> int:
    if "--version" in sys.argv:
        print(__version__)
        return 0
    if "--selftest" in sys.argv:
        return selftest()
    if "--probe" in sys.argv:
        return probe()

    try:
        mcp, config = build_server()
    except ValueError as exc:
        log.error("Configuration refused: %s", exc)
        return 2

    log.info(
        "ollama-mcp starting: host=%s delete=%s pull=%s allowlist=%s",
        config.base_url,
        config.allow_delete,
        config.allow_pull,
        ",".join(config.model_allowlist) or "none",
    )
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
