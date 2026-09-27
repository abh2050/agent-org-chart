"""AT-02 real behaviour of engineering tools (frontend, backend, database) · AT-24/AT-26 for Postgres."""

from __future__ import annotations

import base64
import json
import time

import pytest

from hierarchy_company.tools import backend as be
from hierarchy_company.tools import database as db
from hierarchy_company.tools import frontend as fe


# ── Frontend ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(("fg", "bg", "ratio"), [("#000000", "#FFFFFF", "21.00"), ("#777777", "#ffffff", "4.48"),
                                                 ("#fff", "#fff", "1.00"), ("rgb(0,0,0)", "#FFFFFF", "21.00")])
def test_at02_wcag_contrast_matches_spec_formula(fg: str, bg: str, ratio: str) -> None:
    assert f"= {ratio}:1" in fe.check_wcag_contrast.invoke({"fg": fg, "bg": bg})


def test_at02_wcag_verdicts_and_bad_color() -> None:
    out = fe.check_wcag_contrast.invoke({"fg": "#777777", "bg": "#FFFFFF"})
    assert "AA normal (4.5) FAIL" in out and "AA large (3.0) PASS" in out
    assert fe.check_wcag_contrast.invoke({"fg": "blue-ish", "bg": "#fff"}).startswith("INPUT NEEDED")


def test_at02_react_lint_finds_real_issues() -> None:
    code = """const [cart, setCart] = useState([]);
useEffect(() => { load(); });
useEffect(() => { load(); }, [id]);
cart.push(item);
{rows.map((r, index) => <li key={index}>{r}</li>)}
{rows.map(r => <Row r={r} />)}"""
    out = fe.lint_react_component.invoke({"code": code})
    assert "line 2: useEffect has no dependency array" in out and "line 3" not in out.split("useEffect")[1][:10]
    assert "direct mutation of state `cart`" in out
    assert "array index used as key" in out and "line 6: elements rendered in .map() have no `key`" in out


def test_at02_render_triggers_handle_arrow_functions_in_props() -> None:
    code = "<Ctx.Provider value={{a, b}}><List onSelect={(i) => pick(i)} style={{m: 1}} key={k} /></Ctx.Provider>"
    out = fe.analyze_render_triggers.invoke({"code": code})
    assert "<Ctx.Provider> value is an inline object" in out
    assert "inline function passed to <List> as `onSelect`" in out
    assert "as `style`" in out and "as `key`" not in out
    assert out.count("Provider") == 1


def test_at02_css_audit() -> None:
    out = fe.audit_css_layout.invoke({"css": ".a{margin:5px;font-size:10px;width:960px}"})
    assert "font-size 10px" in out and "960px exceeds" in out and "no @media" in out and "off a 4px grid: 5" in out
    clean = fe.audit_css_layout.invoke({"css": "@media (min-width: 600px){.a{margin:8px;font-size:16px}}"})
    assert "no issues found" in clean


def test_at02_design_tokens_are_computed_and_accessible() -> None:
    out = fe.generate_design_tokens.invoke({"brand_color": "#2563EB"})
    assert out.count("color.brand.") == 10
    ratios = [float(x.split("contrast ")[1].split(":")[0]) for x in out.splitlines() if "contrast" in x]
    assert all(r >= 4.5 for r in ratios)


def test_at02_html_accessibility() -> None:
    html = ('<html><h1>A</h1><h3>B</h3><img src="x"><button></button><button aria-label="Close"></button>'
            '<label for="e">E</label><input id="e"><input id="q"><div onclick="go()">x</div></html>')
    out = fe.audit_html_accessibility.invoke({"html": html})
    for expected in ("<img> without alt", "<button> has no accessible name", "<input> has no associated",
                     "missing the lang", "h1 to h3", "<div onclick>"):
        assert expected in out
    assert out.count("<button> has no accessible name") == 1 and out.count("<input> has no associated") == 1


# ── Backend ──────────────────────────────────────────────────────────
OPENAPI = """openapi: 3.0.3
info: {title: Orders}
paths:
  /orders/{id}:
    get:
      security: [{bearerAuth: []}]
      responses: {'200': {description: ok}}
  /orders:
    post: {operationId: create, responses: {}}
    get: {operationId: create, responses: {'200': {description: ok}}}
"""


def test_at02_openapi_validation() -> None:
    out = be.validate_openapi_spec.invoke({"spec": OPENAPI})
    for expected in ("info.version is missing", "GET /orders/{id}: missing operationId", "'id' is not declared",
                     "'bearerAuth' is not defined", "401 response", "POST /orders: no responses",
                     "duplicate operationId 'create'"):
        assert expected in out
    assert be.validate_openapi_spec.invoke({"spec": "hello: world"}).startswith("INPUT NEEDED")


def test_at02_rest_design() -> None:
    out = be.design_rest_endpoints.invoke({"resource": "order", "parent": "customers"})
    assert "POST   /v1/customers/{customer_id}/orders" in out
    assert "DELETE /v1/customers/{customer_id}/orders/{id}" in out


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def test_at02_inspect_jwt() -> None:
    now = int(time.time())
    bad = f"{_b64({'alg': 'none'})}.{_b64({'sub': '1', 'iat': now, 'exp': now + 86400, 'password': 'x'})}."
    out = be.inspect_jwt.invoke({"token": bad})
    assert "alg=none" in out and "missing 'aud'" in out and "lifetime 24.0h" in out and "sensitive data" in out
    good = f"{_b64({'alg': 'ES256'})}.{_b64(dict(iss='i', aud='a', sub='1', jti='j', iat=now, exp=now + 600))}.s"
    assert "no issues found" in be.inspect_jwt.invoke({"token": good})
    assert be.inspect_jwt.invoke({"token": "not-a-jwt"}).startswith("INPUT NEEDED")


def test_at02_jwt_config_generator() -> None:
    cfg = json.loads(be.generate_jwt_config.invoke({"issuer": "https://auth.x", "audience": "api"}).split("\n", 1)[1])
    assert cfg["jwks_uri"] == "https://auth.x/.well-known/jwks.json" and "exp" in cfg["required_claims"]


COMPOSE = """services:
  gateway: {image: nginx, depends_on: [orders]}
  orders: {image: 'orders:1.2', environment: {PAY_URL: 'http://payments:8080/x'}}
  payments: {image: 'payments:latest', depends_on: [orders]}
  db: {image: 'postgres:16', ports: ['5432:5432'], restart: always, healthcheck: {test: x},
       deploy: {resources: {limits: {memory: 1g}}}}
"""


def test_at02_compose_dependencies_and_cycles() -> None:
    out = be.map_service_dependencies.invoke({"compose_yaml": COMPOSE})
    assert "orders -> payments" in out and "cycle: orders -> payments -> orders" in out


def test_at02_compose_resilience() -> None:
    out = be.check_service_resilience.invoke({"compose_yaml": COMPOSE})
    assert "gateway: image 'nginx' is not pinned" in out and "payments: image 'payments:latest'" in out
    assert "db: datastore port published" in out and "db: no healthcheck" not in out


# ── Database ─────────────────────────────────────────────────────────
QUERY = ("SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.customer_id = 42 "
         "AND o.status = 'open' AND DATE(o.created_at) > '2024-01-01' AND c.email LIKE '%@x.com' "
         "ORDER BY o.created_at DESC OFFSET 5000 LIMIT 50")


def test_at02_sql_analysis() -> None:
    out = db.analyze_sql_query.invoke({"query": QUERY})
    for expected in ("SELECT *", "OFFSET 5000", "DATE(o.created_at)", "leading wildcard"):
        assert expected in out
    assert "without WHERE" in db.analyze_sql_query.invoke({"query": "DELETE FROM orders"})
    assert "NOT EXISTS" in db.analyze_sql_query.invoke({"query": "SELECT a FROM t WHERE a NOT IN (SELECT b FROM u)"})
    assert db.analyze_sql_query.invoke({"query": "SELEC oops FRM"}).startswith("INPUT NEEDED")


def test_at02_index_suggestions_follow_equality_range_sort() -> None:
    out = db.suggest_indexes.invoke({"query": "SELECT id FROM orders WHERE customer_id = 1 AND status = 'x' "
                                              "AND created_at > now() - interval '1 day' ORDER BY total"})
    assert "ON orders (customer_id, status, created_at);" in out and "total" not in out
    join = db.suggest_indexes.invoke({"query": "SELECT 1 FROM a JOIN b ON b.a_id = a.id"})
    assert "ON b (a_id);" in join


PLAN = """Sort  (cost=1.00..2.00 rows=1000 width=8) (actual time=9000.1..9100.0 rows=50 loops=1)
  Sort Method: external merge  Disk: 215040kB
  ->  Seq Scan on orders  (cost=0.00..98000.00 rows=1200 width=8) (actual time=0.1..8800.2 rows=48123 loops=1)
        Rows Removed by Filter: 4764207
Execution Time: 9104.3 ms"""


def test_at02_explain_plan_parser() -> None:
    out = db.analyze_explain_plan.invoke({"plan": PLAN})
    assert "9,104.3 ms" in out and "Seq Scan on orders over ~48,123" in out and "planned 1,200, actual 48,123" in out
    assert "4,764,207 rows" in out and "210 MB to disk" in out


def test_at24_postgres_tools_unavailable_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HC_DATABASE_URL", raising=False)
    for tool, args in ((db.explain_sql_query, {"query": "SELECT 1"}), (db.inspect_table_schema, {"table": "orders"}),
                       (db.get_slow_query_stats, {"table": "orders"})):
        out = tool.invoke(args)
        assert out.startswith("UNAVAILABLE") and "HC_DATABASE_URL is not set" in out


def test_at24_postgres_connection_failure_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    assert db.inspect_table_schema.invoke({"table": "orders"}).startswith("UNAVAILABLE")


def test_at26_explain_is_select_only_and_identifiers_validated() -> None:
    for q in ("DELETE FROM orders", "UPDATE t SET a = 1", "DROP TABLE t"):
        assert "only SELECT" in db.explain_sql_query.invoke({"query": q}) or \
            db.explain_sql_query.invoke({"query": q}).startswith("INPUT NEEDED")
    assert db.inspect_table_schema.invoke({"table": "orders; DROP TABLE x"}).startswith("INPUT NEEDED")
