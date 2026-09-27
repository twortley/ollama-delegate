#!/usr/bin/env python3
"""
Render the verification report from `verification_manifest.json`, and refuse to
render one that cannot be trusted.

    python generate_verification.py --manifest PATH --out PATH
    python generate_verification.py --manifest PATH --check
    python generate_verification.py --manifest PATH --release-gate

WHERE THE MANIFEST LIVES
------------------------
Outside this repository, and `--manifest` is required because of it.

The manifest is the verification report's WORKING COPY, exactly as the design
specification's working copy sits outside the repository and is published into
it. Two reasons it is not carried here:

  * It cites issue identifiers that resolve in no published document. The
    disclosure scan runs on documents that get PUBLISHED; a manifest committed
    straight to a public repository would never pass through it.
  * A repository that ignores `/*.json` for build artefacts will silently
    swallow a root-level manifest. That happened on 2026-08-30 -- the register
    was invisible to `git status` and nothing said so.

The generator ships here; the register does not. Same split as the design
specification and its generator.

WHY THIS IS GENERATED
---------------------
On 2026-08-30 the design specification was published with section 11 stating the
retrieval tools were implemented while section 15's trace matrix still listed six
of them as not built. The drift check passed the whole time, because the matrix
was hand-written and nothing compared it to the sections it cited.

A verification report fails the same way and in a worse direction: it is read as
evidence. So the report is rendered from a manifest, and the manifest is checked
against reality before anything is written.

WHAT THE CHECKS ARE FOR
-----------------------
Not to demonstrate that a process was followed. Each one exists because it can
find a specific defect that has actually occurred in this project:

  * a test citing a `--selftest` assertion that no longer exists -- the report
    would go on quoting evidence that stopped being produced
  * a `pass` with no record of where or when it was produced
  * a test claiming ENV-4 while needing a live model, which ENV-4 cannot reach:
    "a result attributed to ENV-4 that required a live model did not come from
    ENV-4"
  * an entry with no falsifier, which is a demonstration rather than a test
  * a requirement in URS_FS with no test and no conscious disclosure

The last one is the point of the whole file. An untested requirement should
surface as a hole, not as an absence nobody notices.

REUSING THIS OUTSIDE THIS PROJECT
---------------------------------
The gates are generic; the coupling to this repository is two things, and they
are the only edits an extraction needs:

  * `selftest_labels()` hardcodes `ollama_server.py --selftest`. Parameterise
    the command and the label pattern.
  * `NO_HOST_ENVS` names this project's unit-test environment. Parameterise it,
    or move it into the manifest as a property of each environment.

Everything else -- the two-phase gate, the falsifier requirement, the evidence
requirement on a pass, the register cross-check -- is about verification, not
about Ollama.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

RESULTS = {"pass", "fail", "not_run", "deferred"}

# Two gates, because the standard differs by phase.
#
# --check is the DEVELOPMENT gate. A requirement may be untested and a test may
# be failing; that is what work in progress looks like, and hiding it would be
# worse.
#
# --release-gate is the PUBLICATION gate, and it enforces three rules:
#
#   1. No requirement ships untested. Not one.
#   2. No test ships `fail` without a mitigation -- an issue raised, and a
#      disposition: fixed and retested, or deferred to a named release.
#   3. Therefore `❌` never appears in a published requirements register. It is
#      an honest development state and an unacceptable published one, because a
#      reader takes a published register as a statement of what was verified.
#
# `deferred` is the terminal state for a gap shipped knowingly: it REQUIRES an
# issue id and a release decision. A gap with a decision behind it is a normal
# release. A gap without one is a claim that fails when someone checks it.
RELEASE_TERMINAL = {"pass", "deferred"}

# The marks a requirements register uses. `❌` and `◐` are development states.
PUBLISHABLE_MARKS = {"✅", "⚠️"}
# Tiers mirror the requirement register's own method codes rather than
# inventing a second vocabulary. `inspection` is a legitimate method, not a
# weaker one: a structural claim -- "there is exactly one call site" -- is
# settled by reading the code, and executing something would prove less.
TIERS = {"assertion", "scripted", "manual", "inspection"}
# ENV-4 is the unit-test environment. It reaches no Ollama endpoint, by
# construction, so any test needing a live model cannot have run there.
NO_HOST_ENVS = {"ENV-4", "ENV-8"}

# The defect triage register's decision vocabulary, and no other. A second
# decision language is how two findings end up describing the same thing and
# neither gets acted on -- the same argument that keeps the risk scale single.
DECISIONS = {"fix before release", "release with disclosure",
             "defer with justification"}
# A decision is what was decided; `status` is whether it has been carried out.
# The pair is what makes the register enforceable rather than readable: an OPEN
# defect whose decision was `fix before release` is the one combination a list
# of defects cannot catch by being read.
DEFECT_STATUS = {"open", "resolved"}

MARK = {"pass": "PASS", "fail": "FAIL 🔴", "not_run": "NOT RUN",
        "disclosed": "DISCLOSED"}


def load(manifest: pathlib.Path) -> dict:
    if not manifest.exists():
        raise SystemExit(
            f"No manifest at {manifest}.\n"
            "The verification register lives outside this repository -- pass "
            "--manifest with its path. See this file's docstring for why.")
    return json.loads(manifest.read_text(encoding="utf-8"))


def selftest_labels() -> set[str] | None:
    """Every check label --selftest currently prints. None if it cannot run."""
    try:
        r = subprocess.run([sys.executable, "ollama_server.py", "--selftest"],
                           cwd=HERE, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    return set(re.findall(r"^\s*(?:PASS|FAIL)\s+(.+?)\s*$", r.stdout, re.M))


def defect_index(data: dict) -> tuple[list[dict], dict[str, str]]:
    """(the defect register, {internal issue id: public defect id})."""
    defects = data.get("defects") or []
    public = {d["tracked_as"]: d["id"] for d in defects
              if d.get("tracked_as") and d.get("id")}
    return defects, public


def publish_ids(text: str, public: dict[str, str]) -> str:
    """
    Internal issue identifiers become the public defect id this report defines.

    The register is what makes the substitution legitimate: the internal id
    resolves in no published document, and the public one resolves a few
    paragraphs up. An identifier with NO register entry is left exactly as it
    is, so the disclosure scan refuses on it -- substituting a placeholder would
    conceal the gap the scan exists to find.
    """
    if not text or not public:
        return text
    pattern = r"\b(?:%s)\b" % "|".join(re.escape(k) for k in sorted(public))
    return re.sub(pattern, lambda m: public[m.group(0)], text)


def requirement_states(urs_fs: pathlib.Path | None) -> dict[str, str]:
    """
    {identifier: state mark} from the requirements register.

    The register's state column is hand-maintained and the manifest is not, so
    they drift -- and they have. On 2026-08-30 four identifiers stood at `❌`
    while the item that closed them was marked done. Reading both and comparing
    is the only thing that catches it.
    """
    if not urs_fs or not urs_fs.exists():
        return {}
    text = urs_fs.read_text(encoding="utf-8")
    states: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*\*\*((?:UR|FS)-\d+)\*\*\s*\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        mark = next((c for c in reversed(cells) if c in {"✅", "❌", "⚠️", "◐"}), "")
        states[m.group(1)] = mark
    return states


def check(data: dict, labels: set[str] | None, states: dict[str, str],
          release: bool = False) -> list[str]:
    defined = set(states)
    findings: list[str] = []
    seen_ids: set[str] = set()
    tests = data["tests"]
    defects, public = defect_index(data)

    for t in tests:
        tid = t.get("id", "<no id>")
        if tid in seen_ids:
            findings.append(f"{tid}: duplicate test id")
        seen_ids.add(tid)

        if t.get("result") not in RESULTS:
            findings.append(f"{tid}: result {t.get('result')!r} is outside "
                            f"{sorted(RESULTS)}")
        if t.get("tier") not in TIERS:
            findings.append(f"{tid}: tier {t.get('tier')!r} is outside {sorted(TIERS)}")

        # A demonstration is not a test.
        if not (t.get("falsifier") or "").strip():
            findings.append(f"{tid}: no falsifier -- this is a demonstration, "
                            f"not a test")
        if not (t.get("expected") or "").strip():
            findings.append(f"{tid}: no expected outcome")

        # Evidence, or it did not happen.
        if t.get("result") in ("pass", "fail"):
            if not t.get("run_on"):
                findings.append(f"{tid}: result {t['result']!r} with no run_on -- "
                                f"a result without where and when is an opinion")
            if not (t.get("observed") or "").strip():
                findings.append(f"{tid}: result {t['result']!r} with nothing observed")

        # `deferred` is a decision, not a synonym for untested. It needs both an
        # issue to carry the work and a recorded decision not to do it now.
        if t.get("result") == "deferred":
            if not (t.get("release_decision") or "").strip():
                findings.append(f"{tid}: deferred with no release_decision recorded")
            if not (t.get("issue") or "").strip():
                findings.append(f"{tid}: deferred with no issue raised -- a deferral "
                                f"with nothing tracking it is an omission")
        # A failure is publishable only once it has become an issue with a
        # disposition. Until then it is a defect nobody has decided about.
        if t.get("result") == "fail" and not (t.get("issue") or "").strip():
            findings.append(f"{tid}: FAILING with no issue raised -- a failed test "
                            f"must raise an issue that is then fixed or deferred")

        # An issue a test cites must be DEFINED in the defect register. This is
        # UR-27's bar enforced rather than asserted: an issue identifier that
        # the report does not define is one a reader cannot resolve, and the
        # disclosure scan refuses the document on it.
        issue = (t.get("issue") or "").strip()
        if issue and defects and issue not in public:
            findings.append(f"{tid}: cites the issue {issue}, which the defect "
                            f"register does not define -- a reader cannot "
                            f"resolve it, and the disclosure scan refuses it")

        # The ENV-4 rule, enforced rather than remembered.
        envs = set(t.get("env") or [])
        if t.get("live_host") and envs & NO_HOST_ENVS:
            findings.append(f"{tid}: claims {sorted(envs & NO_HOST_ENVS)} and needs a "
                            f"live host -- that environment reaches no Ollama endpoint")
        ran_in = (t.get("run_on") or {}).get("env")
        if t.get("live_host") and ran_in in NO_HOST_ENVS:
            findings.append(f"{tid}: recorded as run on {ran_in}, which cannot reach "
                            f"a model. The result did not come from there")

        # A report must not cite evidence that has stopped being produced.
        if t.get("tier") == "assertion" and labels is not None:
            for label in t.get("asserts") or []:
                if label not in labels:
                    findings.append(f"{tid}: cites the assertion {label!r}, which "
                                    f"--selftest no longer prints")

        for ident in t.get("discharges") or []:
            if defined and re.match(r"^(UR|FS)-\d+$", ident) and ident not in defined:
                findings.append(f"{tid}: discharges {ident}, which the requirements "
                                f"register does not define")

    # Coverage, in the direction that finds holes.
    covered: dict[str, list[str]] = {}
    for t in tests:
        for i in t.get("discharges") or []:
            covered.setdefault(i, []).append(t.get("result", "?"))
    if defined:
        for ident in sorted(defined - set(covered)):
            findings.append(f"{ident}: no test and no deferral -- an untested "
                            f"requirement must be a hole, not an absence")

        # The register's state column against what the tests actually found.
        for ident, results in sorted(covered.items()):
            mark = states.get(ident)
            if mark is None:
                continue
            if mark == "✅" and any(r in ("fail", "not_run") for r in results):
                findings.append(f"{ident}: register says ✅ but its test(s) are "
                                f"{results} -- the tick is not supported")
            if mark == "❌" and all(r == "pass" for r in results):
                findings.append(f"{ident}: register says ❌ but every test passes "
                                f"-- the register is behind the evidence")

    seen_defects: set[str] = set()
    for d in defects:
        did = d.get("id", "<no id>")
        if did in seen_defects:
            findings.append(f"{did}: duplicate defect id")
        seen_defects.add(did)
        # A defect with no release note is a defect nobody decided how to
        # describe, which is how a known gap ships quietly.
        for field in ("title", "severity", "rationale", "release_note"):
            if not (d.get(field) or "").strip():
                findings.append(f"{did}: no {field}")
        if d.get("release_decision") not in DECISIONS:
            findings.append(f"{did}: release_decision "
                            f"{d.get('release_decision')!r} is outside "
                            f"{sorted(DECISIONS)}")
        if d.get("status") not in DEFECT_STATUS:
            findings.append(f"{did}: status {d.get('status')!r} is outside "
                            f"{sorted(DEFECT_STATUS)}")

    if release:
        # A decision taken and not carried out. Reading the register cannot
        # catch this; comparing the decision against the status can.
        for d in defects:
            if (d.get("release_decision") == "fix before release"
                    and d.get("status") != "resolved"):
                findings.append(f"RELEASE: {d.get('id')} was decided `fix before "
                                f"release` and is still open. Fix it, or change "
                                f"the decision and say why")

        # Rule 3: a published register carries no development marks.
        for ident in sorted(defined):
            mark = states.get(ident, "")
            if mark and mark not in PUBLISHABLE_MARKS:
                findings.append(f"RELEASE: {ident} stands at {mark} in the "
                                f"requirements register. A published register "
                                f"carries only {' or '.join(sorted(PUBLISHABLE_MARKS))} "
                                f"-- resolve it, or defer it with an issue")
        # Rules 1 and 2: nothing ships untested, nothing ships failing without a
        # disposition.
        for t in tests:
            if t.get("result") not in RELEASE_TERMINAL:
                findings.append(f"RELEASE: {t.get('id')} is {t.get('result')!r}. "
                                f"Every test must reach `pass` or `deferred` "
                                f"(with an issue and a release decision) before "
                                f"publication")
    return findings


def frontmatter(doc: dict | None) -> list[str]:
    """
    YAML frontmatter from the register's `document` block, in the order given.

    The other published documents carry frontmatter and this one carried none,
    so the report that states what was verified was the only one with no title,
    status, revision or date. Every value comes from the register -- never the
    clock -- so two renders of one register are identical and --check can
    compare them.
    """
    if not doc:
        return []

    def scalar(v: object) -> str:
        text = str(v)
        if text == "" or any(c in text for c in ':#"\'') or text != text.strip():
            return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
        return text

    out = ["---"]
    for key, value in doc.items():
        if isinstance(value, list):
            out.append(f"{key}:")
            out += [f"  - {scalar(v)}" for v in value]
        else:
            out.append(f"{key}: {scalar(value)}")
    return out + ["---", ""]


def render(data: dict, findings: list[str]) -> str:
    tests = data["tests"]
    defects, public = defect_index(data)

    def pub(text: str) -> str:
        return publish_ids(text or "", public)

    def cell(text: str) -> str:
        """
        A table cell is one line. A field with a newline in it ends the row, and
        everything after it renders as loose paragraph text under a broken
        table -- which is what twelve of these rows were doing, unnoticed,
        because nobody had read the rendered report as a stranger would. The
        content is unchanged; only the line structure is.
        """
        return (pub(text).replace("|", r"\|")
                .replace("\r\n", "\n").replace("\n", "<br>"))
    counts = {r: sum(1 for t in tests if t.get("result") == r) for r in sorted(RESULTS)}

    out = frontmatter(data.get("document")) + [
        "<!-- GENERATED from verification_manifest.json by "
        "generate_verification.py. Do not hand-edit: your changes will be "
        "overwritten and, worse, will not be checked. -->",
        "",
        "# Verification Report",
        "",
        f"**{len(tests)} tests — "
        + " · ".join(f"{n} {r.replace('_', ' ')}" for r, n in counts.items() if n)
        + ".**"
        + (f" **{len(data['defects'])} known defects, each with a release "
           f"decision.**" if data.get("defects") else ""),
        "",
        "Every entry carries an expected outcome and a **falsifier**. An entry "
        "without a falsifier is a demonstration that a process was followed, and "
        "the generator refuses to render one.",
        "",
        "`disclosed` is a conscious release decision with a release note — **not "
        "a synonym for untested.** A gap shipped knowingly is a normal release; a "
        "gap shipped quietly is a claim that fails when someone checks it.",
        "",
    ]

    if findings:
        out += [
            "> ## ⚠ This report is inconsistent with the code it describes",
            ">",
            "> The checks below found problems the report cannot resolve on its "
            "own. **Read them before reading anything else here** — a "
            "verification report is taken as evidence, so an unsound one is "
            "worse than none.",
            ">",
        ]
        out += [f"> - {f}" for f in findings]
        out.append("")

    if defects:
        out += [
            f"## Known defects, and what was decided about each  ({len(defects)})",
            "",
            "**Every defect this project knows about — including the ones already "
            "fixed — with the decision taken on it.** A gap shipped knowingly is a "
            "normal release; a gap shipped quietly is a claim that fails when "
            "someone checks it.",
            "",
            "Decisions come from one vocabulary — **fix before release**, "
            "**release with disclosure**, **defer with justification** — and "
            "`status` says whether the decision has been carried out. The release "
            "gate refuses a defect decided *fix before release* that is still "
            "open.",
            "",
            "| | Defect | Severity | Decision | Status |",
            "|---|---|---|---|---|",
        ]
        for d in defects:
            out.append(f"| **{d.get('id')}** | {cell(d.get('title'))} | "
                       f"{d.get('severity')} | {d.get('release_decision')} | "
                       f"{d.get('status')} |")
        out.append("")
        for d in defects:
            out += [
                f"### {d.get('id')} — {pub(d.get('title'))}",
                "",
                f"**{d.get('severity')} · {d.get('release_decision')} · "
                f"{d.get('status')}**",
                "",
                "| | |", "|---|---|",
                f"| Affects | {', '.join(f'`{i}`' for i in d.get('affects') or []) or '—'} |",
                f"| Evidence | {', '.join(f'`{i}`' for i in d.get('evidence') or []) or '—'} |",
                f"| Rationale | {cell(d.get('rationale'))} |",
                f"| Release note | {cell(d.get('release_note'))} |",
                "",
            ]

    for tier, heading, blurb in [
        ("assertion", "Automated — runs with no host",
         "Executed by `--selftest` and `mutation_check.py`. Each names the exact "
         "check labels it relies on, and the generator fails if a label stops "
         "being printed."),
        ("scripted", "Scripted — a command and its output",
         "Reproducible by anyone with the repository and, where noted, a host."),
        ("inspection", "Inspection — settled by reading, not by running",
         "A structural claim is verified by reading the code. Executing "
         "something would prove less, not more: a passing call shows one path "
         "worked, not that only one path exists."),
        ("manual", "Manual — live host, specific client, or a human",
         "Written so someone who was not present can run them."),
    ]:
        group = [t for t in tests if t.get("tier") == tier]
        if not group:
            continue
        out += [f"## {heading}  ({len(group)})", "", blurb, ""]
        for t in group:
            out += [
                f"### {t['id']} — {pub(t['title'])}",
                "",
                f"**Result: {MARK.get(t.get('result'), t.get('result'))}**"
                # `build` is rendered because it is REQUIRED on a live-host
                # result and was reaching no reader: 23 of these entries carried
                # one and the report printed none of them. An `unknown` is the
                # half that matters most -- that value exists to be seen, not to
                # sit in the register satisfying a gate.
                + (f" · {t['run_on']['env']}, {t['run_on']['date']}"
                   f"{', Python ' + t['run_on']['python'] if t['run_on'].get('python') else ''}"
                   f"{', build `' + t['run_on']['build'] + '`' if t['run_on'].get('build') else ''}"
                   if t.get("run_on") else ""),
                "",
                f"| | |", "|---|---|",
                f"| Discharges | {', '.join(f'`{i}`' for i in t.get('discharges') or []) or '—'} |",
                f"| Environment | {', '.join(t.get('env') or []) or 'any'}"
                f"{' · **needs a live host**' if t.get('live_host') else ''} |",
                f"| Procedure | {cell(t.get('procedure', '—'))} |",
                f"| Expected | {cell(t.get('expected', '—'))} |",
                f"| **Falsifier** | {cell(t.get('falsifier', '—'))} |",
            ]
            if t.get("observed"):
                out.append(f"| Observed | {cell(t['observed'])} |")
            if t.get("issue"):
                out.append(f"| Issue | {public.get(t['issue'], t['issue'])} |")
            if t.get("release_decision"):
                out.append(f"| Release decision | {cell(t['release_decision'])} |")
            # `notes` is internal provenance -- run records in other projects,
            # host paths, who found what and when. It is worth keeping and is
            # not worth publishing, so it ships to the vault copy inside the
            # markers the publisher strips. Inline, because a table row broken
            # across lines is no longer a table row.
            if t.get("notes"):
                out.append("<!-- VAULT ONLY -->"
                           f"| Note | {cell(t['notes'])} |"
                           "<!-- END VAULT ONLY -->")
            out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", required=True,
                    help="path to the verification register. Required: it lives "
                         "outside this repository -- see the module docstring")
    ap.add_argument("--out", default=None, help="write the rendered report here")
    ap.add_argument("--check", action="store_true",
                    help="development gate: consistency findings, exit 1 on any")
    ap.add_argument("--release-gate", action="store_true", dest="release",
                    help="publication gate: additionally refuse any untested "
                         "requirement, any test not at pass/deferred, and any "
                         "development mark left in the requirements register")
    ap.add_argument("--urs", default=None,
                    help="path to the requirements register, for coverage and "
                         "state cross-checking")
    args = ap.parse_args()

    data = load(pathlib.Path(args.manifest))
    labels = selftest_labels()
    if labels is None:
        print("  ! --selftest could not be run; assertion labels were NOT "
              "verified. This report is weaker than it looks.", file=sys.stderr)
    states = requirement_states(pathlib.Path(args.urs) if args.urs else None)
    if args.urs and not states:
        print(f"  ! no requirement identifiers found in {args.urs}; coverage and "
              f"state were NOT checked", file=sys.stderr)

    findings = check(data, labels, states, release=args.release)
    for f in findings:
        print(f"  ! {f}", file=sys.stderr)

    if args.out:
        # The rendered report always carries the DEVELOPMENT findings, so a
        # reader sees them even when nobody ran the gate.
        dev = check(data, labels, states, release=False)
        pathlib.Path(args.out).write_text(render(data, dev), encoding="utf-8",
                                          newline="\n")
        print(f"  {len(data['tests'])} tests -> {args.out}")

    if args.check or args.release:
        gate = "RELEASE GATE" if args.release else "check"
        print(f"  {gate}: {len(findings)} finding(s)", file=sys.stderr)
        return 1 if findings else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
