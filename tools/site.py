"""Render the verification site: the full session transcript, the defects, the oracle runs.

The paper links into this site (`\\siteurl` in paper/gen/stats.tex). Every tool call has
an anchor equal to its tool id, every transcript message an anchor `r-<uuid>`.

Usage:
  site.py                    write build/site/
  site.py --check            fail on any link (in the site or in build/paper.html) to a
                             missing page or anchor of the site; silent when all resolve
  site.py --check --selftest also check that the checker catches a forged link
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import (CEST, DATA, MAIN_SESSION, OS_REPO, PHASES, ROOT, SITE, TRAJ_REPO, commits,  # noqa: E402
                   fetch, human_text, oracle_runs, phase_page, row_anchor, ts)

OUT = ROOT / "build" / "site"
PAPER = ROOT / "build" / "paper.html"
GITHUB = f"https://github.com/{OS_REPO[0]}/commit/"
TRAJ_URL = f"https://github.com/{TRAJ_REPO[0]}/blob/{TRAJ_REPO[1]}/{MAIN_SESSION}"
FOLD = 30  # results longer than this many lines are folded

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#e2dfd8;--box:#f2f0ea;--hum:#fff3d6;--err:#fbe4e1;--acc:#1f5fbf}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#17171a;--fg:#e8e6e1;--mut:#9a988f;--line:#33333a;--box:#202026;--hum:#3a321c;--err:#3d2224;--acc:#7fb0ff}}
:root[data-theme="dark"]{--bg:#17171a;--fg:#e8e6e1;--mut:#9a988f;--line:#33333a;--box:#202026;--hum:#3a321c;--err:#3d2224;--acc:#7fb0ff}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:980px;padding:16px}
a{color:var(--acc)} h1{font-size:1.5em} nav{margin:.5em 0 1.5em;color:var(--mut)}
pre{background:var(--box);padding:8px 10px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font:12.5px/1.4 ui-monospace,monospace;margin:4px 0}
.ev{border-top:1px solid var(--line);padding:6px 0}.ev:target{outline:2px solid var(--acc);outline-offset:2px}
.meta{color:var(--mut);font-size:12px}.hum{background:var(--hum);padding:6px 10px}.say{white-space:pre-wrap}
.err pre{background:var(--err)} img{max-width:100%;image-rendering:pixelated}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top;font-size:13.5px}
tr:target{outline:2px solid var(--acc)}
"""


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def page(title: str, body: str, depth: int = 0) -> str:
    up = "../" * depth
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            f'<link rel="stylesheet" href="{up}site.css"></head><body>'
            f'<nav><a href="{up}index.html">QuineOS evidence</a> · <a href="{up}defects.html">defects</a> · '
            f'<a href="{up}oracles.html">oracle runs</a> · <a href="{up}paper.html">paper</a></nav>'
            f"<h1>{esc(title)}</h1>\n{body}\n</body></html>\n")


def when(t: datetime | None) -> str:
    return t.astimezone(CEST).strftime("%b %d, %H:%M:%S") if t else ""


# ---- transcript -----------------------------------------------------------------

def tool_input(c: dict) -> str:
    i, name = c["input"], c["name"]
    if name == "Bash":
        d = f'<div class="meta">{esc(i.get("description", ""))}</div>' if i.get("description") else ""
        return d + f"<pre>$ {esc(i.get('command', ''))}</pre>"
    if name == "Edit":
        diff = "\n".join(["- " + x for x in i.get("old_string", "").splitlines()]
                         + ["+ " + x for x in i.get("new_string", "").splitlines()])
        return f"<div>{esc(i.get('file_path', ''))}</div>{fold(diff)}"
    if name == "Write":
        return f"<div>{esc(i.get('file_path', ''))}</div>{fold(i.get('content', ''))}"
    if name == "Read":
        return f"<div>{esc(i.get('file_path', ''))}</div>"
    return fold(json.dumps(i, indent=1, ensure_ascii=False))


def fold(text: str, cls: str = "") -> str:
    pre = f"<pre{cls}>{esc(text)}</pre>"
    n = text.count("\n") + 1
    return f"<details><summary>{n} lines</summary>{pre}</details>" if n > FOLD else pre


def tool_result(x: dict, tid: str, images: dict[str, bytes], persisted: dict[str, bytes]) -> str:
    out = x.get("content")
    parts = out if isinstance(out, list) else [{"type": "text", "text": out or ""}]
    html_parts = []
    for k, p in enumerate(parts):
        if p.get("type") == "image":
            name = f"img/{tid}-{k}.{p['source']['media_type'].split('/')[-1]}"
            images[name] = base64.b64decode(p["source"]["data"])
            html_parts.append(f'<img src="../{name}" alt="image returned by the tool">')
        elif p.get("type") == "text":
            text = p["text"]
            m = re.search(r"saved to: \S*/tool-results/(\w+\.txt)", text)
            if m and m.group(1) in persisted:
                html_parts.append(f'<div class="meta">full output: <a href="../out/{m.group(1)}">{m.group(1)}</a></div>')
            html_parts.append(fold(text))
    cls = "err" if x.get("is_error") else ""
    return f'<div class="{cls}"><div class="meta">result{" (error)" if cls else ""}</div>{"".join(html_parts)}</div>'


def render_transcript(rows: list[dict], bounds: list,
                      persisted: dict[str, bytes]) -> tuple[dict[str, str], dict[str, bytes], dict[str, str]]:
    """Per-page HTML bodies, images, and the page of every tool id and row anchor."""
    results = {x["tool_use_id"]: x for r in rows if r.get("type") == "user"
               and isinstance(r["message"].get("content"), list)
               for x in r["message"]["content"] if isinstance(x, dict) and x.get("type") == "tool_result"}
    bodies: dict[str, list[str]] = defaultdict(list)
    where: dict[str, str] = {}
    images: dict[str, bytes] = {}
    for r in rows:
        t = ts(r)
        if t is None or r.get("type") not in ("user", "assistant"):
            continue
        pg = phase_page(t, bounds)
        anchor = row_anchor(r)
        c = r["message"].get("content")
        if r["type"] == "user":
            text = human_text(r)
            if text is not None:
                bodies[pg].append(f'<div class="ev hum" id="{anchor}"><div class="meta">human · {when(t)}</div>'
                                  f'<div class="say">{esc(text)}</div></div>')
                where[anchor] = pg
            elif isinstance(c, str) and c.startswith("This session is being continued"):
                bodies[pg].append(f'<div class="ev" id="{anchor}"><details><summary>context compaction '
                                  f'summary · {when(t)}</summary>{fold(c)}</details></div>')
                where[anchor] = pg
            continue
        for k, x in enumerate(c or []):
            if not isinstance(x, dict):
                continue
            if x.get("type") == "text" and x["text"].strip():
                a = f"{anchor}-{k}"
                bodies[pg].append(f'<div class="ev" id="{a}"><div class="meta">agent · {when(t)}</div>'
                                  f'<div class="say">{esc(x["text"])}</div></div>')
                where[a] = pg
            elif x.get("type") == "tool_use" and x["id"] not in where:  # a few rows are logged twice
                res = results.get(x["id"])
                bodies[pg].append(
                    f'<div class="ev" id="{x["id"]}"><div class="meta">{esc(x["name"])} · {when(t)} · '
                    f'<a href="#{x["id"]}">{x["id"]}</a></div>{tool_input(x)}'
                    f'{tool_result(res, x["id"], images, persisted) if res else ""}</div>')
                where[x["id"]] = pg
    return {k: "\n".join(v) for k, v in bodies.items()}, images, where


# ---- defects and oracles ----------------------------------------------------------

def defects_page(where: dict[str, str]) -> str:
    from stats import DEFECT_ROWS
    ds = json.loads((DATA / "trajectory-defects.json").read_text())
    groups = [(f"row-{n}", label, lambda d, c=comps: d["kind"] == "product" and d["component"] in c)
              for n, (label, comps) in enumerate(DEFECT_ROWS)]
    groups += [("oracle", "Oracle tooling", lambda d: d["kind"] == "oracle"),
               ("environment", "Environment", lambda d: d["kind"] == "environment")]

    def link(tid: str | None) -> str:
        return f'<a href="t/{where[tid]}.html#{tid}">{tid[-8:]}</a>' if tid else "—"

    out = [f"<p>{sum(1 for d in ds if not d.get('duplicate_of'))} defects annotated from the transcript by "
           "subagents, validated by <code>tools/defects.py</code> (every tool id exists, every evidence "
           "quote is verbatim in its chunk). Links go to the tool call that first showed the defect, "
           "the edits that fixed it and the run that confirmed the fix.</p>"]
    for gid, label, sel in groups:
        rows = [d for d in ds if sel(d) and not d.get("duplicate_of")]
        out.append(f'<h2 id="{gid}">{esc(label)} ({len(rows)})</h2><table><tr><th>id</th><th>defect</th>'
                   "<th>symptom / revealed by</th><th>first seen · fixed by · confirmed by</th></tr>")
        for d in rows:
            fixes = ", ".join(link(t) for t in d.get("fixed_by") or []) or "—"
            cm = " · in commit message" if d.get("in_commit_message") else ""
            out.append(f'<tr id="{d["id"]}"><td>{d["id"]}<div class="meta">{esc(d["component"])}</div></td>'
                       f'<td>{esc(d["summary"])}<pre>{esc(d.get("evidence") or "")}</pre></td>'
                       f'<td>{esc(d["symptom"])}<div class="meta">{esc(d["revealed_by"])}{cm}; '
                       f'{d.get("failed_attempts") or 0} failed attempts</div></td>'
                       f'<td>{link(d.get("first_seen"))} · {fixes} · {link(d.get("confirmed_by"))}</td></tr>')
        out.append("</table>")
    dups = [d for d in ds if d.get("duplicate_of")]
    out.append("<h2 id=\"duplicates\">Merged duplicates</h2><ul>" + "".join(
        f'<li id="{d["id"]}">{d["id"]} = <a href="#{d["duplicate_of"]}">{d["duplicate_of"]}</a></li>'
        for d in dups) + "</ul>")
    return "\n".join(out)


def oracles_page(log: list, where: dict[str, str]) -> str:
    from stats import ORACLES
    out = ["<p>Every execution of a scripted oracle in the session, classified by "
           "<code>tools/stats.py</code> from the verdict line of its output.</p>"]
    for name, *_ in ORACLES:
        runs = [x for x in log if x[0] == name]
        out.append(f'<h2 id="{name}">{name} ({len(runs)} runs)</h2><table>'
                   "<tr><th>time</th><th>verdict</th><th>tool call</th></tr>")
        out += [f'<tr><td>{when(t)}</td><td>{v}</td><td><a href="t/{where[tid]}.html#{tid}">{tid}</a></td></tr>'
                for _, t, v, tid in runs]
        out.append("</table>")
    return "\n".join(out)


def index_page(bounds: list, titles: dict[str, str], pages: dict[str, str]) -> str:
    items = [f'<li><a href="t/{c.sha}.html">{esc(titles[c.sha])}</a> '
             f'<span class="meta">until <a href="{GITHUB}{c.sha}">{c.sha}</a>, {when(c.when)}</span></li>'
             for c in bounds]
    if "after" in pages:
        items.append('<li><a href="t/after.html">after the last commit</a></li>')
    return (f"<p>The evidence behind the paper on QuineOS, an operating system written by an AI. "
            f"The transcript is the complete Claude Code session <a href=\"{TRAJ_URL}\">{MAIN_SESSION}</a> "
            f"(pinned at <code>{TRAJ_REPO[1][:7]}</code>); the OS is <a href=\"https://github.com/{OS_REPO[0]}\">"
            f"{OS_REPO[0]}</a> at <code>{OS_REPO[1][:7]}</code>. Each page holds the work that ends at one "
            "phase commit; every tool call is anchored by its id.</p><h2>Transcript by phase</h2><ol>"
            + "".join(items) + "</ol>")


def build() -> None:
    traj = fetch(*TRAJ_REPO)
    os_repo = fetch(*OS_REPO)
    rows = [json.loads(line) for line in (traj / MAIN_SESSION).open() if line.strip()]
    bounds = [c for c in commits(os_repo) if c.sha in PHASES]
    pdir = traj / MAIN_SESSION.removesuffix(".jsonl") / "tool-results"
    persisted = {p.name: p.read_bytes() for p in sorted(pdir.glob("*.txt"))}
    bodies, images, where = render_transcript(rows, bounds, persisted)
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "t").mkdir(parents=True)
    (OUT / "img").mkdir()
    (OUT / "out").mkdir()
    (OUT / "site.css").write_text(CSS)
    titles = {c.sha: re.sub(r"\\texttt\{(.*?)\}", r"\1", PHASES[c.sha]) for c in bounds} | {"after": "after the last commit"}
    for pg, body in bodies.items():
        (OUT / "t" / f"{pg}.html").write_text(page(f"Phase {titles[pg]}", body, 1))
    for name, data in images.items():
        (OUT / name).write_bytes(data)
    for name, data in persisted.items():
        (OUT / "out" / name).write_bytes(data)
    (OUT / "defects.html").write_text(page("Defects", defects_page(where)))
    (OUT / "oracles.html").write_text(page("Oracle runs", oracles_page(oracle_runs(rows)[1], where)))
    (OUT / "index.html").write_text(page("QuineOS: the evidence", index_page(bounds, titles, bodies)))
    if PAPER.exists():
        shutil.copy(PAPER, OUT / "paper.html")


# ---- link checker ---------------------------------------------------------------

def broken_links(pages: dict[str, str]) -> list[str]:
    """Links between site pages (relative, or absolute under SITE) to a missing page or anchor.

    `pages` maps a site path (e.g. "t/42f0af3.html") or "<paper>" to its HTML.
    """
    ids = {p: set(re.findall(r'\bid="([^"]+)"', h)) for p, h in pages.items()}
    bad = [f"{p}: duplicate ids" for p, h in pages.items() if len(re.findall(r'\bid="', h)) != len(ids[p])]
    for src, h in pages.items():
        base = "" if src == "<paper>" or "/" not in src else src.rsplit("/", 1)[0] + "/"
        for href in re.findall(r'\bhref="([^"]+)"', h):
            href = html.unescape(href)
            if href.startswith(SITE + "/"):
                target = href[len(SITE) + 1:]
            elif src == "<paper>" or re.match(r"[a-z]+:", href):
                continue
            else:
                target = base + href if not href.startswith("#") else src + href
            path, _, frag = target.partition("#")
            parts: list[str] = []
            for seg in path.split("/"):
                if seg == "..":
                    parts and parts.pop()
                elif seg not in ("", "."):
                    parts.append(seg)
            path = "/".join(parts) or "index.html"
            if path.endswith((".png", ".jpeg", ".txt", ".css")):
                exists = (OUT / path).exists()
            else:
                exists = path in pages and (not frag or frag in ids[path])
            if not exists:
                bad.append(f"{src}: {href}")
    return bad


def check(selftest: bool) -> None:
    pages = {str(p.relative_to(OUT)): p.read_text() for p in sorted(OUT.rglob("*.html"))}
    if PAPER.exists():
        pages["<paper>"] = PAPER.read_text()
    if selftest:
        some_tool = re.search(r'id="(toolu_\w+)"', pages["t/42f0af3.html"]).group(1)
        last = re.findall(r'id="(toolu_\w+)"', pages["t/a4a64cb.html"])[-1]
        probe = {"<paper>": f'<a href="{SITE}/t/42f0af3.html#{some_tool}">good</a>'
                            f'<a href="{SITE}/t/a4a64cb.html#{last}">boundary</a>'
                            f'<a href="{SITE}/t/42f0af3.html#toolu_bogus">forged</a>'}
        got = broken_links({**{k: v for k, v in pages.items() if k != "<paper>"}, **probe})
        assert got == [f"<paper>: {SITE}/t/42f0af3.html#toolu_bogus"], got
    bad = broken_links(pages)
    for b in bad:
        print(b)
    if bad:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.check:
        check(a.selftest)
    else:
        build()


if __name__ == "__main__":
    main()
