#!/usr/bin/env python3
"""
Compose a release's notes from the two documents that already hold them, and
refuse a tag that disagrees with the code.

    python ci/release_notes.py v1.0.0 > notes.md

  * the tag must equal `v` + ollama_server.__version__
  * CHANGELOG.md must have a `## [<version>]` section -- it becomes the top of
    the notes
  * the known-defects section of docs/VERIFICATION.md is appended as it stands,
    so every defect ships with its severity and release note, and the notes
    cannot disagree with the verification report: they are the same text

Nothing is transcribed by hand. Exits 1 naming what is missing.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def section(text: str, heading: re.Pattern) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if heading.match(line):
            level = len(line) - len(line.lstrip("#"))
            body = [line]
            for nxt in lines[i + 1:]:
                if re.match(rf"^#{{1,{level}}} ", nxt):
                    break
                body.append(nxt)
            return "\n".join(body).strip()
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: release_notes.py <tag>", file=sys.stderr)
        return 2
    tag = sys.argv[1]
    src = (ROOT / "ollama_server.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__ = "([^"]+)"', src, re.M)
    version = m.group(1) if m else None
    problems = []
    if tag != f"v{version}":
        problems.append(f"tag {tag} does not match __version__ {version}")
    changes = section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
                      re.compile(rf"^## \[{re.escape(str(version))}\]"))
    if not changes:
        problems.append(f"CHANGELOG.md has no '## [{version}]' section")
    defects = section((ROOT / "docs" / "VERIFICATION.md").read_text(encoding="utf-8"),
                      re.compile(r"^## Known defects"))
    if not defects:
        problems.append("docs/VERIFICATION.md has no '## Known defects' section")
    if problems:
        for p in problems:
            print(f"  FAIL  {p}", file=sys.stderr)
        return 1
    # CHANGELOG's own heading is redundant under a release titled with the tag.
    changes = changes.split("\n", 1)[1].strip() if "\n" in changes else ""
    print(changes)
    print()
    print(defects)
    print()
    print("Full verification report: [docs/VERIFICATION.md](docs/VERIFICATION.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
