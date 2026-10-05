"""Extract failure episodes from the session transcript, for defect annotation.

A failure signal is a tool result that shows something went wrong: an oracle FAIL or
no-verdict run, a build error, a CPU exception, a crash, a byte mismatch, a traceback.
A green event is an oracle PASS or a git commit. An episode is the maximal stretch of
the transcript from a failure signal to the next green event; it may contain several
defects, which the annotation separates.

Usage:
  episodes.py          write data/episodes/index.json and one excerpt per episode
  episodes.py --json   print the episode index
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import DATA, MAIN_SESSION, ORACLES, TRAJ_REPO, executes, fetch, ts  # noqa: E402

OUT = DATA / "episodes"

SIGNALS = [
    ("cpu-exception", r"EXCEPTION vector="),
    ("selftest-fail", r"SELFTEST FAIL"),
    ("crash", r"Segmentation fault|core dumped|Aborted"),
    ("boot-timeout", r"timed out waiting for"),
    ("byte-mismatch", r"differ: (byte|char)"),
    ("traceback", r"Traceback \(most recent call last\)"),
    ("build-error", r"(^|\s)error:|Error \d|unexpected token|too many \w+|undefined reference|cannot find"),
]
BUILD_CMD = re.compile(r"\bmake\b|toolchain/(cc|as|ld)|build/toolchain|\bgcc\b|\bnasm\b|\bld\b")


@dataclass
class Event:
    i: int                 # index of the tool_use row in the transcript
    t: datetime
    tool_id: str
    command: str
    output: str
    signals: list[str] = field(default_factory=list)
    green: str | None = None


@dataclass
class Episode:
    id: str
    start: str
    end: str
    first_row: int
    last_row: int
    signals: dict[str, int]
    closed_by: str
    tool_ids: list[str]
    files_edited: list[str]


def load() -> list[dict]:
    traj = fetch(*TRAJ_REPO)
    return [json.loads(line) for line in (traj / MAIN_SESSION).open() if line.strip()]


def bash_events(rows: list[dict]) -> list[Event]:
    calls: dict[str, tuple[int, datetime, str]] = {}
    for i, r in enumerate(rows):
        if r.get("type") == "assistant":
            for c in r["message"].get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c["name"] == "Bash":
                    calls[c["id"]] = (i, ts(r), c["input"]["command"])
    events = []
    for r in rows:
        if r.get("type") != "user" or not isinstance(r["message"].get("content"), list):
            continue
        for x in r["message"]["content"]:
            if not (isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in calls):
                continue
            i, t, cmd = calls[x["tool_use_id"]]
            out = x.get("content")
            out = out if isinstance(out, str) else json.dumps(out)
            ev = Event(i, t, x["tool_use_id"], cmd, out)
            classify(ev, bool(x.get("is_error")))
            events.append(ev)
    return sorted(events, key=lambda e: e.i)


def classify(ev: Event, err: bool) -> None:
    if "git commit" in ev.command and re.search(r"^\[\w+ [0-9a-f]{7}\]", ev.output, re.M):
        ev.green = "commit"
        return
    for name, script, ok, ko in ORACLES:
        if executes(ev.command, script):
            if re.search(ko, ev.output, re.M):
                ev.signals.append(f"oracle-fail:{name}")
            elif re.search(ok, ev.output):
                ev.green = f"oracle-pass:{name}"
            else:
                ev.signals.append(f"oracle-noverdict:{name}")
    if ev.green:
        return
    for name, pat in SIGNALS:
        if name == "build-error" and not BUILD_CMD.search(ev.command):
            continue
        if re.search(pat, ev.output, re.M):
            ev.signals.append(name)


def episodes(rows: list[dict], events: list[Event]) -> list[Episode]:
    eps: list[Episode] = []
    cur: list[Event] = []

    def close(by: Event | None) -> None:
        if not cur:
            return
        first, last_row = cur[0], (by.i if by else len(rows) - 1)
        sig: dict[str, int] = {}
        for e in cur:
            for s in e.signals:
                sig[s] = sig.get(s, 0) + 1
        files = sorted({c["input"].get("file_path", "")
                        for r in rows[first.i:last_row + 1] if r.get("type") == "assistant"
                        for c in r["message"].get("content") or []
                        if isinstance(c, dict) and c.get("name") in ("Edit", "Write")})
        eps.append(Episode(
            id=f"E{len(eps) + 1:03d}", start=first.t.isoformat(),
            end=(by.t if by else cur[-1].t).isoformat(), first_row=first.i, last_row=last_row,
            signals=sig, closed_by=by.green if by else "end of session",
            tool_ids=[e.tool_id for e in cur], files_edited=files))
        cur.clear()

    for e in events:
        if e.green:
            close(e)
        elif e.signals:
            cur.append(e)
    close(None)
    return eps


def excerpt(rows: list[dict], ep: Episode, budget: int = 40000) -> str:
    """Readable trace of an episode: agent prose, commands, failing outputs, edits."""
    lines = [f"# {ep.id}  {ep.start} -> {ep.end}  closed by: {ep.closed_by}",
             f"signals: {ep.signals}", ""]
    failing = set(ep.tool_ids)
    results: dict[str, str] = {}
    for r in rows[ep.first_row:ep.last_row + 2]:
        if r.get("type") == "user" and isinstance(r["message"].get("content"), list):
            for x in r["message"]["content"]:
                if isinstance(x, dict) and x.get("type") == "tool_result":
                    out = x.get("content")
                    results[x.get("tool_use_id")] = out if isinstance(out, str) else json.dumps(out)
    for r in rows[ep.first_row:ep.last_row + 1]:
        if r.get("type") == "user":
            c = r["message"].get("content")
            if isinstance(c, str) and not c.startswith("<"):
                lines.append(f"[HUMAN] {c[:600]}")
            continue
        if r.get("type") != "assistant":
            continue
        for c in r["message"].get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and c["text"].strip():
                lines.append(f"[AGENT] {c['text'].strip()[:1500]}")
            elif c.get("type") == "tool_use":
                tid, name, inp = c["id"], c["name"], c["input"]
                if name == "Bash":
                    lines.append(f"[BASH {tid}] {inp['command'][:400]}")
                    out = results.get(tid, "")
                    keep = 1500 if tid in failing else 300
                    lines.append(f"[OUT{' FAIL' if tid in failing else ''}] ...{out[-keep:]}")
                elif name in ("Edit", "Write"):
                    body = inp.get("new_string") or inp.get("content") or ""
                    old = inp.get("old_string", "")
                    lines.append(f"[{name.upper()} {tid}] {inp.get('file_path', '')}\n  - {old[:250]}\n  + {body[:350]}")
                elif name == "Read":
                    lines.append(f"[READ {tid}] {inp.get('file_path', '')}")
    text = "\n".join(lines)
    if len(text) > budget:
        text = text[:budget // 2] + "\n\n[... middle of episode elided ...]\n\n" + text[-budget // 2:]
    return text


# Annotation chunks: groups of consecutive phases, about 100k characters each. A phase
# larger than that (T5, T7, T8) is split at row boundaries.
CHUNK_PHASES = [
    ["42f0af3", "0b4c5c1", "1a67c58", "a3b80d1", "594867b", "f4702ba", "3c37ed4", "24ac0a4"],
    ["cd6231d"],
    ["72955af", "f08a1b9"],
    ["1647d6f"],
    ["0f82a8d", "1d62d14/1"],
    ["1d62d14/2"],
    ["acff0a5/1"],
    ["acff0a5/2", "900c4cf"],
    ["4246ba7", "377d751"],
    ["a4a64cb", "after"],
]


def write_chunks(rows: list[dict]) -> None:
    from stats import OS_REPO, PHASES, commits, git
    os_repo = fetch(*OS_REPO)
    cs = [c for c in commits(os_repo) if c.sha in PHASES]
    times = [ts(r) for r in rows]

    def row_after(t: datetime) -> int:
        return next((i for i, x in enumerate(times) if x and x > t), len(rows))

    seg: dict[str, tuple[int, int]] = {}
    prev = 0
    for c in cs:
        end = row_after(c.when)
        seg[c.sha] = (prev, end - 1)
        prev = end
    seg["after"] = (prev, len(rows) - 1)
    for sha in ("1d62d14", "acff0a5"):
        a, b = seg.pop(sha)
        mid = (a + b) // 2
        seg[f"{sha}/1"], seg[f"{sha}/2"] = (a, mid), (mid + 1, b)

    chunks = []
    cdir = DATA / "chunks"
    cdir.mkdir(parents=True, exist_ok=True)
    for n, parts in enumerate(CHUNK_PHASES, 1):
        text, msgs = [], []
        for p in parts:
            a, b = seg[p]
            ep = Episode(p, "", "", a, b, {}, "", [], [])
            text.append(f"\n\n======== PHASE SEGMENT {p} (rows {a}-{b}) ========\n")
            text.append(excerpt(rows, ep, budget=10**9))
            sha = p.split("/")[0]
            if sha != "after":
                msgs.append(git(os_repo, "show", "-s", "--format=commit %h%n%B", sha))
        cid = f"C{n:02d}"
        (cdir / f"{cid}.txt").write_text("".join(text))
        (cdir / f"{cid}.commits.txt").write_text("\n".join(msgs))
        chunks.append({"id": cid, "parts": parts, "chars": sum(len(t) for t in text)})
    (cdir / "chunks.json").write_text(json.dumps(chunks, indent=1) + "\n")
    for c in chunks:
        print(c["id"], c["parts"], c["chars"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print the episode index")
    ap.add_argument("--chunks", action="store_true", help="write annotation chunks to data/chunks/")
    a = ap.parse_args()
    rows = load()
    if a.chunks:
        write_chunks(rows)
        return
    evs = bash_events(rows)
    eps = episodes(rows, evs)

    # Instrument checks. Known negative: phase 1 (0b4c5c1, 19:04-19:07 CEST) had no failure.
    assert not [e for e in eps if "2026-07-17T17:04:38" <= e.start <= "2026-07-17T17:07:22"], "phase 1"
    # Known positive: the phase 5 int+int bug and the INT13h boot regression fall in an episode
    # before the phase 5 commit (17:30:51Z).
    assert [e for e in eps if "2026-07-17T17:20:41" <= e.start <= "2026-07-17T17:30:51"], "phase 5"

    index = [asdict(e) for e in eps]
    if a.json:
        print(json.dumps(index, indent=1))
        return
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    for e in eps:
        (OUT / f"{e.id}.txt").write_text(excerpt(rows, e))
    print(f"episodes: {len(eps)}, signals: {sum(len(e.signals) for e in evs)}")


if __name__ == "__main__":
    main()
