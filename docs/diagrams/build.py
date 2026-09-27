"""Generate the architecture diagrams.

Writes standalone SVGs (docs/diagrams/*.svg, embedded in README.md) and the architecture page
(docs/architecture.html) from one source, so the README and the published page never drift apart.

Each shape carries both an explicit light-theme color (for standalone rendering on GitHub) and a class
(f-*/s-*) that the HTML page's CSS remaps to theme tokens for light and dark mode.

Run: python docs/diagrams/build.py
"""

from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

P = {  # light palette; the page CSS redefines these per theme
    "paper": "#F5F6F3", "card": "#FFFFFF", "ink": "#1B2230", "muted": "#5B6474", "line": "#C4C9D2",
    "route": "#0B7C6E", "routebg": "#DDF1EC", "warn": "#B7700B", "warnbg": "#FBEFD9",
    "stop": "#B83A33", "stopbg": "#F8E1DF",
}
SANS = "IBM Plex Sans, -apple-system, Segoe UI, Roboto, sans-serif"
MONO = "IBM Plex Mono, SFMono-Regular, Menlo, Consolas, monospace"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def rect(x: float, y: float, w: float, h: float, fill: str = "card", stroke: str = "line",
         rx: float = 8, sw: float = 1.2, dash: str | None = None) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect class="f-{fill} s-{stroke}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{P[fill]}" stroke="{P[stroke]}" stroke-width="{sw}"{d}/>')


def text(x: float, y: float, s: str, size: int = 13, fill: str = "ink", anchor: str = "middle",
         weight: int = 400, mono: bool = False, spacing: float = 0) -> str:
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text class="f-{fill}" x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'font-family="{MONO if mono else SANS}" text-anchor="{anchor}" fill="{P[fill]}"{ls}>{esc(s)}</text>')


def path(d: str, stroke: str = "line", sw: float = 1.4, marker: str | None = None, dash: str | None = None,
         fig: str = "") -> str:
    m = f' marker-end="url(#{fig}-arrow-{marker})"' if marker else ""
    da = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<path class="s-{stroke}" d="{d}" fill="none" stroke="{P[stroke]}" stroke-width="{sw}"{m}{da}/>'


def box(cx: float, cy: float, w: float, h: float, title: str, sub: str | None = None, fill: str = "card",
        stroke: str = "line", tcolor: str = "ink", mono: bool = False, sw: float = 1.2, dash: str | None = None,
        size: int = 13) -> str:
    out = rect(cx - w / 2, cy - h / 2, w, h, fill, stroke, sw=sw, dash=dash)
    if sub:
        out += text(cx, cy - 3, title, size, tcolor, weight=600, mono=mono)
        out += text(cx, cy + 13, sub, 11, "muted")
    else:
        out += text(cx, cy + 4.5, title, size, tcolor, weight=600, mono=mono)
    return out


def svg(fig: str, w: int, h: int, label: str, body: str) -> str:
    markers = "".join(
        f'<marker id="{fig}-arrow-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path class="f-{c}" d="M0 0 L10 5 L0 10 z" fill="{P[c]}"/></marker>'
        for c in ("line", "ink", "route", "warn", "stop"))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}">'
            f'<defs>{markers}</defs>'
            f'<rect class="bg f-paper" x="0" y="0" width="{w}" height="{h}" rx="14" fill="{P["paper"]}"/>'
            f"{body}</svg>")


# ── Figure 1: the hierarchy and one real routing path ────────────────
def fig_hierarchy() -> str:
    f = "hier"
    b = ""
    for y, label in ((70, "L1 · CEO"), (185, "L2 · DIRECTORS"), (305, "L3 · TEAMS"), (405, "L4 · SPECIALISTS")):
        b += text(24, y + 4, label, 11, "muted", anchor="start", weight=600, spacing=0.8)
    for y in (125, 245, 355):
        b += path(f"M150 {y} H1040", "line", 1, dash="2 5")

    b += box(595, 65, 190, 50, "CEO", "picks 1 of 2 divisions")
    divisions = {"engineering": 365, "operations": 825}
    b += box(365, 185, 210, 50, "Engineering Director", "picks 1 of 3 teams")
    b += box(825, 185, 210, 50, "Operations Director", "picks 1 of 3 teams", stroke="route", sw=2)
    teams = {"frontend_team": 215, "backend_team": 365, "database_team": 515,
             "qa_team": 675, "devops_team": 825, "security_team": 975}

    b += path("M595 90 L365 158", "line", 1.4, "line", fig=f)
    b += path("M595 90 L825 158", "route", 2.4, "route", fig=f)
    b += text(725, 117, "call_operations", 11, "route", anchor="start", mono=True)
    for name, x in teams.items():
        src = divisions["engineering"] if x < 600 else divisions["operations"]
        hot = name == "security_team"
        b += path(f"M{src} 210 L{x} 281", "route" if hot else "line", 2.4 if hot else 1.2,
                  "route" if hot else "line", fig=f)
        b += box(x, 305, 136, 44, name, mono=True, size=12, stroke="route" if hot else "line", sw=2 if hot else 1.2)
        if not hot:
            b += path(f"M{x} 327 L{x} 385", "line", 1.2, "line", fig=f)
            b += box(x, 403, 122, 34, "3 specialists", stroke="line", dash="4 3", tcolor="muted", size=12)
    b += text(905, 236, "call_security_team", 11, "route", anchor="start", mono=True)

    # security specialists: consulted in order, one left out
    b += rect(898, 372, 156, 150, "paper", "route", rx=10, sw=1.4, dash="5 4")
    b += path("M975 327 L975 370", "route", 2.4, "route", fig=f)
    for i, (name, order) in enumerate((("Secrets Agent", "1"), ("IAM Agent", "2"), ("Vulnerability Agent", "–"))):
        cy = 400 + i * 42
        used = order != "–"
        b += box(987, cy, 128, 32, name, fill="routebg" if used else "card", stroke="route" if used else "line",
                 size=11, tcolor="ink" if used else "muted")
        b += f'<circle class="f-{"route" if used else "line"}" cx="912" cy="{cy}" r="9" fill="{P["route" if used else "line"]}"/>'
        b += text(912, cy + 4, order, 11, "card", weight=700)

    # the trace this path produces
    b += rect(150, 440, 600, 118, "card", "line", rx=10)
    b += text(166, 462, "trace  (reducer operator.add, returned up through each wrapper node)", 11, "muted",
              anchor="start", weight=600)
    for i, line in enumerate(("CEO → operations", "Operations Director → security_team",
                              "Security Supervisor → Secrets Agent", "Security Supervisor → IAM Agent",
                              "Security Supervisor → FINISH")):
        b += text(166, 484 + i * 17, f"{i + 1}. {line}", 12, "route" if i < 4 else "ink", anchor="start", mono=True)
    return svg(f, 1060, 575, "Four-level hierarchy with the route for an AWS key leak highlighted: CEO to "
               "Operations Director to security_team, which consults Secrets Agent then IAM Agent.", b)


# ── Figure 2: team supervisor loop and the shrinking choice set ──────
def fig_team_loop() -> str:
    f = "team"
    b = ""
    b += f'<circle class="f-ink" cx="46" cy="170" r="9" fill="{P["ink"]}"/>' + text(46, 197, "START", 10, "muted")
    b += path("M57 170 L128 170", "ink", 1.4, "ink", fig=f)
    b += box(230, 170, 200, 62, "team_supervisor", "structured choice (Literal enum)", mono=True, stroke="ink", sw=1.6)
    for i, name in enumerate(("Vulnerability Agent", "IAM Agent", "Secrets Agent")):
        cy = 90 + i * 80
        b += box(575, cy, 200, 50, name, "create_agent · 2 real tools")
        b += path(f"M331 170 L472 {cy}", "route", 1.6, "route", fig=f)
    b += text(398, 118, "picks next", 11, "route", anchor="middle")
    b += path("M575 276 C575 322 230 322 230 204", "ink", 1.4, "ink", fig=f)
    b += text(402, 316, "note  +  consulted[name]  +  turns+1", 11, "ink", mono=True)
    b += path("M230 139 V36 H875 V137", "warn", 1.8, "warn", fig=f)
    b += text(552, 28, "FINISH from the LLM, or forced in code: turns = 3 · everyone consulted", 11, "warn")
    b += box(875, 170, 190, 62, "team_writer", "merges notes → team_output", mono=True, stroke="ink", sw=1.6)
    b += path("M971 170 L993 170", "ink", 1.4, "ink", fig=f)
    b += f'<circle class="f-ink" cx="1004" cy="170" r="9" fill="{P["ink"]}"/>' + text(1004, 197, "END", 10, "muted")

    b += text(40, 362, "ALLOWED CHOICES PER TURN", 11, "muted", anchor="start", weight=600, spacing=0.8)
    widths = {"Vulnerability Agent": 142, "IAM Agent": 80, "Secrets Agent": 108, "FINISH": 64}
    rows = [("turn 1", ["Vulnerability Agent", "IAM Agent", "Secrets Agent", "FINISH"], "first pick: Secrets Agent"),
            ("turn 2", ["Vulnerability Agent", "IAM Agent", "FINISH"], "Secrets consulted → removed"),
            ("turn 3", ["Vulnerability Agent", "FINISH"], "IAM consulted → removed")]
    for r, (label, opts, note) in enumerate(rows):
        y = 380 + r * 32
        b += text(40, y + 15, label, 11, "muted", anchor="start", mono=True)
        x = 100
        for o in opts:
            w = widths[o]
            hot = o == "FINISH"
            b += rect(x, y, w, 22, "warnbg" if hot else "card", "warn" if hot else "line", rx=11)
            b += text(x + w / 2, y + 15, o, 11, "warn" if hot else "ink", mono=True)
            x += w + 8
        b += text(x + 10, y + 15, note, 11, "muted", anchor="start")
    return svg(f, 1040, 480, "Team loop: the supervisor picks a specialist, the specialist returns a note, and "
               "the supervisor's allowed choices shrink each turn until FINISH, which the code forces at the cap.", b)


# ── Figure 3: real tools, and how each one fails honestly ────────────
def fig_tools() -> str:
    f = "tools"
    b = ""
    b += box(95, 175, 140, 56, "Specialist", "calls a tool")
    b += path("M166 175 L218 175", "ink", 1.4, "ink", fig=f)
    b += box(290, 175, 140, 56, "validate input", "before any I/O", stroke="ink", sw=1.6)
    b += path("M290 204 L290 283", "warn", 1.6, "warn", fig=f)
    b += text(298, 250, "missing / unsafe", 11, "warn", anchor="start")
    b += box(290, 310, 170, 48, "INPUT NEEDED", "names the artifact to supply", fill="warnbg", stroke="warn",
             tcolor="warn", mono=True)
    tiers = [("Analyzers ×18", "AST · sqlglot · YAML · WCAG math", False),
             ("Generators ×8", "templates · runbooks · skeletons", False),
             ("Local files & git ×3", "coverage XML · JUnit · git diff", True),
             ("Public APIs ×3", "OSV.dev · GitHub REST", True),
             ("Credentialed ×4", "Postgres (read-only) · AWS IAM", True)]
    for i, (title, sub, can_fail) in enumerate(tiers):
        cy = 55 + i * 62
        b += path(f"M361 175 L443 {cy}", "line", 1.3, "line", fig=f)
        b += box(560, cy, 232, 48, title, sub)
        b += path(f"M677 {cy - 8} H712", "route", 1.4)
        if can_fail:
            b += path(f"M677 {cy + 10} H752", "warn", 1.3, dash="5 4")
    b += path("M712 47 V295", "route", 1.6)
    b += path("M712 150 H808", "route", 1.8, "route", fig=f)
    b += path("M752 189 V313", "warn", 1.4, dash="5 4")
    b += path("M752 300 H808", "warn", 1.6, "warn", dash="5 4", fig=f)
    b += box(890, 150, 160, 56, "result", "computed from real input", fill="routebg", stroke="route", tcolor="route")
    b += box(890, 300, 160, 56, "UNAVAILABLE", "reason given, no data", fill="warnbg", stroke="warn", tcolor="warn",
             mono=True)
    b += text(890, 346, "when unreachable or not configured", 11, "warn", anchor="middle")
    return svg(f, 990, 370, "Every tool validates its input first, then computes a result from a real source; "
               "tools that depend on files, APIs or credentials return UNAVAILABLE instead of guessing.", b)


# ── Figure 4: SDLC gates and the NO-GO decisions that happened ───────
def fig_gates() -> str:
    f = "gates"
    b = ""
    gates = [("G0", "Requirements", ["v1.0 · NFR-07 had", "no acceptance test"]),
             ("G1", "Design", []),
             ("G2", "Implementation", ["v1.0 · lint errors,", "package not installed"]),
             ("G3", "Verification", ["v1.0 · AT-14/16 had", "no test · v1.1 · AT-23", "not traced"]),
             ("G4", "Validation (live)", ["v2.1 · supervisor kept", "consulting (FINISH 2/15)"]),
             ("G5", "Release", [])]
    for i, (gid, phase, stops) in enumerate(gates):
        x = 30 + i * 162
        b += rect(x, 40, 142, 72, "routebg", "route", rx=10, sw=1.6)
        b += text(x + 71, 70, gid, 20, "route", weight=700)
        b += text(x + 71, 94, phase, 12, "ink", weight=600)
        if i < len(gates) - 1:
            b += path(f"M{x + 144} 76 L{x + 160} 76", "ink", 1.4, "ink", fig=f)
        if stops:
            b += path(f"M{x + 71} 114 L{x + 71} 134", "stop", 1.4, dash="3 3")
            b += rect(x + 38, 136, 66, 20, "stopbg", "stop", rx=10)
            b += text(x + 71, 150, "NO-GO", 11, "stop", weight=700, mono=True)
            for j, line in enumerate(stops):
                b += text(x + 71, 174 + j * 15, line, 11, "muted")
            b += text(x + 71, 176 + len(stops) * 15, "fixed → re-run", 11, "route", weight=600)
    b += text(30, 26, "EACH GATE: GO · CONDITIONAL GO (advisory miss) · NO-GO (stops the pipeline)", 11, "muted",
              anchor="start", weight=600, spacing=0.6)
    b += text(970, 26, "v2.1.0: all six GO", 12, "route", anchor="end", weight=700)
    return svg(f, 1000, 250, "Six SDLC gates from requirements to release; four gates returned NO-GO during the "
               "build and passed after the cause was fixed. All six are GO for v2.1.0.", b)


FIGURES = {"hierarchy": fig_hierarchy, "team-loop": fig_team_loop, "tools": fig_tools, "gates": fig_gates}


def main() -> None:
    rendered = {name: fn() for name, fn in FIGURES.items()}
    for name, content in rendered.items():
        (HERE / f"{name}.svg").write_text(content + "\n")
    template = (HERE / "architecture.template.html").read_text()
    results = json.loads((ROOT / "reports" / "e2e_results.json").read_text())["runs"]
    rows = "\n".join(
        f"<tr><td>{r['task_id']}</td><td>{esc(r['task'])}</td><td><code>{r['division']}</code></td>"
        f"<td><code>{r['team']}</code></td><td class=\"num\">{r['llm_calls']}</td>"
        f"<td class=\"num\">{r['latency_s']:.1f}s</td></tr>" for r in results)
    page = template.replace("{{E2E_ROWS}}", rows)
    for name, content in rendered.items():
        page = page.replace("{{" + name + "}}", content)
    (ROOT / "docs" / "architecture.html").write_text(page)
    print("wrote", ", ".join(f"{n}.svg" for n in rendered), "and docs/architecture.html")


if __name__ == "__main__":
    main()
