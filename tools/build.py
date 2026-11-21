"""Compile paper/main.tex to build/paper.html, a self-contained page (images inlined).

Usage:
  build.py    build; silent on success
"""
from __future__ import annotations

import argparse
import base64
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


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(inline_images(pandoc(PAPER / "main.tex")))


if __name__ == "__main__":
    main()
