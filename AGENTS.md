# AGENTS.md

## Goal

Write a LaTeX paper whose source of truth is `paper/main.tex`, compiled to a
self-contained HTML page (`build/paper.html`) for reading.

## Paper

Topic: Claude writes an operating system, named **QuineOS** in the paper (`\sysname` in
`main.tex`). Data sources, pinned in `tools/stats.py`:
- https://github.com/monperrus/boot-to-claude: the OS and its git history (public).
- https://github.com/monperrus/boot-to-claude-paper: the Claude Code transcripts (public).

Every number in the paper comes from `paper/gen/*.tex`, written by
`.venv/bin/python tools/stats.py` (clones both repos into `data/`, needs `gh` logged in).
Never type a number into `main.tex` by hand; add a macro to `stats.py` instead.
`paper/gen/` is committed so the build does not need the data. The manual
annotations are `data/defects/C*.json` (defects per transcript chunk, by subagents),
merged and validated by `tools/defects.py` into `data/trajectory-defects.json`, with hand
fixes in `data/defects-corrections.json`. `data/defects.json` (defects per commit
message) is the older count, no longer used by the paper.
`stats.py` asserts its own instrument: the transcript's `git commit` calls must match
every agent commit, and nothing else.

Verification site: `tools/site.py` renders the Tiny Monsters screencasts (default page,
`index.html`), the transcript by phase (`phases.html`), the defects and the oracle runs
into `build/site/`, published by `.github/workflows/pages.yml` at the URL in
`SITE` (`stats.py`), from the repo `monperrus/boot-to-claude-website`. Every tool call
is anchored by its tool id. The paper links into it via `\siteurl` (generated tables)
and `\defect{D-..}{text}` (prose). `tools/site.py --check` must stay silent: it fails
on any link from the paper or within the site to a missing page or anchor.

## Layout

- `paper/main.tex`, `paper/refs.bib`: the paper. Edit only these (and figures in `paper/`).
- `paper/neurips_2025.sty`: the official NeurIPS 2025 style, used in `preprint` mode (no anonymization, no line numbers), with numeric natbib citations and `abbrvnat`.
- `tools/template.html`: pandoc template for the page (title, styles, light/dark tokens).
- `tools/filters.lua`: pandoc filter that numbers equations, tables and figures (caption prefixes "Table N."), resolves `\ref`/`\eqref`, and drops the `adjustbox` wrapper around tables (PDF-only scaling).
- `tools/build.py`: LaTeX → `build/paper.html` (gitignored), images inlined as data URIs so the page is one file. Deterministic output.

## Workflow (every change to the paper)

1. Edit `paper/*.tex|bib`.
2. `.venv/bin/python tools/build.py` writes `build/paper.html`; check the rendered page.
   If the change adds links into the site: `tools/site.py && tools/site.py --check`.
3. Commit.

## Setup

```sh
uv venv .venv && uv pip install --python .venv/bin/python pypandoc_binary
```

`pypandoc_binary` bundles pandoc. No TeX distribution is needed for the HTML page.

## LaTeX subset

The source goes through pandoc's LaTeX reader, so keep to what it supports:
`\title \author \and \maketitle`, `abstract`, sections, `\label/\ref`, `equation`
(numbered) / `equation*` / `\[ \]`, `\eqref`, `\cite` + `refs.bib`, `itemize/enumerate`,
`figure` + `\includegraphics` + `\caption`, `tabular`, `\emph \textbf \texttt \url \href`.
Tables follow the NeurIPS style: caption above, booktabs rules (`\toprule \midrule \bottomrule`),
tabular inside `\begin{adjustbox}{max width=\linewidth}`; `stats.py` writes generated tables so.
Custom macros defined with `\newcommand` in the preamble are expanded by pandoc.
Check the rendered page after you use anything outside this list.
