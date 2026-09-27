#!/usr/bin/env python3
"""
Build the skill ZIP that a release attaches, and check it is one a skills-aware
client will accept.

    python ci/build_skill_zip.py [OUT.zip]      # default: dist/local-inference-delegation.zip

What is being tested is the ARCHIVE, not the tool that made it. A client that
takes an uploaded skill reads the ZIP's entry names, so the checks are on those:

  * every entry sits under one top-level folder named after the skill
  * that folder holds SKILL.md at its root
  * entry names use `/`, never `\\` -- the separator the ZIP format specifies,
    and the one some writers get wrong on Windows
  * SKILL.md's frontmatter `name` matches the folder, and `description` is
    present and no longer than 1,024 characters

Built with Python's zipfile so the same command gives the same archive on every
platform, with fixed timestamps so two builds of one commit are byte-identical.
Exits 1 and says which check failed.
"""
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = "local-inference-delegation"
SRC = ROOT / "skills" / SKILL
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def build(out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in SRC.rglob("*") if p.is_file())
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            name = f"{SKILL}/{p.relative_to(SRC).as_posix()}"
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())


def verify(out: pathlib.Path) -> list[str]:
    problems = []
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        if not names:
            return ["the archive is empty"]
        for n in names:
            if "\\" in n:
                problems.append(f"entry uses a backslash separator: {n!r}")
            if not n.startswith(f"{SKILL}/"):
                problems.append(f"entry outside the {SKILL}/ folder: {n!r}")
        if f"{SKILL}/SKILL.md" not in names:
            return problems + [f"{SKILL}/SKILL.md is not in the archive"]
        text = z.read(f"{SKILL}/SKILL.md").decode("utf-8")
    fm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not fm:
        return problems + ["SKILL.md has no frontmatter block"]
    name = re.search(r'^name:\s*"?([^"\n]+)"?\s*$', fm.group(1), re.M)
    desc = re.search(r'^description:\s*"?(.*?)"?\s*$', fm.group(1), re.M)
    if not name or name.group(1).strip() != SKILL:
        problems.append(f"frontmatter name is {name.group(1) if name else None!r}, "
                        f"not {SKILL!r}")
    if not desc or not desc.group(1).strip():
        problems.append("frontmatter description is missing")
    elif len(desc.group(1)) > 1024:
        problems.append(f"description is {len(desc.group(1))} characters; the limit is 1,024")
    return problems


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / f"{SKILL}.zip"
    build(out)
    problems = verify(out)
    for p in problems:
        print(f"  FAIL  {p}")
    with zipfile.ZipFile(out) as z:
        for n in z.namelist():
            print(f"  entry {n}")
    print(f"{out.name}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
