"""Compute every number in the paper from the two data repositories; write paper/gen/*.tex.

Data (cloned into data/ on first run, then checked out at the pinned commits):
  monperrus/boot-to-claude        the OS, its git history
  monperrus/boot-to-claude-paper  the Claude Code transcripts that built it

Usage:
  stats.py            regenerate paper/gen/
  stats.py --json     print the computed numbers as JSON instead
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GEN = ROOT / "paper" / "gen"

OS_REPO = ("monperrus/boot-to-claude", "a4a64cbbb751f3498f5ab722d51df856aabcdc95")
TRAJ_REPO = ("monperrus/boot-to-claude-paper", "cd9643e2b01f0130e0a619146b4e7309aeed1ce0")
MAIN_SESSION = "trajectories/03468d1c-5b29-4e24-8f0d-2a7435d227ab.jsonl"

# A gap between two consecutive transcript events longer than this is idle time
# (the agent waiting for the human), not work.
IDLE = timedelta(minutes=10)
CEST = timezone(timedelta(hours=2))

# Rows of the timeline table: short hash -> label. Commits not listed here are folded
# into the next listed one.
PHASES = {
    "42f0af3": "0: boot sector, build pipeline",
    "0b4c5c1": "1: two-stage loader, protected mode",
    "1a67c58": "2: GDT/IDT, allocator, self-test",
    "a3b80d1": "3: ATA driver, tinyfs",
    "594867b": "4: keyboard driver, shell",
    "f4702ba": "5: scripting language",
    "3c37ed4": "6: compiler, bytecode VM",
    "24ac0a4": "7: README, docs, final check",
    "cd6231d": "T1: assembler",
    "72955af": "T2/T4: C compiler",
    "f08a1b9": "T3: adapt kernel to tinyC",
    "1647d6f": "T5: linker",
    "0f82a8d": "T6: drop \\texttt{objcopy}",
    "1d62d14": "T7: switch the build over",
    "acff0a5": "T8: self-hosting bootstrap",
    "900c4cf": "T9: docs, cleanup",
    "4246ba7": "serial-playable battle game",
    "377d751": "VGA mode 13h, bitmap font",
    "a4a64cb": "graphical battle screen",
}
EPISODE_BREAKS = {"24ac0a4", "900c4cf"}  # horizontal rule after these rows
# Verification site rendered by tools/site.py; the paper links into it.
SITE = "https://monperrus.github.io/boot-to-claude-website"

SIZE_ROWS = [
    ("Boot loader (\\texttt{boot/})", "boot/", 0),
    ("Kernel (\\texttt{kernel/})", "kernel/", 0),
    ("of which languages (\\texttt{kernel/lang/})", "kernel/lang/", 1),
    ("of which game (\\texttt{kernel/game/})", "kernel/game/", 1),
    ("of which tinyfs (\\texttt{kernel/fs/})", "kernel/fs/", 1),
    ("of which shell (\\texttt{kernel/shell/})", "kernel/shell/", 1),
    ("Toolchain (\\texttt{toolchain/})", "toolchain/", 0),
    ("of which C compiler", "toolchain/cc/", 1),
    ("of which assembler", "toolchain/as/", 1),
    ("of which linker", "toolchain/ld/", 1),
    ("Build and test tools (\\texttt{tools/})", "tools/", 0),
    ("Test fixtures (\\texttt{tests/})", "tests/", 0),
    ("Design documents (\\texttt{docs/})", "docs/", 0),
]
SOURCE_EXT = (".c", ".h", ".S", ".sh", ".py", ".md", ".tsh", ".tc")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def fetch(slug: str, pin: str) -> Path:
    path = DATA / slug.split("/")[1]
    if not path.exists():
        DATA.mkdir(exist_ok=True)
        subprocess.run(["gh", "repo", "clone", slug, str(path), "--", "-q"], check=True)
    if git(path, "rev-parse", "HEAD").strip() != pin:
        git(path, "fetch", "-q", "origin")
        git(path, "checkout", "-q", pin)
    return path


# ---- OS repository -------------------------------------------------------------

@dataclass
class Commit:
    sha: str
    when: datetime
    subject: str
    files: int = 0
    added: int = 0


def commits(repo: Path) -> list[Commit]:
    out = []
    for line in git(repo, "log", "--reverse", "--format=%h%x09%aI%x09%s", OS_REPO[1]).splitlines():
        sha, when, subject = line.split("\t", 2)
        c = Commit(sha, datetime.fromisoformat(when), subject)
        for stat in git(repo, "show", "--numstat", "--format=", sha).splitlines():
            a, _, _ = stat.split("\t", 2)
            c.files += 1
            c.added += int(a) if a != "-" else 0
        out.append(c)
    return out


def loc(repo: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for name in git(repo, "ls-tree", "-r", "--name-only", OS_REPO[1]).splitlines():
        if not name.endswith(SOURCE_EXT):
            continue
        n = git(repo, "show", f"{OS_REPO[1]}:{name}").count("\n")
        for prefix in [p for _, p, _ in SIZE_ROWS] + GAME_FILES:
            if name.startswith(prefix):
                counts[prefix] += n
    return dict(counts)


GAME_FILES = ["kernel/vga.c", "kernel/font.c", "kernel/game/sprites.c", "kernel/game/battle_gui.c",
              "kernel/game/game.c", "kernel/input.c"]
PROMPT_TABLE_LAST = "start the game and play for 5 minutes"
PLAY_REPORT = re.compile(r"played (\d+) game runs across a real 5-minute \((\d+)s\) session")


# Scripted oracles: (name, script regex, pass marker, fail marker). A run whose output has
# neither marker (output cut by `tail`, a timeout) is counted as failed when the tool
# call itself errored, otherwise as unknown.
ORACLES = [
    ("os", r"run_tests\.sh", r"run_tests: PASS", r"run_tests: FAIL"),
    ("serial", r"(run_serial_tests\.sh|serial_game_test\.py)", r"serial_game_test: PASS", r"FAIL|Traceback"),
    ("toolchain", r"run_toolchain_tests\.sh", r"run_toolchain_tests: PASS",
     r"run_toolchain_tests: FAIL|^FAIL t|unexpected token"),
    ("bootstrap", r"bootstrap_selfhost\.sh", r"bootstrap: PASS", r"bootstrap: FAIL"),
]


def executes(cmd: str, script: str) -> bool:
    """True if a shell command line runs `script` (not merely mentions it)."""
    if "git commit" in cmd or "cat >" in cmd or "<<" in cmd:
        return False
    for seg in re.split(r"&&|\|\||;|\n|\|", cmd):
        if re.match(r"^(timeout\s+\S+\s+)?(env\s+)?(\S+=\S+\s+)*(bash\s+|python3\s+)?(\./)?(\S*/)?" + script
                    + r"(\s|$)",
                    seg.strip()):
            return True
    return False


def oracle_runs(rows: list[dict]) -> tuple[dict[str, Counter[str]], list[tuple[str, datetime, str, str]]]:
    """Per-oracle verdict counts, and every (oracle, time, verdict, tool id) run."""
    cmds: dict[str, str] = {}
    when: dict[str, datetime] = {}
    for r in rows:
        if r.get("type") == "assistant":
            for c in r["message"].get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c["name"] == "Bash":
                    cmds[c["id"]] = c["input"]["command"]
                    when[c["id"]] = ts(r)
    results: dict[str, tuple[bool, str]] = {}
    for r in rows:
        if r.get("type") == "user" and isinstance(r["message"].get("content"), list):
            for x in r["message"]["content"]:
                if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in cmds:
                    out = x.get("content")
                    results[x["tool_use_id"]] = (bool(x.get("is_error")),
                                                 out if isinstance(out, str) else json.dumps(out))
    tally = {name: Counter() for name, *_ in ORACLES}
    log = []
    for tid, cmd in cmds.items():
        err, out = results.get(tid, (False, ""))
        for name, script, ok, ko in ORACLES:
            if not executes(cmd, script):
                continue
            if re.search(ko, out, re.M):
                verdict = "fail"
            elif re.search(ok, out):
                verdict = "pass"
            else:
                verdict = "fail" if err else "unknown"
            tally[name][verdict] += 1
            log.append((name, when[tid], verdict, tid))
    return tally, log


def image_reads(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("type") == "assistant"
               for c in r["message"].get("content") or []
               if isinstance(c, dict) and c.get("name") == "Read"
               and re.search(r"\.(png|ppm|jpg)$", c["input"].get("file_path", "")))


def play_session(rows: list[dict]) -> tuple[int, int]:
    """(runs, seconds) of the agent's serial play session, from its own report."""
    for r in rows:
        if r.get("type") != "assistant":
            continue
        for c in r["message"].get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text" and (m := PLAY_REPORT.search(c["text"])):
                return int(m.group(1)), int(m.group(2))
    raise AssertionError("play session report not found in transcript")


# ---- transcript ----------------------------------------------------------------

NOT_HUMAN = ("This session is being continued", "[Request interrupted", "<command-", "<local-command")


@dataclass
class Segment:
    events: list[datetime] = field(default_factory=list)
    assistant: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    prompts: list[tuple[datetime, str]] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)  # site page#anchor of each prompt

    def active(self) -> timedelta:
        ev = sorted(self.events)
        return sum((b - a for a, b in zip(ev, ev[1:]) if b - a <= IDLE), timedelta())


def ts(row: dict) -> datetime | None:
    t = row.get("timestamp")
    return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None


def phase_page(t: datetime, bounds: list[Commit]) -> str:
    """The phase an event belongs to: the first phase commit at or after it, else "after"."""
    return next((c.sha for c in bounds if t <= c.when), "after")


def row_anchor(row: dict) -> str:
    return "r-" + row["uuid"][:8]


def human_text(row: dict) -> str | None:
    if row.get("type") != "user" or row.get("isMeta"):
        return None
    c = row["message"].get("content")
    if isinstance(c, list):
        parts = [x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"]
        c = " ".join(parts) if parts else None
    if not c or c.startswith(NOT_HUMAN):
        return None
    return c


def answers(rows: list[dict]) -> list[tuple[datetime, str]]:
    """Human answers to AskUserQuestion, as tool_result text."""
    ask_ids = {
        c["id"]
        for r in rows if r.get("type") == "assistant"
        for c in r["message"].get("content") or []
        if isinstance(c, dict) and c.get("type") == "tool_use" and c["name"] == "AskUserQuestion"
    }
    out = []
    for r in rows:
        if r.get("type") != "user" or not isinstance(r["message"].get("content"), list):
            continue
        for x in r["message"]["content"]:
            if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in ask_ids:
                out.append((ts(r), str(x.get("content"))))
    return out


def commit_calls(rows: list[dict]) -> list[datetime]:
    out = []
    for r in rows:
        if r.get("type") != "assistant":
            continue
        for c in r["message"].get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use" and c["name"] == "Bash" \
                    and "git commit" in c["input"].get("command", ""):
                out.append(ts(r))
    return out


# ---- analysis -------------------------------------------------------------------

def analyse() -> dict:
    os_repo = fetch(*OS_REPO)
    traj = fetch(*TRAJ_REPO)
    cs = commits(os_repo)
    rows = [json.loads(line) for line in (traj / MAIN_SESSION).open() if line.strip()]

    # Instrument check: a commit was made by the agent in this session iff a `git commit`
    # tool call is within a few seconds of its author time (git truncates to the second).
    calls = commit_calls(rows)

    def in_session(c: Commit) -> bool:
        return any(timedelta(seconds=-2) <= c.when - t < timedelta(seconds=30) for t in calls)

    agent = [c for c in cs if in_session(c)]
    outside = [c for c in cs if not in_session(c)]
    assert {c.sha for c in outside} == {"17c6619", "e640d0c"}, [c.sha for c in outside]
    # e640d0c was committed by the human, but its content was written by the agent in this
    # session: an Edit to the Makefile adding the run-gui target precedes the commit.
    gui = next(c for c in outside if c.sha == "e640d0c")
    assert any(
        r.get("type") == "assistant" and ts(r) < gui.when
        and any(isinstance(c, dict) and c.get("name") == "Edit"
                and c["input"].get("file_path", "").endswith("/Makefile")
                and "run-gui" in c["input"].get("new_string", "")
                for c in r["message"].get("content") or [])
        for r in rows
    )
    assert all(c.sha in {x.sha for x in agent} for c in cs if c.sha in PHASES)

    # Segment the transcript: an event belongs to the first phase commit at or after it.
    bounds = [c for c in cs if c.sha in PHASES]
    segs: dict[str, Segment] = {c.sha: Segment() for c in bounds}
    segs["after"] = Segment()

    for r in rows:
        t = ts(r)
        if t is None:
            continue
        s = segs[phase_page(t, bounds)]
        s.events.append(t)
        if r.get("type") == "assistant":
            m = r["message"]
            s.assistant += 1
            s.output_tokens += (m.get("usage") or {}).get("output_tokens", 0)
            s.tool_calls += sum(1 for c in m.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use")
        text = human_text(r)
        if text is not None:
            s.prompts.append((t, text))
            s.anchors.append(f"{phase_page(t, bounds)}.html#{row_anchor(r)}")

    usage: Counter[str] = Counter()
    models: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    compactions = 0
    for r in rows:
        if r.get("type") == "assistant":
            models[r["message"].get("model")] += 1
            for k, v in (r["message"].get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] += v
            for c in r["message"].get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tools[c["name"]] += 1
        if r.get("type") == "user" and isinstance(r["message"].get("content"), str) \
                and r["message"]["content"].startswith(NOT_HUMAN[0]):
            compactions += 1

    # Boundary check: the segments partition the transcript.
    assert sum(s.assistant for s in segs.values()) == sum(models.values())
    assert sum(s.tool_calls for s in segs.values()) == sum(tools.values())

    approvals = [ts(r) for r in rows if r.get("type") == "user"
                 and "User has approved your plan" in json.dumps(r["message"].get("content"))]
    first_os = next(c for c in cs if c.sha == "42f0af3")
    last_os = next(c for c in cs if c.sha == "3c37ed4")
    assert approvals[0] < first_os.when

    defects = json.loads((ROOT / "data" / "defects.json").read_text())
    defects.pop("_comment")
    times = [t for s in segs.values() for t in s.events]
    ans = answers(rows)
    return {
        "commits": cs, "segs": segs, "loc": loc(os_repo), "defects": defects,
        "tdefects": [d for d in json.loads((DATA / "trajectory-defects.json").read_text())
                     if not d.get("duplicate_of")],
        "usage": usage, "models": models, "tools": tools, "compactions": compactions,
        "answers": ans, "approvals": approvals, "play": play_session(rows),
        "oracles": oracle_runs(rows)[0], "oracle_log": oracle_runs(rows)[1],
        "commit_time": {c.sha: c.when for c in cs},
        "image_reads": image_reads(rows),
        "selftest_checks": len(re.findall(r"^\s*check\(", git(os_repo, "show", f"{OS_REPO[1]}:kernel/selftest.c"),
                                          re.M)),
        "toolchain_fixtures": sum(1 for n in git(os_repo, "ls-tree", "--name-only", OS_REPO[1],
                                                "tests/toolchain/").splitlines() if n.endswith(".c")),
        "os_fixtures": sum(1 for n in git(os_repo, "ls-tree", "--name-only", OS_REPO[1],
                                         "tests/expected/").splitlines() if n.endswith(".txt")),
        "plan_to_os": last_os.when - approvals[0],
        "recommended": sum(a.count("(Recommended)") for _, a in ans),
        "questions": sum(a.count('"=') for _, a in ans),
        "start": min(times), "end": max(times),
        "prompts": [p for s in segs.values() for p in s.prompts],
        "prompt_anchors": [a for s in segs.values() for a in s.anchors],
    }


# ---- LaTeX output ---------------------------------------------------------------

def tex_escape(s: str) -> str:
    rep = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
           "#": r"\#", "^": r"\^{}", "_": r"\_", "%": r"\%", "~": r"\~{}"}
    return "".join(rep.get(ch, ch) for ch in s)


def mins(d: timedelta) -> str:
    return str(round(d.total_seconds() / 60))


def kilo(n: int) -> str:
    return f"{n / 1000:,.0f}".replace(",", "\\,")


def num(n: int) -> str:
    return f"{n:,}".replace(",", "\\,")


def site(path: str, text: object) -> str:
    """A link into the verification site (tools/site.py)."""
    return f"\\href{{\\siteurl/{path.replace('#', chr(92) + '#')}}}{{{text}}}"


# Defect table: rows group components, columns group what revealed the defect.
DEFECT_ROWS = [
    ("Boot loader and kernel", {"boot", "kernel", "fs", "shell"}),
    ("Languages (script, compiler, VM)", {"lang-script", "lang-compiler-vm"}),
    ("Game and VGA", {"game", "vga"}),
    ("Assembler (AS)", {"AS"}),
    ("C compiler (CC)", {"CC"}),
    ("Linker and C library (LD, hostlib)", {"LD", "hostlib"}),
    ("Build rules", {"build", "other"}),
]
REVEALED_COLS = [
    ("Build", {"build"}),
    ("Toolchain tests", {"toolchain-test"}),
    ("Boot oracles", {"os-test", "self-test", "cpu-exception", "serial-test", "bootstrap-cmp"}),
    ("Manual run", {"manual-qemu-run", "manual-run", "live-play"}),
    ("Screenshot", {"screenshot"}),
    ("Code reading", {"code-reading"}),
]


def defect_table(ds: list[dict]) -> str:
    head = "% Generated by tools/stats.py. Do not edit.\n"

    def row(label: str, sel: list[dict], anchor: str) -> str:
        cells = [site(f"defects.html{anchor}", len(sel)), str(sum(1 for d in sel if d.get("in_commit_message")))]
        cells += [str(sum(1 for d in sel if d["revealed_by"] in vals) or "") for _, vals in REVEALED_COLS]
        return f"{label} & " + " & ".join(cells) + " \\\\"

    product = [d for d in ds if d["kind"] == "product"]
    rows = [row(label, [d for d in product if d["component"] in comps], f"#row-{n}")
            for n, (label, comps) in enumerate(DEFECT_ROWS)]
    assert sum(1 for d in product for _, c in DEFECT_ROWS if d["component"] in c) == len(product)
    rows += ["\\hline", row("\\textbf{Product total}", product, ""), "\\hline",
             row("Oracle tooling", [d for d in ds if d["kind"] == "oracle"], "#oracle"),
             row("Environment", [d for d in ds if d["kind"] == "environment"], "#environment")]
    cols = " & ".join(c for c, _ in REVEALED_COLS)
    return (head + "\\begin{table}[h]\n\\centering\n\\begin{tabular}{lrrrrrrrr}\n"
            f"Component & Defects & In commit msg. & {cols} \\\\\n\\hline\n" + "\n".join(rows) +
            "\n\\end{tabular}\n\\caption{Defects recovered from the full transcript, by component and by "
            "what revealed them. \\emph{In commit msg.} counts those that a commit message also reports. "
            "\\emph{Boot oracles} are the OS system test, the boot self-test, the CPU exception handlers "
            "and the bootstrap comparison.}\n\\label{tab:defects}\n\\end{table}\n")


def defect_macros(ds: list[dict]) -> dict[str, object]:
    kinds = Counter(d["kind"] for d in ds)
    product = [d for d in ds if d["kind"] == "product"]
    attempts = sorted(int(d.get("failed_attempts") or 0) for d in product)
    by = Counter(d["revealed_by"] for d in product)
    boot = sum(by[v] for v in REVEALED_COLS[2][1])
    silent = [d for d in product if d["symptom"] in ("wrong-output", "data-corruption", "hang", "visual")]
    return {
        "ntdefects": len(ds),
        "ntproduct": kinds["product"],
        "ntoracle": kinds["oracle"],
        "ntenv": kinds["environment"],
        "ntreported": sum(1 for d in product if d.get("in_commit_message")),
        "ntunreported": sum(1 for d in product if not d.get("in_commit_message")),
        "ntcc": sum(1 for d in product if d["component"] == "CC"),
        "ntas": sum(1 for d in product if d["component"] == "AS"),
        "nttoolchain": sum(1 for d in product if d["component"] in ("AS", "CC", "LD", "hostlib")),
        "ntbuildrevealed": by["build"],
        "ntbootrevealed": boot,
        "ntcodereading": by["code-reading"],
        "ntsilent": len(silent),
        "ntmaxattempts": attempts[-1],
        "ntmultiattempt": sum(1 for a in attempts if a > 1),
    }


def write(st: dict) -> None:
    (GEN / "tab-defects.tex").write_text(defect_table(st["tdefects"]))
    GEN.mkdir(parents=True, exist_ok=True)
    by_sha = {c.sha: c for c in st["commits"]}
    segs: dict[str, Segment] = st["segs"]
    u = st["usage"]
    head = "% Generated by tools/stats.py. Do not edit.\n"

    # Fold the minor commits' size into the next listed phase.
    files: Counter[str] = Counter()
    added: Counter[str] = Counter()
    pending_f = pending_a = 0
    for c in st["commits"]:
        if c.sha == "17c6619":
            continue
        pending_f += c.files
        pending_a += c.added
        if c.sha in PHASES:
            files[c.sha], added[c.sha] = pending_f, pending_a
            pending_f = pending_a = 0

    rows = []
    for sha, label in PHASES.items():
        c, s = by_sha[sha], segs[sha]
        when = f"\\href{{https://github.com/{OS_REPO[0]}/commit/{sha}}}{{{c.when.astimezone(CEST).strftime('%b %d, %H:%M')}}}"
        rows.append(f"{when} & {site(f't/{sha}.html', label)} & {files[sha]} & {added[sha]} & {len(s.prompts)} & "
                    f"{mins(s.active())} & {kilo(s.output_tokens)} & {s.tool_calls} \\\\")
        if sha in EPISODE_BREAKS:
            rows.append("\\hline")
    a = segs["after"]
    rows.append("\\hline")
    rows.append(f"after last commit & {site('t/after.html', 'screencasts, hosting')} & ---& --- & {len(a.prompts)} & "
                f"{mins(a.active())} & {kilo(a.output_tokens)} & {a.tool_calls} \\\\")
    (GEN / "tab-timeline.tex").write_text(head + "\\begin{table}[h]\n\\centering\n"
        "\\begin{tabular}{llrrrrrr}\n"
        "Commit (CEST) & Phase & Files & Lines added & Prompts & Active min & Output ktok & Tool calls \\\\\n"
        "\\hline\n" + "\n".join(rows) + "\n\\end{tabular}\n"
        "\\caption{Phases of \\texttt{boot-to-claude}. \\emph{Prompts} counts human messages, "
        "\\emph{Active min} sums the gaps between transcript events shorter than "
        f"{mins(IDLE)} minutes, \\emph{{Output ktok}} is thousands of tokens generated. "
        "Work between two phase commits is attributed to the later one; four minor commits "
        "are folded into the next phase.}\n\\label{tab:timeline}\n\\end{table}\n")

    size_rows = []
    for label, prefix, indent in SIZE_ROWS:
        size_rows.append(f"{'\\quad ' if indent else ''}{label} & {st['loc'].get(prefix, 0)} \\\\")
    (GEN / "tab-size.tex").write_text(head + "\\begin{table}[h]\n\\centering\n\\begin{tabular}{lr}\n"
        "Component & Lines \\\\\n\\hline\n" + "\n".join(size_rows) + "\n\\end{tabular}\n"
        f"\\caption{{Size of \\texttt{{boot-to-claude}} at commit \\texttt{{{OS_REPO[1][:7]}}}, "
        "in physical lines including comments.}\n\\label{tab:size}\n\\end{table}\n")

    # The table stops at the request to play the game; later prompts are screencast
    # errands (recording, converting, hosting) and the graphical screen, quoted in the text.
    shown = st["prompts"]
    last = next(i for i, (_, p) in enumerate(shown) if PROMPT_TABLE_LAST in p)
    shown, omitted = shown[:last + 1], len(shown) - last - 1
    prow = []
    for (t, text), anchor in zip(shown, st["prompt_anchors"]):
        one = " ".join(text.split())
        if len(one) > 300:
            one = one[:297] + "..."
        prow.append(f"{site('t/' + anchor, t.astimezone(CEST).strftime('%b %d, %H:%M'))} & {tex_escape(one)} \\\\")
    (GEN / "tab-prompts.tex").write_text(head + "\\begin{table}[h]\n\\begin{tabular}{lp{11cm}}\n"
        "Time (CEST) & Prompt \\\\\n\\hline\n" + "\n".join(prow) + "\n\\end{tabular}\n"
        f"\\caption{{The human prompts of the session, verbatim, up to the request to play the "
        f"game. The {omitted} later prompts are about recording and hosting screencasts and "
        "the graphical battle screen.}\n"
        "\\label{tab:prompts}\n\\end{table}\n")

    o = st["oracles"]

    def runs(name: str) -> str:
        c = o[name]
        return f"{site(f'oracles.html#{name}', sum(c.values()))} & {c['fail']} & {c['unknown']}"

    orows = [
        ("CPU exception handlers", "any CPU fault, e.g.\\ an invalid opcode from a mis-encoded instruction",
         "\\texttt{EXCEPTION vector=$n$}, then halt", "\\multicolumn{3}{c}{in every boot}"),
        ("Boot self-test", f"{st['selftest_checks']} checks: string functions, allocator with coalescing, "
         "tinyfs create/write/read/delete", "\\texttt{SELFTEST PASS} or \\texttt{FAIL}",
         "\\multicolumn{3}{c}{in every boot}"),
        ("OS system test", "boots headless QEMU, types shell commands through the QEMU monitor, compares "
         f"serial output with {st['os_fixtures']} expected outputs; both languages and the game menu",
         "\\texttt{run\\_tests: PASS}", runs("os")),
        ("Serial game test", "plays a full battle over the serial port alone, then checks the shell answers",
         "\\texttt{serial\\_game\\_test: PASS}", runs("serial")),
        ("Toolchain tests", f"{st['toolchain_fixtures']} C programs compiled by CC, AS and LD, run on the "
         "host; exit code compared with the expected value", "\\texttt{run\\_toolchain\\_tests: PASS}",
         runs("toolchain")),
        ("Bootstrap fixed point", "Stage~2 and Stage~3 of AS, CC and LD are byte-identical",
         "\\texttt{bootstrap: PASS}", runs("bootstrap")),
        ("Reference tools", "while \\texttt{nasm}, \\texttt{gcc}, \\texttt{ld} and \\texttt{objcopy} are "
         "still in the build: a change must pass the tests with the old tools first; LD's flat output must "
         "equal \\texttt{objcopy}'s byte for byte", "same as the tests above", "\\multicolumn{3}{c}{---}"),
        ("Boot budget", "the kernel image fits the sectors the boot loader reads", "build error",
         "\\multicolumn{3}{c}{in every build}"),
        ("Screenshots", "the graphical battle screen, from QEMU frame-buffer dumps",
         "the agent looks at the image", f"\\multicolumn{{3}}{{c}}{{{st['image_reads']} images read}}"),
    ]
    (GEN / "tab-oracles.tex").write_text(head + "\\begin{table}[h]\n\\begin{tabular}{p{2.6cm}p{6cm}p{3cm}rrr}\n"
        "Oracle & What it checks & Verdict & Runs & Failed & No verdict \\\\\n\\hline\n"
        + "\n".join(" & ".join(r) + " \\\\" for r in orows) + "\n\\end{tabular}\n"
        "\\caption{The oracles of \\sysname{}. \\emph{Runs} counts the times the agent executed the "
        "oracle during the session, each run linked to the list of runs on the verification site; "
        "\\emph{No verdict} means the output carried neither verdict, typically a hang killed by a "
        "timeout, a crash of the tool under test, or a build error before the test started.}\n"
        "\\label{tab:oracles}\n\\end{table}\n")

    t7_start, t7_end = st["commit_time"]["0f82a8d"], st["commit_time"]["1d62d14"]
    total_active = sum((s.active() for s in segs.values()), timedelta())
    os_active = sum((segs[s].active() for s in list(PHASES)[:8]), timedelta())
    tc_active = sum((segs[s].active() for s in list(PHASES)[8:16]), timedelta())
    model = st["models"].most_common(1)[0][0]
    macros = {
        "siteurl": SITE,
        "nprompts": len(st["prompts"]),
        "nshortprompts": sum(1 for _, p in st["prompts"] if len(p.split()) <= 3),
        "napprovals": len(st["approvals"]),
        "plantoosmin": mins(st["plan_to_os"]),
        "nquestions": st["questions"],
        "nrecommended": st["recommended"],
        "ncompactions": st["compactions"],
        "nassistant": num(sum(st["models"].values())),
        "ntoolcalls": num(sum(st["tools"].values())),
        "nbash": num(st["tools"]["Bash"]),
        "nedit": num(st["tools"]["Edit"] + st["tools"]["Write"]),
        "outputMtok": f"{u['output_tokens'] / 1e6:.2f}",
        "cachereadGtok": f"{u['cache_read_input_tokens'] / 1e9:.2f}",
        "cachewriteMtok": f"{u['cache_creation_input_tokens'] / 1e6:.1f}",
        "activehours": f"{total_active.total_seconds() / 3600:.1f}",
        "osactivemin": mins(os_active),
        "tcactivehours": f"{tc_active.total_seconds() / 3600:.1f}",
        "ndefects": sum(st["defects"].values()),
        "ncommits": len(st["commits"]),
        "sessionmodel": f"\\texttt{{{model}}}",
        "ospin": f"\\texttt{{{OS_REPO[1][:7]}}}",
        "gamerules": st["loc"]["kernel/game/game.c"],
        "gamegui": st["loc"]["kernel/game/battle_gui.c"],
        "gamesprites": st["loc"]["kernel/game/sprites.c"],
        "vgadriver": st["loc"]["kernel/vga.c"],
        "fontlines": st["loc"]["kernel/font.c"],
        "inputlines": st["loc"]["kernel/input.c"],
        "noraclesruns": sum(sum(c.values()) for c in o.values()),
        "noraclesfailed": sum(c["fail"] for c in o.values()),
        "nostestruns": sum(o["os"].values()),
        "nostestfailed": o["os"]["fail"],
        "ntoolchaintestruns": sum(o["toolchain"].values()),
        "ntoolchainfixtures": st["toolchain_fixtures"],
        "nselftestchecks": st["selftest_checks"],
        "nimagereads": st["image_reads"],
        **defect_macros(st["tdefects"]),
        "ntsevenfailed": sum(1 for _, t, v, _ in st["oracle_log"] if v == "fail" and t7_start < t <= t7_end),
        # Defects whose first sighting, or whose fix confirmation, is a scripted oracle run.
        "ntfirstinrun": sum(1 for d in st["tdefects"] if d.get("first_seen") in {x[3] for x in st["oracle_log"]}),
        "ntconfirmedinrun": sum(1 for d in st["tdefects"]
                                if d.get("confirmed_by") in {x[3] for x in st["oracle_log"]}),
        "playruns": st["play"][0],
        "playseconds": st["play"][1],
    }
    (GEN / "stats.tex").write_text(head + "".join(
        f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in macros.items()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print numbers as JSON instead of writing paper/gen/")
    a = ap.parse_args()
    st = analyse()
    if a.json:
        segs = {k: {"prompts": len(s.prompts), "active_min": mins(s.active()), "output_tokens": s.output_tokens,
                    "tool_calls": s.tool_calls} for k, s in st["segs"].items()}
        print(json.dumps({"segments": segs, "usage": st["usage"], "tools": st["tools"],
                          "prompts": [(t.isoformat(), p[:120]) for t, p in st["prompts"]],
                          "questions": st["questions"], "recommended": st["recommended"]}, indent=1))
    else:
        write(st)


if __name__ == "__main__":
    sys.exit(main())
