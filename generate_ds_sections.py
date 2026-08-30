#!/usr/bin/env python3
"""
Generate the mechanical sections of docs/DESIGN.md directly from source.

Five sections of the design specification are FACTS ABOUT THE CODE rather than
prose about it:

    tools      MCP tool signatures and their return contracts
    cli        the vault_index.py command-line surface
    config     every environment variable read, with its default
    verdicts   the verdict vocabulary
    schema     the on-disk index file schema

Hand-writing those is how a design specification goes stale without anyone
noticing: the document keeps asserting last month's surface, and nothing in the
repository disagrees with it. Three such claims were already live when this
script was written -- a `status` subcommand that does not exist, an index
"generation hash" that was never implemented, and an environment variable read
by the server but absent from its own docstring. The third is the instructive
one: it is invisible from the docstring, so a careful human copying the
docstring would have reproduced the omission exactly.

So these sections are read out of the AST instead. Nothing here executes the
modules it documents -- it parses them. That matters: importing the server has
side effects (it configures logging from the environment), and a documentation
generator that runs its subject is a documentation generator that can fail for
reasons having nothing to do with documentation.

    python generate_ds_sections.py             # rewrite the generated blocks
    python generate_ds_sections.py --check     # exit 1 if they are out of date
    python generate_ds_sections.py --stdout    # print everything, write nothing
    python generate_ds_sections.py --out PATH  # update a copy held elsewhere

`--check` is the point. It turns staleness into a build failure rather than a
discovery, which is the only reason to generate these at all.

`--out` exists because the working copy of this document is edited outside the
repository and published into it. Both copies carry the same markers, so either
can be refreshed in place, and running the generator after publishing corrects
any drift the working copy picked up rather than carrying it forward.

Reads:  ollama_server.py, vault_index.py
Writes: only the regions of the target document between matching marker pairs

    <!-- BEGIN GENERATED: tools -->
    <!-- END GENERATED: tools -->

Everything outside those markers is hand-written and is never touched. A marker
naming an unknown section, or a BEGIN without its END, is an error rather than
a silent skip -- a generator that quietly declines to update a section is the
staleness it exists to prevent, wearing a green tick.

stdlib only, same as everything else here.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SERVER = HERE / "ollama_server.py"
INDEXER = HERE / "vault_index.py"
# The retrieval tools live in their own module, registered only when
# OLLAMA_MCP_INDEX_DIR is set. Reading only ollama_server.py would have left
# them out of the generated tool table while `--check` reported the document up
# to date -- a drift check that passes because it is looking at the wrong file
# is worse than no drift check, since it is believed.
RETRIEVAL = HERE / "index_tools.py"
DESIGN = HERE / "docs" / "DESIGN.md"

MARKER = re.compile(
    r"(?P<begin><!--\s*BEGIN GENERATED:\s*(?P<name>[a-z_]+)\s*-->)"
    r"(?P<body>.*?)"
    r"(?P<end><!--\s*END GENERATED:\s*(?P=name)\s*-->)",
    re.S,
)


# ---------------------------------------------------------------- ast helpers


def parse(path: Path) -> ast.Module:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]
    return tree


def enclosing_def(node: ast.AST) -> str:
    """Name of the nearest enclosing function, or '<module>'."""
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = getattr(cur, "parent", None)
    return "<module>"


def src(node: ast.AST | None) -> str:
    return "" if node is None else ast.unparse(node)


def summary(fn: ast.FunctionDef) -> str:
    """
    The docstring's first PARAGRAPH, joined into one line.

    Not its first line: these docstrings wrap at 79 columns, so a first-line
    summary truncates mid-clause -- `delete_model` came out as "Destructive
    and", which reads as a defect in the document rather than in the extractor.
    """
    para: list[str] = []
    for line in (ast.get_docstring(fn) or "").splitlines():
        if not line.strip():
            if para:
                break
            continue
        para.append(line.strip())
    return " ".join(para)


def module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    """Module-level `NAME = value` bindings, for resolving defaults by name."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            out[node.targets[0].id] = node.value
    return out


def literal(node: ast.AST | None, consts: dict[str, ast.AST], depth: int = 0) -> str:
    """
    Best-effort readable value for a default or a `choices` expression.

    A design specification saying a default is `TARGET_CHUNK_CHARS` has told the
    reader nothing they could not have guessed; the whole point is the number.
    Follows one level of module constant, and unwraps the two shapes that
    actually occur here -- `sorted(SOME_DICT)` and `os.environ.get(...)`.
    """
    if node is None or depth > 2:
        return src(node)
    if isinstance(node, ast.Constant):
        return f"`{node.value!r}`"
    if isinstance(node, ast.Call) and src(node.func) == "os.environ.get" and node.args:
        var = node.args[0].value
        fallback = literal(node.args[1], consts, depth + 1) if len(node.args) > 1 else "unset"
        return f"`${var}`, else {fallback}"
    if isinstance(node, ast.Call) and src(node.func) == "sorted" and node.args:
        target = node.args[0]
        if (
            isinstance(target, ast.Name)
            and isinstance(consts.get(target.id), ast.Dict)
        ):
            keys = [
                f"`{k.value}`"
                for k in consts[target.id].keys  # type: ignore[attr-defined]
                if isinstance(k, ast.Constant)
            ]
            return ", ".join(sorted(keys))
    if isinstance(node, ast.Name) and node.id in consts:
        return f"{literal(consts[node.id], consts, depth + 1)} — `{node.id}`"
    return f"`{src(node)}`"


def signature(fn: ast.FunctionDef) -> str:
    a = fn.args
    pad = [None] * (len(a.args) - len(a.defaults))
    parts = []
    for arg, default in zip(a.args, pad + list(a.defaults)):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {src(arg.annotation)}"
        if default is not None:
            text += f" = {src(default)}"
        parts.append(text)
    ret = f" -> {src(fn.returns)}" if fn.returns else ""
    return f"{fn.name}({', '.join(parts)}){ret}"


# ------------------------------------------------------------------- verdicts
#
# A verdict can be introduced four ways, and all four are collected. Missing any
# one of them produces a vocabulary that looks complete -- which is the failure
# this whole document set is shaped against.


def walk_shallow(fn: ast.AST):
    """
    Like `ast.walk`, but does NOT descend into nested `def`s or `class`es.

    Required for attribution to mean anything. With a full walk, `register()`
    appears to raise every verdict in the server, because all nine tools are
    nested inside it -- a table that is true of nobody.
    """
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def verdicts_in(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in walk_shallow(fn):
        # {"verdict": "..."}
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "verdict"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    out.add(value.value)
        # result["verdict"] = "..."
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "verdict"
                    and isinstance(node.value.value, str)
                ):
                    out.add(node.value.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # raise Refused("verdict", ...)
            if node.func.id == "Refused" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    out.add(node.args[0].value)
            # _ok(...) is the success verdict, spelled once in a helper
            if node.func.id == "_ok":
                out.add("ok")
    return out


def called_names(fn: ast.AST) -> set[tuple[str, bool]]:
    """{(name, is_attribute)} called directly by this def. `guard.check` -> ("check", True)."""
    out: set[tuple[str, bool]] = set()
    for node in walk_shallow(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                out.add((node.func.id, False))
            elif isinstance(node.func, ast.Attribute):
                out.add((node.func.attr, True))
    return out


class CallGraph:
    """
    Every `def` in a module, keyed by qualified name, with what it raises and
    what it calls.

    QUALIFIED NAMES ARE NOT DECORATION. Keying on the bare name silently loses
    to collisions, and this file has one: `Guard.check` -- the single
    enforcement function, which raises `not_permitted` and `invalid_request` --
    collides with the nested `check()` helper inside `selftest()`, which raises
    nothing. Bare-name indexing let the second overwrite the first, and the
    generated tool contracts dropped `not_permitted` from every gated tool.
    The output looked complete and was wrong about the security surface.
    """

    def __init__(self, tree: ast.Module):
        self.defs: dict[str, ast.AST] = {}
        self.classes: set[str] = set()
        self._index(tree, "")
        self.own = {q: verdicts_in(n) for q, n in self.defs.items()}
        self.calls = {q: called_names(n) for q, n in self.defs.items()}

    def _index(self, node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self.classes.add(child.name)
                self._index(child, f"{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + child.name
                self.defs[qual] = child
                self._index(child, f"{qual}.")
            else:
                self._index(child, prefix)

    def resolve(self, name: str, is_attribute: bool) -> list[str]:
        if is_attribute:
            # A method call can only reach a method. This is what keeps
            # `guard.check` off `selftest`'s local helper.
            return [
                q
                for q in self.defs
                if q.endswith(f".{name}") and q.split(".")[0] in self.classes
            ]
        if name in self.defs:  # module-level function
            return [name]
        nested = [
            q
            for q in self.defs
            if q.endswith(f".{name}") and q.split(".")[0] not in self.classes
        ]
        return nested if len(nested) == 1 else []

    def reachable(self, qual: str) -> set[str]:
        """
        Verdicts a tool can actually return, following calls transitively.

        A tool's own body names only a few. `generate` reads as though it
        returns three; through `Guard.check`, `_request`, `_check_context` and
        `_apply_completion_verdict` it can return thirteen. Documenting only the
        literal ones would describe a contract the code does not honour.
        """
        seen: set[str] = set()
        out: set[str] = set()
        stack = [qual]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            out |= self.own.get(current, set())
            for name, is_attr in self.calls.get(current, set()):
                stack.extend(self.resolve(name, is_attr))
        return out


# ---------------------------------------------------------------------- tools


def is_tool_decorator(dec: ast.AST) -> bool:
    return (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr == "tool"
    )


def collect_tools(tree: ast.Module, source: str = "ollama_server.py") -> list[dict[str, Any]]:
    register = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "register"
        ),
        None,
    )
    if register is None:
        raise SystemExit(f"{source} has no register() -- tool extraction failed")

    graph = CallGraph(tree)
    tools = []
    for node in ast.walk(register):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not any(is_tool_decorator(d) for d in node.decorator_list):
            continue

        ok_keys: list[str] = []
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_ok"
            ):
                for kw in call.keywords:
                    key = kw.arg if kw.arg else f"**{src(kw.value)}"
                    if key not in ok_keys:
                        ok_keys.append(key)

        tools.append(
            {
                "name": node.name,
                "signature": signature(node),
                "summary": summary(node),
                "ok_keys": ok_keys,
                "verdicts": sorted(graph.reachable(f"register.{node.name}")),
            }
        )
    tools.sort(key=lambda t: t["name"])
    # Not a style check. These counts are asserted in three other documents, and
    # this is the only place that can notice when they stop being true. They are
    # per-module now: the bridge is nine tools always registered, the retrieval
    # module is four registered only when OLLAMA_MCP_INDEX_DIR is set, and a
    # single total would hide either one moving.
    expected = {"ollama_server.py": 9, "index_tools.py": 4}.get(source)
    if expected is not None and len(tools) != expected:
        print(f"note: {len(tools)} tools found in {source}; the record says "
              f"{expected}", file=sys.stderr)
    return tools


def render_tools(tools: list[dict[str, Any]]) -> str:
    out = [
        f"**{len(tools)} tools, registered in a single `register()`.** Every one "
        "returns a dict carrying a `verdict`; there is no other success channel "
        "and no exception reaches the client.",
        "",
    ]
    for t in tools:
        out.append(f"#### `{t['name']}`")
        out.append("")
        if t["summary"]:
            out.append(t["summary"])
            out.append("")
        out.append("```python")
        out.append(t["signature"])
        out.append("```")
        out.append("")
        if t["ok_keys"]:
            keys = ", ".join(
                "throughput fields, when Ollama reports them"
                if k.startswith("**_throughput")
                else f"`{k}`"
                for k in t["ok_keys"]
            )
            out.append(f"**Returns on success:** {keys}")
            out.append("")
        out.append(
            "**Verdicts reachable:** "
            + ", ".join(f"`{v}`" for v in t["verdicts"])
        )
        out.append("")
    return "\n".join(out).rstrip()


# ------------------------------------------------------------------------ cli


def collect_cli(tree: ast.Module) -> list[dict[str, Any]]:
    """
    Subcommands and their flags, read from the argparse construction.

    Read from `add_parser` / `add_argument` rather than from the module
    docstring: the docstring is prose and can claim a subcommand that was never
    built. One did.
    """
    var_to_cmd: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "add_parser"):
            continue
        if not (call.args and isinstance(call.args[0], ast.Constant)):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        help_text = next(
            (src(kw.value).strip("'\"") for kw in call.keywords if kw.arg == "help"), ""
        )
        var_to_cmd[target.id] = {
            "name": call.args[0].value,
            "help": help_text,
            "args": [],
        }
        order.append(target.id)

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in var_to_cmd
        ):
            continue
        flags = [a.value for a in node.args if isinstance(a, ast.Constant)]
        # Keep the AST nodes, not their source text: a default of
        # `TARGET_CHUNK_CHARS` has to be resolved to 1500 to be worth printing.
        opts = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        var_to_cmd[node.func.value.id]["args"].append({"flags": flags, "opts": opts})

    if not order:
        raise SystemExit(
            "no subcommands found in vault_index.py -- the argparse "
            "construction has changed shape (this matches on "
            "`name = <parser>.add_parser(...)`). An empty CLI section renders "
            "as nothing at all, and --check would still pass.")

    return [var_to_cmd[v] for v in order]


def render_cli(commands: list[dict[str, Any]], consts: dict[str, ast.AST]) -> str:
    out = [
        f"**{len(commands)} subcommands.** "
        + ", ".join(f"`{c['name']}`" for c in commands)
        + ". There are no others.",
        "",
    ]
    for cmd in commands:
        out.append(f"#### `vault_index.py {cmd['name']}`")
        out.append("")
        if cmd["help"]:
            out.append(f"{cmd['help'].capitalize()}.")
            out.append("")
        out.append("| Argument | Default | Notes |")
        out.append("|---|---|---|")
        for arg in cmd["args"]:
            flags = ", ".join(f"`{f}`" for f in arg["flags"])
            opts = arg["opts"]
            if "default" in opts:
                default = literal(opts["default"], consts)
            elif src(opts.get("action")) == "'store_true'":
                default = "`False`"
            elif arg["flags"] and arg["flags"][0].startswith("-"):
                # A flag with no default is a REQUIRED FLAG, not a positional.
                # Calling `--name` positional would tell a reader to type the
                # value bare, which argparse then rejects.
                default = "*required*"
            else:
                default = "*required, positional*"

            notes = []
            if "choices" in opts:
                notes.append(f"choices: {literal(opts['choices'], consts)}")
            if "const" in opts:
                notes.append(f"bare flag uses {literal(opts['const'], consts)}")
            if "type" in opts:
                notes.append(f"parsed as `{src(opts['type'])}`")
            if "help" in opts:
                # Strip the table's own markup before it is spliced into prose,
                # or `%(default)s` expands to a backtick salad.
                plain = re.sub(r"[`*]", "", default).split(" — ")[0]
                notes.append(_flatten(opts["help"], plain))
            out.append(f"| {flags} | {default} | {'; '.join(notes) or '—'} |")
        out.append("")
    return "\n".join(out).rstrip()


def _flatten(node: ast.AST, default: str) -> str:
    """
    Collapse an argparse `help=` expression into one readable line.

    Substitutes `%(default)s`, which argparse expands at parse time and which
    would otherwise be published verbatim as a placeholder -- a documentation
    artefact reading as though the value were unknown.
    """
    try:
        text = ast.literal_eval(node)  # implicit adjacent-string concatenation
    except (ValueError, SyntaxError):
        text = src(node)
    text = " ".join(str(text).split())
    return text.replace("%(default)s", default.strip("`"))


# --------------------------------------------------------------------- config


def collect_config(trees: dict[str, ast.Module]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for filename, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kind = None
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and src(node.func.value) == "os.environ"
            ):
                kind = "str"
            elif isinstance(node.func, ast.Name) and node.func.id == "_env_flag":
                kind = "flag"
            if not kind or not node.args:
                continue
            # `os.environ.get(INDEX_DIR_ENV, "")` names the variable through a
            # module constant. Resolving it matters: skipping it silently
            # dropped OLLAMA_MCP_INDEX_DIR from vault_index.py's row of this
            # table, so the document said the indexer reads one environment
            # variable when it reads two.
            name_node = node.args[0]
            if isinstance(name_node, ast.Name):
                const = module_constants(tree).get(name_node.id)
                if isinstance(const, ast.Constant) and isinstance(const.value, str):
                    name_node = const
            if not isinstance(name_node, ast.Constant):
                continue

            parent = getattr(node, "parent", None)
            coercion = ""
            if (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Name)
                and parent.func.id in ("float", "int", "_normalise_base_url")
            ):
                coercion = parent.func.id

            found.append(
                {
                    "var": name_node.value,
                    "default": src(node.args[1]) if len(node.args) > 1 else "*unset*",
                    "kind": kind,
                    "coercion": coercion,
                    "file": filename,
                    "where": enclosing_def(node),
                }
            )

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in found:
        merged.setdefault((entry["file"], entry["var"]), entry)
    return sorted(merged.values(), key=lambda e: (e["file"], e["var"]))


def render_config(entries: list[dict[str, Any]]) -> str:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_file.setdefault(entry["file"], []).append(entry)

    out = [
        "**The two components have separate configuration surfaces and they are "
        "not interchangeable.** `vault_index.py` is a CLI and takes its settings "
        "as flags. The one variable they share is `OLLAMA_MCP_INDEX_DIR`, which "
        "names the directory the CLI writes indexes to and the `index_*` tools "
        "read them from — deliberately the same value, because an index the "
        "tools cannot see is the failure it exists to prevent. No other "
        "`OLLAMA_MCP_*` variable reaches the indexer.",
        "",
        f"**{len(entries)} environment reads across "
        f"{len(by_file)} files.** Every one is listed; this table is generated "
        "from the call sites, not from any docstring.",
        "",
    ]
    for filename in sorted(by_file):
        out.append(f"#### `{filename}`")
        out.append("")
        out.append("| Variable | Default | Type | Read in |")
        out.append("|---|---|---|---|")
        for e in by_file[filename]:
            kind = "boolean flag" if e["kind"] == "flag" else "string"
            if e["coercion"] in ("float", "int"):
                kind = e["coercion"]
            elif e["coercion"]:
                kind = f"string, via `{e['coercion']}()`"
            out.append(
                f"| `{e['var']}` | `{e['default']}` | {kind} | `{e['where']}` |"
            )
        out.append("")
    return "\n".join(out).rstrip()


# ------------------------------------------------------------------- verdicts


def render_verdicts(server_tree: ast.Module) -> str:
    graph = CallGraph(server_tree)
    introduced: dict[str, set[str]] = {}
    for qual, verdicts in graph.own.items():
        # The selftest constructs verdicts to assert against them. Those are
        # test fixtures, not part of the surface a caller sees, and listing them
        # would overstate where a verdict can originate.
        if qual.startswith("selftest"):
            continue
        # `register.` is a registration detail, not part of a tool's identity.
        label = qual[len("register."):] if qual.startswith("register.") else qual
        for v in verdicts:
            introduced.setdefault(v, set()).add(label)

    out = [
        f"**{len(introduced)} verdicts, one vocabulary.** A caller branches on "
        "`verdict` and never on the presence or absence of a field. `ok` is the "
        "only success value; every other value names a specific failure, and "
        "*“the check did not happen”* is spelled differently from "
        "*“the check passed”*.",
        "",
        "| Verdict | Raised in |",
        "|---|---|",
    ]
    for verdict in sorted(introduced):
        where = ", ".join(f"`{w}`" for w in sorted(introduced[verdict]))
        out.append(f"| `{verdict}` | {where} |")
    return "\n".join(out)


# --------------------------------------------------------------------- schema


def collect_schema(tree: ast.Module) -> dict[str, list[tuple[str, str]]]:
    build = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "build"
        ),
        None,
    )
    if build is None:
        raise SystemExit("vault_index.py has no build() -- schema extraction failed")

    def pairs(node: ast.Dict) -> list[tuple[str, str]]:
        return [
            (k.value, src(v))
            for k, v in zip(node.keys, node.values)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]

    root: list[tuple[str, str]] = []
    entry: list[tuple[str, str]] = []
    chunk: list[tuple[str, str]] = []

    for node in ast.walk(build):
        if (
            isinstance(node, ast.Call)
            and src(node.func) == "json.dumps"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            root = pairs(node.args[0])
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and src(node.func.value) == "entries"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            entry = pairs(node.args[0])
            for key, value in zip(node.args[0].keys, node.args[0].values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "chunks"
                    and isinstance(value, ast.ListComp)
                    and isinstance(value.elt, ast.Dict)
                ):
                    chunk = pairs(value.elt)

    # Each of these is found by matching a specific expression shape inside
    # build(). Rewriting build() to assemble the header in a variable first
    # would leave the match returning nothing -- and an empty schema renders as
    # an empty table rather than an error, which is the silent failure this
    # generator exists to prevent.
    for level, fields in (("root", root), ("file entry", entry),
                          ("chunk", chunk)):
        if not fields:
            raise SystemExit(
                f"schema extraction found no {level!r} fields in "
                f"vault_index.py build(). The literal it matches on has moved; "
                "fix the extractor rather than shipping an empty schema.")

    return {"root": root, "file entry": entry, "chunk": chunk}


def render_schema(schema: dict[str, list[tuple[str, str]]]) -> str:
    out = [
        "**One JSON file, written whole.** There is no database, no sidecar and "
        "no index-level content hash: change detection is per file, on the "
        "`digest` field below. The file is not committed — `.gitignore` "
        "excludes `/*.json`, because a built index contains the corpus.",
        "",
        "```",
        "{",
    ]
    def field(indent: int, key: str, expr: str) -> str:
        # Show the source expression only where it says more than the key does.
        # `"file": // rel` is noise; the `dimensions` expression is the answer to
        # "where does this number come from", which is exactly a DS question.
        note = f"  // {expr}" if not expr.isidentifier() else ""
        return f'{" " * indent}"{key}":{note}'

    for key, expr in schema["root"]:
        if key == "files":
            out.append('  "files": [')
            out.append("    {")
            for k2, e2 in schema["file entry"]:
                if k2 == "chunks":
                    out.append('      "chunks": [')
                    out.append("        {")
                    for k3, e3 in schema["chunk"]:
                        out.append(field(10, k3, e3))
                    out.append("        }, ...")
                    out.append("      ]")
                else:
                    out.append(field(6, k2, e2))
            out.append("    }, ...")
            out.append("  ]")
        else:
            out.append(field(2, key, expr))
    out.append("}")
    out.append("```")
    out.append("")
    out.append("| Level | Fields |")
    out.append("|---|---|")
    for level, fields in schema.items():
        names = ", ".join(f"`{k}`" for k, _ in fields)
        out.append(f"| {level} | {names} |")
    return "\n".join(out)


# ------------------------------------------------------------------ rendering


def generate() -> dict[str, str]:
    server = parse(SERVER)
    indexer = parse(INDEXER)
    retrieval = parse(RETRIEVAL)

    tools = collect_tools(server) + collect_tools(retrieval, RETRIEVAL.name)
    # Assert the second module actually contributed. Without this, renaming
    # `register` in index_tools.py would drop four tools from the document and
    # `--check` would still pass, because nothing it reads would have changed.
    if not any(t["name"].startswith("index_") for t in tools):
        raise SystemExit(
            f"{RETRIEVAL.name} contributed no index_* tools -- extraction is "
            "reading the wrong thing, or the tools moved.")

    return {
        "tools": render_tools(tools),
        "cli": render_cli(collect_cli(indexer), module_constants(indexer)),
        "config": render_config(
            collect_config({SERVER.name: server, INDEXER.name: indexer,
                            RETRIEVAL.name: retrieval})
        ),
        "verdicts": render_verdicts(server),
        "schema": render_schema(collect_schema(indexer)),
    }


def apply(text: str, sections: dict[str, str]) -> tuple[str, list[str]]:
    """Replace every marked region. Returns the new text and the names seen."""
    seen: list[str] = []

    def swap(m: re.Match[str]) -> str:
        name = m.group("name")
        if name not in sections:
            raise SystemExit(
                f"docs/DESIGN.md marks a section named {name!r}, which this "
                f"generator does not produce. Known: {', '.join(sorted(sections))}"
            )
        seen.append(name)
        return f"{m.group('begin')}\n\n{sections[name]}\n\n{m.group('end')}"

    return MARKER.sub(swap, text), seen


def check_unpaired(text: str) -> None:
    begins = set(re.findall(r"<!--\s*BEGIN GENERATED:\s*([a-z_]+)\s*-->", text))
    ends = set(re.findall(r"<!--\s*END GENERATED:\s*([a-z_]+)\s*-->", text))
    if begins != ends:
        raise SystemExit(
            "unbalanced generated markers in docs/DESIGN.md: "
            f"BEGIN without END {sorted(begins - ends)}, "
            f"END without BEGIN {sorted(ends - begins)}"
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the generated blocks are out of date; write nothing",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="print every section and write nothing (works with no DESIGN.md yet)",
    )
    p.add_argument(
        "--out",
        metavar="PATH",
        default=str(DESIGN),
        help="document to update (default %(default)s). The working copy of this "
             "document lives outside the repository; point --out at it to "
             "refresh its generated blocks in place.",
    )
    args = p.parse_args()
    target = Path(args.out)

    sections = generate()

    if args.stdout:
        for name in ("tools", "cli", "config", "verdicts", "schema"):
            print(f"<!-- BEGIN GENERATED: {name} -->\n")
            print(sections[name])
            print(f"\n<!-- END GENERATED: {name} -->\n")
        return 0

    if not target.exists():
        print(f"{target} does not exist. Run with --stdout to see the sections, "
              "or create the document with the BEGIN/END markers in place.")
        return 1

    original = target.read_text(encoding="utf-8")
    check_unpaired(original)
    updated, seen = apply(original, sections)

    missing = sorted(set(sections) - set(seen))
    if missing:
        print(f"note: {target.name} has no marker for {', '.join(missing)}")

    if args.check:
        if updated != original:
            print(
                f"STALE: the generated sections of {target} do not match the "
                "source. Run `python generate_ds_sections.py` and commit."
            )
            return 1
        print(f"{target} is current ({len(seen)} generated section(s)).")
        return 0

    if updated == original:
        print(f"{target} already current ({len(seen)} section(s)).")
        return 0

    target.write_text(updated, encoding="utf-8", newline="\n")
    print(f"{target} updated ({len(seen)} section(s) regenerated).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
