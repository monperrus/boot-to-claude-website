-- Pandoc filter: LaTeX-like numbering of equations, tables and figures, and \ref/\eqref
-- resolution. Captions get a "Table N." / "Figure N." prefix, as LaTeX prints them.
-- Images are inlined as data URIs by build.py, not here.

local eqnum = {}   -- label -> number
local count = 0

local function is_numbered(tex)
  if tex:match("\\label%s*{") then return true end
  return tex:match("^%s*\\begin{equation}") ~= nil
end

local function number_equations(inline)
  if inline.t ~= "Math" or inline.mathtype ~= "DisplayMath" then return nil end
  if not is_numbered(inline.text) then return nil end
  count = count + 1
  local label = inline.text:match("\\label%s*{([^}]*)}")
  if label then eqnum[label] = count end
  local tag = pandoc.Span({ pandoc.Str("(" .. count .. ")") }, { class = "eqno" })
  return pandoc.Span({ inline, tag }, { id = label or "", class = "equation" })
end

local floatnum = { Table = 0, Figure = 0 }

local function prefix_caption(caption, kind, label)
  floatnum[kind] = floatnum[kind] + 1
  if label ~= "" then eqnum[label] = floatnum[kind] end
  local tag = pandoc.Strong({ pandoc.Str(kind .. " " .. floatnum[kind] .. ".") })
  local first = caption.long[1]
  if first and (first.t == "Plain" or first.t == "Para") then
    first.content:insert(1, pandoc.Space())
    first.content:insert(1, tag)
  else
    caption.long:insert(1, pandoc.Plain({ tag }))
  end
end

-- \begin{table}...\label{tab:x} arrives as a Div with that id around the Table.
local function number_table(div)
  if not div.identifier:match("^tab:") then return nil end
  for _, b in ipairs(div.content) do
    if b.t == "Table" then
      prefix_caption(b.caption, "Table", div.identifier)
      return div
    end
  end
end

local function number_figure(fig)
  prefix_caption(fig.caption, "Figure", fig.identifier)
  return fig
end

local function resolve_refs(link)
  local kind = link.attributes["reference-type"]
  local target = link.attributes["reference"]
  if not target or not eqnum[target] then return nil end
  local n = tostring(eqnum[target])
  if kind == "eqref" then n = "(" .. n .. ")" end
  link.content = { pandoc.Str(n) }
  return link
end

-- \paragraph and below are run-in headings in LaTeX: never numbered.
local function unnumber_paragraphs(h)
  if h.level >= 4 then
    h.classes:insert("unnumbered")
    h.classes:insert("paragraph")
    return h
  end
end

return {
  { Header = unnumber_paragraphs },
  { Inline = number_equations },
  { traverse = "topdown", Div = number_table, Figure = number_figure },
  { Link = resolve_refs },
}
