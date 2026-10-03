"""Compile paper/main.tex to build/paper.html, the page published as the claude.ai artifact.

Usage:
  build.py                   build, print the sha256 of the page
  build.py --check           build, exit 1 if artifact.json's sha256 differs (artifact stale)
  build.py --mark-published  build, record its sha256 (and --url) in artifact.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import subprocess
import sys
from pathlib import Path

import pypandoc

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "paper"
TOOLS = ROOT / "tools"
OUT = ROOT / "build" / "paper.html"
STATE = ROOT / "artifact.json"


def pandoc(src: Path) -> str:
    args = [
        pypandoc.get_pandoc_path(), src.name,
        "--from=latex", "--to=html5", "--standalone",
        f"--template={TOOLS / 'template.html'}",
        f"--lua-filter={TOOLS / 'filters.lua'}",
        "--mathml", "--number-sections", "--wrap=none",
        "--metadata=reference-section-title:References",
    ]
    bib = PAPER / "refs.bib"
    if bib.exists():
        args += ["--citeproc", f"--bibliography={bib}"]
    res = subprocess.run(args, cwd=PAPER, capture_output=True, text=True)
    if res.stderr:
        sys.stderr.write(res.stderr)
    if res.returncode:
        sys.exit(res.returncode)
    return res.stdout


def inline_images(html: str) -> str:
    def repl(m: re.Match[str]) -> str:
        src = m.group(2)
        if src.startswith(("data:", "http:", "https:")):
            return m.group(0)
        path = PAPER / src
        if not path.exists():
            sys.exit(f"missing image: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = base64.b64encode(path.read_bytes()).decode()
        return f'{m.group(1)}data:{mime};base64,{data}"'

    return re.sub(r'(<img[^>]*?\ssrc=")([^"]+)"', repl, html)


def build() -> str:
    html = inline_images(pandoc(PAPER / "main.tex"))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html)
    return hashlib.sha256(html.encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the published artifact is stale")
    ap.add_argument("--mark-published", action="store_true", help="record the current build as published")
    ap.add_argument("--url", help="artifact URL to record with --mark-published")
    a = ap.parse_args()

    sha = build()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}

    if a.mark_published:
        if a.url:
            state["url"] = a.url
        if not state.get("url"):
            sys.exit("no artifact url: pass --url")
        state["sha256"] = sha
        STATE.write_text(json.dumps(state, indent=2) + "\n")
    elif a.check:
        if state.get("sha256") != sha:
            sys.exit(
                f"artifact stale: build/paper.html sha256 {sha[:12]} != published "
                f"{str(state.get('sha256'))[:12]}. Publish it to {state.get('url')}, "
                "then run tools/build.py --mark-published"
            )
    else:
        print(sha)


if __name__ == "__main__":
    main()
