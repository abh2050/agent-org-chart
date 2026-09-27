"""Frontend team tools: static analysis of JSX/CSS/HTML and WCAG color math (all local, no network)."""

from __future__ import annotations

import colorsys
import re
from html.parser import HTMLParser

from langchain_core.tools import tool

from hierarchy_company.tools._common import needs_input, report

_JSX_HINT = re.compile(r"<[A-Za-z]|use[A-Z]\w*\(|React")


def _call_args(code: str, start: int) -> str:
    """Return the text inside the parentheses that open at code[start] == '('."""
    depth = 0
    for i in range(start, len(code)):
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
            if depth == 0:
                return code[start + 1:i]
    return code[start + 1:]


def _jsx_open_tags(code: str) -> list[tuple[int, str, str]]:
    """Return (index, tag, attributes) for each opening JSX tag of a component (capitalized name),
    scanning braces so `=>` and `>` inside `{...}` do not end the tag early."""
    tags = []
    for m in re.finditer(r"<([A-Z][\w.]*)", code):
        depth, i = 0, m.end()
        while i < len(code):
            ch = code[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == ">" and depth == 0:
                break
            i += 1
        tags.append((m.start(), m.group(1), code[m.end():i]))
    return tags


def _line_of(code: str, index: int) -> int:
    return code.count("\n", 0, index) + 1


# ── React Agent ──────────────────────────────────────────────────────
@tool
def lint_react_component(code: str) -> str:
    """Statically lint React/JSX source code. Pass the component's source text. Reports list items rendered
    without a `key`, useEffect calls without a dependency array, direct mutation of useState values and
    index/random keys, each with a line number."""
    if not _JSX_HINT.search(code):
        return needs_input("lint_react_component", "React/JSX source code")
    findings: list[str] = []
    lines = code.splitlines()
    for i, line in enumerate(lines, 1):
        if ".map(" in line:
            window = " ".join(lines[i - 1:i + 3])
            if "<" in window and "key=" not in window:
                findings.append(f"line {i}: elements rendered in .map() have no `key` prop")
        if re.search(r"key=\{\s*(index|i|idx)\s*\}", line):
            findings.append(f"line {i}: array index used as key; reordering will remount/misassociate state")
        if "Math.random()" in line and "key=" in line:
            findings.append(f"line {i}: random key forces a remount on every render")

    for m in re.finditer(r"\buseEffect\s*\(", code):
        args = _call_args(code, m.end() - 1).strip()
        if not args.rstrip().endswith("]"):
            findings.append(f"line {_line_of(code, m.start())}: useEffect has no dependency array (runs after every render)")

    state_vars = re.findall(r"const\s*\[\s*(\w+)\s*,\s*set\w+\s*\]\s*=\s*useState", code)
    for var in state_vars:
        for m in re.finditer(rf"\b{var}\.(push|pop|shift|unshift|splice|sort|reverse)\(|\b{var}(\.\w+)+\s*=[^=]", code):
            findings.append(f"line {_line_of(code, m.start())}: direct mutation of state `{var}`; use its setter "
                            "with a new value")
    return report("React lint", findings, f"{len(lines)} lines, {len(state_vars)} useState hook(s).")


@tool
def analyze_render_triggers(code: str) -> str:
    """Find avoidable re-render causes in React/JSX source: context Provider values built inline, inline
    functions/objects/arrays passed as props to child components, and missing memoization. Pass source text."""
    if not _JSX_HINT.search(code):
        return needs_input("analyze_render_triggers", "React/JSX source code")
    findings: list[str] = []
    for m in re.finditer(r"<(\w+(?:\.\w+)?)\.Provider[^>]*value=\{\{", code):
        findings.append(f"line {_line_of(code, m.start())}: <{m.group(1)}.Provider> value is an inline object; every "
                        "render creates a new value and re-renders all consumers (wrap in useMemo)")
    for start, tag, attrs in _jsx_open_tags(code):
        if tag.endswith(".Provider"):
            continue  # reported above
        line = _line_of(code, start)
        for prop in re.findall(r"(\w+)=\{\s*(?:\([^)]*\)|\w+)\s*=>", attrs):
            findings.append(f"line {line}: inline function passed to <{tag}> as `{prop}` (new reference each render; "
                            "use useCallback)")
        for prop in re.findall(r"(\w+)=\{\s*[\[{]", attrs):
            if prop != "key":
                findings.append(f"line {line}: inline object/array passed to <{tag}> as `{prop}` (use useMemo or a "
                                "module constant)")
    memo = len(re.findall(r"\b(useMemo|useCallback|React\.memo|memo\()", code))
    return report("Render-trigger analysis", findings, f"memoization primitives used: {memo}.")


# ── UI Agent ─────────────────────────────────────────────────────────
@tool
def audit_css_layout(css: str) -> str:
    """Audit CSS for layout problems: inconsistent spacing scale, tiny font sizes, fixed widths that overflow
    mobile screens, missing media queries and !important overuse. Pass the stylesheet text."""
    body = re.sub(r"@media[^{]*", "", css)  # media-query conditions are not declarations
    decls = re.findall(r"([\w-]+)\s*:\s*([^;{}]+)", body)
    if not decls:
        return needs_input("audit_css_layout", "CSS stylesheet text")
    findings: list[str] = []
    spacing = sorted({v for p, val in decls if re.match(r"(margin|padding|gap)", p)
                      for v in re.findall(r"(\d+(?:\.\d+)?)px", val)}, key=float)
    if len(spacing) > 6:
        findings.append(f"{len(spacing)} distinct px spacing values ({', '.join(spacing)}); adopt a 4/8px scale")
    off_grid = [v for v in spacing if float(v) % 4]
    if off_grid:
        findings.append(f"spacing values off a 4px grid: {', '.join(off_grid)}")
    for p, val in decls:
        size = re.search(r"(\d+(?:\.\d+)?)px", val)
        if p == "font-size" and size and float(size.group(1)) < 12:
            findings.append(f"font-size {size.group(1)}px is below 12px (legibility)")
        if p in ("width", "min-width") and size and float(size.group(1)) > 375:
            findings.append(f"{p}: {size.group(1)}px exceeds a 375px mobile viewport (use max-width or %)")
    if "@media" not in css:
        findings.append("no @media queries: layout is not responsive")
    important = css.count("!important")
    if important > 2:
        findings.append(f"{important} uses of !important (specificity problems)")
    return report("CSS layout audit", findings, f"{len(decls)} declarations.")


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    c = color.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", c):
        c = "".join(ch * 2 for ch in c)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        m = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", color.strip())
        if not m:
            raise ValueError(f"unrecognized color {color!r} (use #rgb, #rrggbb or rgb(r,g,b))")
        return tuple(min(int(x), 255) / 255 for x in m.groups())  # type: ignore[return-value]
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(rgb: tuple[float, float, float]) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    lf, lb = _luminance(_hex_to_rgb(fg)), _luminance(_hex_to_rgb(bg))
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


@tool
def generate_design_tokens(brand_color: str) -> str:
    """Generate a 50–900 color scale from a brand color (hex or rgb) using HSL lightness steps, and give the
    accessible text color (black or white) for each step with its WCAG contrast ratio."""
    try:
        r, g, b = _hex_to_rgb(brand_color)
    except ValueError as exc:
        return needs_input("generate_design_tokens", str(exc))
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    rows = []
    for step, light in zip((50, 100, 200, 300, 400, 500, 600, 700, 800, 900),
                           (0.95, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.12), strict=True):
        rr, gg, bb = colorsys.hls_to_rgb(h, light, s)
        hexv = "#{:02X}{:02X}{:02X}".format(*(round(v * 255) for v in (rr, gg, bb)))
        on_white, on_black = contrast_ratio("#000000", hexv), contrast_ratio("#FFFFFF", hexv)
        text, ratio = ("#000000", on_white) if on_white >= on_black else ("#FFFFFF", on_black)
        rows.append(f"color.brand.{step} = {hexv}  (text {text}, contrast {ratio:.2f}:1)")
    return "Design tokens:\n" + "\n".join(rows) + "\nspace.scale = 4, 8, 12, 16, 24, 32, 48px"


# ── Accessibility Agent ──────────────────────────────────────────────
@tool
def check_wcag_contrast(fg: str, bg: str) -> str:
    """Compute the exact WCAG 2.1 contrast ratio between a foreground and background color (hex or rgb) and
    report pass/fail for AA and AAA, normal and large text."""
    try:
        ratio = contrast_ratio(fg, bg)
    except ValueError as exc:
        return needs_input("check_wcag_contrast", str(exc))

    def verdict(threshold: float) -> str:
        return "PASS" if ratio >= threshold else "FAIL"

    return (f"Contrast {fg} on {bg} = {ratio:.2f}:1. AA normal (4.5) {verdict(4.5)}, AA large (3.0) {verdict(3.0)}, "
            f"AAA normal (7.0) {verdict(7.0)}, AAA large (4.5) {verdict(4.5)}.")


class _A11yParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.findings: list[str] = []
        self.label_for: set[str] = set()
        self.inputs: list[tuple[int, str, dict[str, str]]] = []
        self.headings: list[int] = []
        self.saw_html = self.has_lang = False
        self._open: list[tuple[str, int, dict[str, str], list[str]]] = []  # button/a text capture

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: v or "" for k, v in attrs_list}
        line = self.getpos()[0]
        if tag == "html":
            self.saw_html, self.has_lang = True, bool(attrs.get("lang"))
        elif tag == "img" and "alt" not in attrs:
            self.findings.append(f"line {line}: <img> without alt attribute")
        elif tag == "label" and attrs.get("for"):
            self.label_for.add(attrs["for"])
        elif tag in ("input", "select", "textarea") and attrs.get("type") not in ("hidden", "submit", "button"):
            self.inputs.append((line, tag, attrs))
        elif re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        elif tag in ("button", "a"):
            self._open.append((tag, line, attrs, []))
        if tag in ("div", "span") and "onclick" in attrs and "role" not in attrs:
            self.findings.append(f"line {line}: <{tag} onclick> without role/tabindex (not keyboard accessible)")
        if attrs.get("tabindex", "0").lstrip("-").isdigit() and int(attrs.get("tabindex", "0")) > 0:
            self.findings.append(f"line {line}: positive tabindex breaks natural focus order")

    def handle_data(self, data: str) -> None:
        for _, _, _, text in self._open:
            text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._open and self._open[-1][0] == tag:
            name, line, attrs, text = self._open.pop()
            if not "".join(text).strip() and not attrs.get("aria-label") and not attrs.get("aria-labelledby"):
                self.findings.append(f"line {line}: <{name}> has no accessible name (text or aria-label)")
            if name == "a" and not attrs.get("href"):
                self.findings.append(f"line {line}: <a> without href is not focusable")


@tool
def audit_html_accessibility(html: str) -> str:
    """Audit HTML markup for accessibility problems: images without alt, buttons/links without an accessible
    name, form fields without labels, missing lang, skipped heading levels, positive tabindex and clickable
    divs. Pass the HTML text."""
    if "<" not in html:
        return needs_input("audit_html_accessibility", "HTML markup")
    p = _A11yParser()
    p.feed(html)
    for line, tag, attrs in p.inputs:
        if not (attrs.get("aria-label") or attrs.get("aria-labelledby") or attrs.get("id") in p.label_for):
            p.findings.append(f"line {line}: <{tag}> has no associated <label> or aria-label")
    if p.saw_html and not p.has_lang:
        p.findings.append("<html> is missing the lang attribute")
    for prev, cur in zip(p.headings, p.headings[1:], strict=False):
        if cur > prev + 1:
            p.findings.append(f"heading level skips from h{prev} to h{cur}")
    return report("HTML accessibility audit", p.findings)
