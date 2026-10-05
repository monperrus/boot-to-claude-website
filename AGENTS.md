# AGENTS.md

## Goal

Write a LaTeX paper whose source of truth is `paper/main.tex`, compiled to a
claude.ai artifact (an HTML page) that **always stays in sync** with the source.
A commit where the published artifact differs from what the source builds to is a bug.

## Paper

Topic: Claude writes an operating system, named **QuineOS** in the paper (`\sysname` in
`main.tex`). Data sources, pinned in `tools/stats.py`:
- https://github.com/monperrus/boot-to-claude: the OS and its git history (public).
- https://github.com/monperrus/boot-to-claude-paper: the Claude Code transcripts (private for now, to be made public with the paper).

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

Verification site: `tools/site.py` renders the transcript, the defects and the oracle
runs into `build/site/`, published by `.github/workflows/pages.yml` at the URL in
`SITE` (`stats.py`), from the repo `monperrus/boot-to-claude-website`. Every tool call
is anchored by its tool id. The paper links into it via `\siteurl` (generated tables)
and `\defect{D-..}{text}` (prose). `tools/site.py --check` must stay silent: it fails
on any link from the paper or within the site to a missing page or anchor.

## Layout

- `paper/main.tex`, `paper/refs.bib`: the paper. Edit only these (and figures in `paper/`).
- `tools/template.html`: pandoc template for the page (title, styles, light/dark tokens).
- `tools/filters.lua`: pandoc filter that numbers equations and resolves `\eqref`.
- `tools/build.py`: LaTeX → `build/paper.html` (gitignored), images inlined as data URIs (the artifact CSP blocks other image sources). Deterministic output.
- `artifact.json`: the published artifact `url` and the `sha256` of the HTML last published there. It is committed.
- `.githooks/pre-commit` runs `tools/build.py --check` and refuses the commit when the artifact is stale.

## Workflow (every change to the paper)

1. Edit `paper/*.tex|bib`.
2. `.venv/bin/python tools/build.py` writes `build/paper.html` and prints its sha256.
   If the change adds links into the site: `tools/site.py && tools/site.py --check`.
3. Publish `build/paper.html` with the Artifact tool, passing `url` from `artifact.json`.
   Never create a second artifact.
4. `.venv/bin/python tools/build.py --mark-published` records the new hash in `artifact.json`.
5. Commit. The pre-commit hook checks that the hash recorded in `artifact.json` matches a fresh build.

Only the agent can publish: the Artifact tool is not a CLI. So step 3 cannot be
automated in a hook. The hook makes a forgotten step 3 fail the commit.

## Setup

```sh
uv venv .venv && uv pip install --python .venv/bin/python pypandoc_binary
git config core.hooksPath .githooks
```

`pypandoc_binary` bundles pandoc. No TeX distribution is needed for the HTML artifact.

## LaTeX subset

The source goes through pandoc's LaTeX reader, so keep to what it supports:
`\title \author \and \maketitle`, `abstract`, sections, `\label/\ref`, `equation`
(numbered) / `equation*` / `\[ \]`, `\eqref`, `\cite` + `refs.bib`, `itemize/enumerate`,
`figure` + `\includegraphics` + `\caption`, `tabular`, `\emph \textbf \texttt \url \href`.
Custom macros defined with `\newcommand` in the preamble are expanded by pandoc.
Check the rendered page after you use anything outside this list.
