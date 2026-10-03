# AGENTS.md

## Goal

Write a LaTeX paper whose source of truth is `paper/main.tex`, compiled to a
claude.ai artifact (an HTML page) that **always stays in sync** with the source.
A commit where the published artifact differs from what the source builds to is a bug.

## Paper

Topic: Claude writes an operating system. Data source: https://github.com/monperrus/boot-to-claude
(numbers in the paper are pinned to a commit stated in its introduction). Every number in
the paper must be recomputable from that repository; no invented facts.

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
