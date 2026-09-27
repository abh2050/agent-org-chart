"""Backend team tools: OpenAPI validation, REST design, JWT inspection, compose topology (all local)."""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

from langchain_core.tools import tool

from hierarchy_company.tools._common import load_yaml_docs, needs_input, report

_HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


# ── API Agent ────────────────────────────────────────────────────────
@tool
def validate_openapi_spec(spec: str) -> str:
    """Validate an OpenAPI 3.x document (YAML or JSON text): version, info, operations without responses or
    operationId, duplicate operationIds, undeclared path parameters, undefined security schemes and secured
    operations that do not document a 401 response."""
    try:
        docs = load_yaml_docs(spec)
    except ValueError as exc:
        return needs_input("validate_openapi_spec", str(exc))
    doc = docs[0] if docs else None
    if not isinstance(doc, dict) or not ("openapi" in doc or "swagger" in doc):
        return needs_input("validate_openapi_spec", "an OpenAPI document with an `openapi` field")
    findings: list[str] = []
    if "swagger" in doc:
        findings.append("Swagger 2.0 document; migrate to OpenAPI 3.x")
    elif not str(doc["openapi"]).startswith("3."):
        findings.append(f"unsupported openapi version {doc['openapi']}")
    info = doc.get("info") or {}
    for key in ("title", "version"):
        if key not in info:
            findings.append(f"info.{key} is missing")
    schemes = set(((doc.get("components") or {}).get("securitySchemes") or {}).keys())
    global_security = doc.get("security") or []
    op_ids: dict[str, str] = {}
    paths = doc.get("paths") or {}
    if not paths:
        findings.append("no paths defined")
    ops = 0
    for path, item in paths.items():
        path_params = set(re.findall(r"\{(\w+)\}", path))
        shared = {p.get("name") for p in (item.get("parameters") or []) if isinstance(p, dict)}
        for method, op in (item or {}).items():
            if method not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            ops += 1
            where = f"{method.upper()} {path}"
            responses = op.get("responses") or {}
            if not responses:
                findings.append(f"{where}: no responses defined")
            if not op.get("operationId"):
                findings.append(f"{where}: missing operationId")
            elif op["operationId"] in op_ids:
                findings.append(f"{where}: duplicate operationId '{op['operationId']}' (also {op_ids[op['operationId']]})")
            else:
                op_ids[op["operationId"]] = where
            declared = shared | {p.get("name") for p in (op.get("parameters") or []) if isinstance(p, dict)}
            for missing in sorted(path_params - declared):
                findings.append(f"{where}: path parameter '{missing}' is not declared")
            security = op.get("security", global_security)
            for req in security or []:
                for name in req:
                    if name not in schemes:
                        findings.append(f"{where}: security scheme '{name}' is not defined in components.securitySchemes")
            if security and "401" not in {str(k) for k in responses}:
                findings.append(f"{where}: secured operation does not document a 401 response")
    return report("OpenAPI validation", findings, f"{len(paths)} path(s), {ops} operation(s).")


@tool
def design_rest_endpoints(resource: str, parent: str = "") -> str:
    """Generate the conventional REST endpoint set (method, path, success/error status codes) for a resource
    name, optionally nested under a parent resource, following RFC 9110 semantics and cursor pagination."""
    name = re.sub(r"[^a-z0-9_-]", "", resource.strip().lower().replace(" ", "-"))
    if not name:
        return needs_input("design_rest_endpoints", "a resource name, e.g. 'orders'")
    plural = name if name.endswith("s") else name + "s"
    base = f"/v1/{parent.strip('/').lower()}/{{{parent.rstrip('s')}_id}}/{plural}" if parent else f"/v1/{plural}"
    rows = [
        f"GET    {base}?cursor=&limit=     200, 400",
        f"POST   {base}                   201 (+Location), 400, 409, 422",
        f"GET    {base}/{{id}}              200, 404",
        f"PATCH  {base}/{{id}}              200, 400, 404, 409, 422",
        f"PUT    {base}/{{id}}              200/201, 400, 404, 422",
        f"DELETE {base}/{{id}}              204, 404",
    ]
    return ("Endpoints:\n" + "\n".join(rows) +
            "\nAll: 401/403 when secured, 429 with Retry-After, errors as application/problem+json (RFC 9457); "
            "use Idempotency-Key on POST.")


# ── Auth Agent ───────────────────────────────────────────────────────
@tool
def generate_jwt_config(issuer: str, audience: str) -> str:
    """Generate a JWT configuration for an issuer and audience that follows RFC 8725 (JWT best current
    practice): asymmetric signing, short-lived access tokens, rotating refresh tokens and required claims."""
    if not issuer.strip() or not audience.strip():
        return needs_input("generate_jwt_config", "issuer and audience")
    config = {
        "issuer": issuer, "audience": audience, "algorithm": "ES256 (or RS256); never 'none' or shared-secret HS*",
        "jwks_uri": f"{issuer.rstrip('/')}/.well-known/jwks.json", "key_rotation": "every 90 days, overlap 2 keys",
        "access_token_ttl": "5-15 minutes", "refresh_token": "opaque, rotating, reuse-detection, TTL <= 7 days",
        "required_claims": ["iss", "sub", "aud", "exp", "iat", "nbf", "jti"],
        "validation": "verify signature, alg allow-list, iss, aud, exp/nbf with <=60s leeway at EVERY service",
        "transport": "Authorization: Bearer; if cookies: HttpOnly, Secure, SameSite=Strict",
    }
    return "JWT config (RFC 8725):\n" + json.dumps(config, indent=2)


def _b64json(part: str) -> dict[str, Any]:
    padded = part + "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


@tool
def inspect_jwt(token: str) -> str:
    """Decode a JWT (without verifying the signature) and audit its header and claims: dangerous algorithms,
    missing iss/aud/exp/iat/jti, excessive lifetime, expiry and sensitive data in the payload."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return needs_input("inspect_jwt", "a compact JWT (header.payload.signature)")
    try:
        header, claims = _b64json(parts[0]), _b64json(parts[1])
    except (ValueError, json.JSONDecodeError):
        return needs_input("inspect_jwt", "a JWT whose header and payload are base64url JSON")
    findings: list[str] = []
    alg = str(header.get("alg", ""))
    if alg.lower() == "none":
        findings.append("alg=none: token is unsigned")
    elif alg.startswith("HS"):
        findings.append(f"alg={alg}: shared-secret signing; every verifier can also forge tokens")
    for claim in ("iss", "aud", "exp", "iat", "jti"):
        if claim not in claims:
            findings.append(f"missing '{claim}' claim")
    if isinstance(claims.get("exp"), int | float) and isinstance(claims.get("iat"), int | float):
        hours = (claims["exp"] - claims["iat"]) / 3600
        if hours > 1:
            findings.append(f"lifetime {hours:.1f}h exceeds 1h for an access token")
    if isinstance(claims.get("exp"), int | float) and claims["exp"] < time.time():
        findings.append("token is expired")
    sensitive = [k for k in claims if re.search(r"pass|secret|ssn|card|dob", k, re.I)]
    if sensitive:
        findings.append(f"sensitive data in payload (JWTs are readable by anyone): {sensitive}")
    return report("JWT inspection", findings,
                  f"alg={alg or '?'}, claims={sorted(claims)}. Signature NOT verified.")


# ── Microservice Agent ───────────────────────────────────────────────
def _compose_services(text: str) -> dict[str, dict[str, Any]]:
    docs = load_yaml_docs(text)
    doc = docs[0] if docs else {}
    services = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(services, dict) or not services:
        raise ValueError("a docker-compose document with a `services` map")
    return {k: (v or {}) for k, v in services.items()}


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    state: dict[str, int] = {}

    def dfs(node: str, path: list[str]) -> None:
        state[node] = 1
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                cycles.append([*path[path.index(nxt):], nxt])
            elif nxt not in state:
                dfs(nxt, [*path, nxt])
        state[node] = 2

    for n in sorted(graph):
        if n not in state:
            dfs(n, [n])
    return cycles


@tool
def map_service_dependencies(compose_yaml: str) -> str:
    """Map service-to-service dependencies from a docker-compose file (depends_on, links and service
    hostnames referenced in environment URLs). Reports the edges, fan-in hotspots and dependency cycles."""
    try:
        services = _compose_services(compose_yaml)
    except ValueError as exc:
        return needs_input("map_service_dependencies", str(exc))
    graph: dict[str, set[str]] = {s: set() for s in services}
    for name, svc in services.items():
        deps = svc.get("depends_on") or []
        graph[name] |= set(deps if isinstance(deps, list) else deps.keys())
        graph[name] |= {link.split(":")[0] for link in svc.get("links") or []}
        env = svc.get("environment") or {}
        values = env.values() if isinstance(env, dict) else [e.split("=", 1)[-1] for e in env]
        for value in values:
            for other in services:
                if other != name and re.search(rf"//{re.escape(other)}[:/]", str(value)):
                    graph[name].add(other)
    edges = [f"{a} -> {b}" for a in sorted(graph) for b in sorted(graph[a])]
    fan_in = {s: sum(s in deps for deps in graph.values()) for s in services}
    findings = [f"cycle: {' -> '.join(c)}" for c in _find_cycles(graph)]
    findings += [f"'{s}' is depended on by {n} services (single point of failure)" for s, n in fan_in.items() if n >= 3]
    return report("Service dependency map", findings, f"edges: {', '.join(edges) or 'none'}.")


@tool
def check_service_resilience(compose_yaml: str) -> str:
    """Audit a docker-compose file for resilience gaps per service: missing healthcheck, restart policy and
    resource limits, unpinned ':latest' images, and datastores published on host ports."""
    try:
        services = _compose_services(compose_yaml)
    except ValueError as exc:
        return needs_input("check_service_resilience", str(exc))
    findings: list[str] = []
    for name, svc in services.items():
        image = str(svc.get("image", ""))
        if "healthcheck" not in svc:
            findings.append(f"{name}: no healthcheck (depends_on cannot wait for readiness)")
        if svc.get("restart") in (None, "no"):
            findings.append(f"{name}: no restart policy")
        if not ((svc.get("deploy") or {}).get("resources") or {}).get("limits") and "mem_limit" not in svc:
            findings.append(f"{name}: no CPU/memory limits")
        if image and (image.endswith(":latest") or ":" not in image.split("/")[-1]):
            findings.append(f"{name}: image '{image}' is not pinned to a version")
        if re.search(r"postgres|mysql|mongo|redis", image) and svc.get("ports"):
            findings.append(f"{name}: datastore port published to the host ({svc['ports']})")
    return report("Service resilience audit", findings, f"{len(services)} service(s).")
