-- Pandoc filter: LaTeX-like equation numbering and \eqref resolution.
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
  { Link = resolve_refs },
}
