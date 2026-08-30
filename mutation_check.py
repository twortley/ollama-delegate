#!/usr/bin/env python3
"""
Mutation check: break the code on purpose, and fail if `--selftest` still passes.

    python mutation_check.py

WHY THIS EXISTS
---------------
A passing test suite says the assertions ran. It does not say any of them could
have failed. This project has now found three assertions that could not:

  * a POSIX-path check asserted through a built index, which passes trivially on
    Linux because `str(PurePath)` already yields forward slashes there -- the
    defect it was written for can only occur on Windows
  * two line-range checks that tested CONTAINMENT, so widening a citation to the
    whole file still satisfied them
  * a reranker fixture whose stub agreed with embedding order, so "the reranked
    order is used" passed just as well when the reranked order was discarded

None was visible by reading the suite. All three were found here.

THE BASELINE GUARD IS THE POINT
-------------------------------
An earlier version of this script reported 12/12 caught while the selftest was
already failing on unmutated code: every mutant was "caught" by a pre-existing
failure. **A perfect score from a broken measurement** -- the exact trap this
project keeps finding, occurring inside the tool used to look for it.

So the first thing this does is prove the baseline is green, and refuse to run
otherwise. A mutation score is meaningless without it.

ADDING A MUTANT
---------------
One line: the file, an exact source string, its replacement, and a description
of the behaviour being broken. If the anchor no longer matches, that is reported
rather than silently skipped -- a mutant that does not apply is not a mutant
that passed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# (file, find, replace, what it breaks)
MUTANTS: list[tuple[str, str, str, str]] = [
    # --- declared Python floor --------------------------------------------
    ("ollama_server.py", "MIN_PYTHON = (3, 10)", "MIN_PYTHON = (3, 9)",
     "let the two modules disagree about the declared floor"),
    ("vault_index.py", "from __future__ import annotations",
     "from __future__ import generator_stop",
     "drop the future import that keeps the floor guard reachable"),
    # --- index identity ---------------------------------------------------
    ("vault_index.py", 'h.update(b"\\x00")', "pass",
     "drop the NUL separator in the generation hash"),
    ("vault_index.py", "if fmt != INDEX_FORMAT:", "if False:",
     "read a v1 index instead of refusing it"),
    ("vault_index.py", "if not model_installed(host, model):", "if False:",
     "substitute a different embedding model"),
    ("vault_index.py", "return path.relative_to(root).as_posix()",
     "return str(path.relative_to(root))",
     "store the platform's path separators in the index"),
    # --- citations --------------------------------------------------------
    ("vault_index.py", 'block["start"] + para.count("\\n", 0, offset)',
     'block["start"]', "cite the block start for every slice of it"),
    ("vault_index.py", 'block["start"] + para.count("\\n", 0, stop)',
     'block["end"]', "cite the block end for every slice of it"),
    # --- the tool surface -------------------------------------------------
    ("index_tools.py", "elif gen != generation:", "elif False:",
     "hydrate an id from a different generation"),
    ("index_tools.py", '"rerank": r.get("rerank"),',
     '"rerank": r.get("rerank"), "text": r["text"],',
     "return chunk text from search instead of citations"),
    ("index_tools.py", "order = [rows[item[5]] for item in scored]",
     "order = rows[:k]", "discard the reranked order"),
    ("index_tools.py", "if not isinstance(k, int) or not 1 <= k <= K_MAX:",
     "if False:", "drop the k bound"),
    ("index_tools.py", 'if _fits({"chunks": chunks + [candidate]}):', "if True:",
     "drop the response byte cap"),
    ("index_tools.py", 'if best["rank"] <= DEFAULT_K:', "if False:",
     "diagnose a rank-1 passage as a ranking failure"),
    # --- rerank reporting -------------------------------------------------
    ("index_tools.py", "RERANK_POOL = 20", "RERANK_POOL = 40",
     "restore a pool that exceeds the client timeout"),
    ("index_tools.py", 'reranked_by["pool_may_be_short"] = (',
     'reranked_by["_unused"] = (',
     "hide that a partial-only rerank may have had too narrow a pool"),
    ("index_tools.py", "for r in rows[:pool]]", "for r in rows[:RERANK_POOL]]",
     "ignore the caller's pool argument"),
    ("index_tools.py", '"judged": run.get("judged"),', '"judged": len(judgements),',
     "count judged candidates after truncation to k"),
    ("index_tools.py", '"distribution": run.get("distribution"),',
     '"distribution": None,', "drop the score distribution"),
    ("index_tools.py",
     'if top_value and spent[top_value] > (run.get("judged") or 0) / 2:',
     "if False:", "never flag a saturated reranker"),
]


def run_selftest(cwd: str) -> tuple[bool, list[str]]:
    result = subprocess.run(
        [sys.executable, "ollama_server.py", "--selftest"],
        cwd=cwd, capture_output=True, text=True,
    )
    return result.returncode == 0, re.findall(r"FAIL  (.+)", result.stdout)


def main() -> int:
    green, failures = run_selftest(HERE)
    if not green:
        print("BASELINE IS RED. Mutation results would be meaningless -- every "
              "mutant would be 'caught' by a failure that is already there.")
        for name in failures:
            print(f"    {name}")
        return 2
    print(f"baseline green, {len(MUTANTS)} mutants\n")

    survived, missing = [], []
    for filename, find, replace, label in MUTANTS:
        work = tempfile.mkdtemp()
        for entry in os.listdir(HERE):
            if entry.endswith(".py"):
                shutil.copy(os.path.join(HERE, entry), work)

        target = os.path.join(work, filename)
        with open(target, encoding="utf-8") as handle:
            source = handle.read()
        if find not in source:
            # Not a pass. The mutant never applied, so nothing was tested.
            missing.append(label)
            print(f"  ANCHOR GONE  {label}")
            continue
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(source.replace(find, replace, 1))

        passed, names = run_selftest(work)
        if passed:
            survived.append(label)
            print(f"  SURVIVED     {label}")
        else:
            caught_by = names[0] if names else "(crashed)"
            extra = f" (+{len(names) - 1})" if len(names) > 1 else ""
            print(f"  caught       {label}\n                 by: {caught_by}{extra}")

    total = len(MUTANTS)
    print(f"\n{total - len(survived) - len(missing)}/{total} caught")
    if survived:
        print("\nSURVIVING MUTANTS -- no assertion detects these changes:")
        for label in survived:
            print(f"  - {label}")
    if missing:
        print("\nMUTANTS THAT NO LONGER APPLY -- the code moved, fix the anchor:")
        for label in missing:
            print(f"  - {label}")
    return 1 if survived or missing else 0


if __name__ == "__main__":
    sys.exit(main())
