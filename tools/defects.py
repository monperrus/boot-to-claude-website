"""Validate and merge the per-chunk defect annotations into data/trajectory-defects.json.

Input: data/defects/C*.json, written by annotators from data/chunks/C*.txt (see
data/chunks/INSTRUCTIONS.md). Each record must cite tool ids that exist in the
transcript and an evidence quote that appears verbatim in its chunk.

Usage:
  defects.py           validate, merge, print a summary
  defects.py --strict  also fail on any unverifiable record
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import DATA, MAIN_SESSION, TRAJ_REPO, fetch  # noqa: E402

KINDS = {"product", "oracle", "environment"}
COMPONENTS = {"boot", "kernel", "fs", "shell", "lang-script", "lang-compiler-vm", "game", "vga", "AS", "CC",
              "LD", "hostlib", "build", "test-harness", "capture", "qemu", "other"}
SYMPTOMS = {"build-error", "crash-or-exception", "hang", "wrong-output", "byte-mismatch", "data-corruption",
            "visual", "other"}
OUT = DATA / "trajectory-defects.json"


def norm(s: str) -> str:
    """Whitespace, dashes and markdown emphasis vary between quote and source."""
    s = re.sub(r"[—–−]", "-", s).replace("*", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip()


def tool_ids() -> set[str]:
    traj = fetch(*TRAJ_REPO)
    return set(re.findall(r'"id": ?"(toolu_[A-Za-z0-9]+)"', (traj / MAIN_SESSION).read_text()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    ids = tool_ids()
    corrections = json.loads((DATA / "defects-corrections.json").read_text())
    corrections.pop("_comment")
    merged, problems = [], []
    for f in sorted((DATA / "defects").glob("C*.json")):
        chunk_text = norm((DATA / "chunks" / f"{f.stem}.txt").read_text())
        for n, d in enumerate(json.loads(f.read_text()), 1):
            key = f"{f.stem}#{n}"
            d["id"] = f"D-{f.stem}-{n:02d}"
            fix = corrections.pop(d["id"], {})
            assert fix.get("_reason") or not fix, d["id"]
            d.update({k: v for k, v in fix.items() if k != "_reason"})
            for field, allowed in (("kind", KINDS), ("component", COMPONENTS), ("symptom", SYMPTOMS)):
                if d.get(field) not in allowed:
                    problems.append(f"{key}: {field}={d.get(field)!r}")
            cited = [d.get("first_seen"), d.get("confirmed_by"), *(d.get("fixed_by") or [])]
            bad = [t for t in cited if t and t not in ids]
            if bad:
                problems.append(f"{key}: unknown tool ids {bad}")
            # Annotators join quoted fragments with "..."; each fragment must appear.
            ev = norm(d.get("evidence") or "")
            frags = [norm(p).strip("\"'`. ") for p in re.split(r"\.\.\.|…", ev)]
            missing = [p for p in frags if len(p) >= 12 and norm(p)[:60] not in chunk_text]
            if missing:
                problems.append(f"{key}: evidence not found: {missing[0][:60]!r}")
            merged.append(d)
    assert not corrections, f"corrections for unknown records: {list(corrections)}"
    OUT.write_text(json.dumps(merged, indent=1) + "\n")
    print(f"defects: {len(merged)}  by kind: {dict(Counter(d['kind'] for d in merged))}")
    print(f"commit-linked: {sum(1 for d in merged if d.get('in_commit_message'))}")
    print(f"problems: {len(problems)}")
    for p in problems:
        print("  " + p)
    if a.strict and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
