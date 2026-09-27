"""DevOps team tools: Dockerfile/K8s static analysis, artifact generators, live GitHub Actions status."""

from __future__ import annotations

import os
import re
from typing import Any

from langchain_core.tools import tool

from hierarchy_company.tools._common import (
    ToolUnavailable,
    http_get_json,
    load_yaml_docs,
    needs_input,
    report,
    unavailable,
)

_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
_SECRET_NAME = re.compile(r"(PASSWORD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY)", re.I)


# ── Docker Agent ─────────────────────────────────────────────────────
@tool
def scan_dockerfile(dockerfile: str) -> str:
    """Lint a Dockerfile (hadolint-style rules): unpinned or ':latest' base images, running as root, ADD
    instead of COPY, apt-get without cleanup or --no-install-recommends, pip without --no-cache-dir,
    curl|sh installs, secrets baked into ENV/ARG, missing HEALTHCHECK and COPY of the whole context."""
    lines = [ln.strip() for ln in dockerfile.splitlines()]
    instructions = [(i, ln) for i, ln in enumerate(lines, 1) if ln and not ln.startswith("#")]
    if not any(ln.upper().startswith("FROM") for _, ln in instructions):
        return needs_input("scan_dockerfile", "Dockerfile text containing a FROM instruction")
    findings: list[str] = []
    for i, ln in instructions:
        up = ln.upper()
        if up.startswith("FROM"):
            image = ln.split()[1] if len(ln.split()) > 1 else ""
            if image.endswith(":latest") or (":" not in image and "@" not in image and image != "scratch"):
                findings.append(f"line {i}: base image '{image}' is not pinned (use a version tag or digest)")
        if up.startswith("ADD") and not re.search(r"https?://|\.tar", ln):
            findings.append(f"line {i}: use COPY instead of ADD for local files")
        if "APT-GET INSTALL" in up and "--NO-INSTALL-RECOMMENDS" not in up:
            findings.append(f"line {i}: apt-get install without --no-install-recommends")
        if "APT-GET INSTALL" in up and "RM -RF /VAR/LIB/APT/LISTS" not in up:
            findings.append(f"line {i}: apt lists not removed in the same RUN (bloats the layer)")
        if re.search(r"\bpip3? install\b", ln) and "--no-cache-dir" not in ln:
            findings.append(f"line {i}: pip install without --no-cache-dir")
        if re.search(r"(curl|wget)[^|]*\|\s*(ba)?sh", ln):
            findings.append(f"line {i}: piping a download into a shell (unverified code execution)")
        if up.startswith(("ENV", "ARG")) and _SECRET_NAME.search(ln) and "=" in ln:
            findings.append(f"line {i}: secret-like variable baked into the image; use build secrets or runtime env")
        if re.match(r"COPY\s+\.\s+", ln, re.I):
            findings.append(f"line {i}: COPY . copies the whole build context (ensure a .dockerignore excludes .git/.env)")
    users = [ln for _, ln in instructions if ln.upper().startswith("USER")]
    if not users or users[-1].split()[-1] in ("root", "0"):
        findings.append("container runs as root (add a non-root USER)")
    if not any(ln.upper().startswith("HEALTHCHECK") for _, ln in instructions):
        findings.append("no HEALTHCHECK instruction")
    stages = sum(ln.upper().startswith("FROM") for _, ln in instructions)
    return report("Dockerfile scan", findings, f"{len(instructions)} instructions, {stages} stage(s).")


_DOCKER_TEMPLATES = {
    "flask": ("python", "gunicorn -w 4 -b 0.0.0.0:{port} app:app"),
    "fastapi": ("python", "uvicorn main:app --host 0.0.0.0 --port {port}"),
    "django": ("python", "gunicorn -w 4 -b 0.0.0.0:{port} config.wsgi"),
    "node": ("node", "node server.js"),
}


@tool
def generate_dockerfile(framework: str, port: int = 8000) -> str:
    """Generate a production Dockerfile for flask, fastapi, django or node: multi-stage build, pinned slim
    base image, non-root user, no cache, HEALTHCHECK and exec-form CMD."""
    key = framework.strip().lower()
    if key not in _DOCKER_TEMPLATES:
        return needs_input("generate_dockerfile", f"framework one of {sorted(_DOCKER_TEMPLATES)}")
    lang, cmd = _DOCKER_TEMPLATES[key]
    cmd_json = ", ".join(f'"{p}"' for p in cmd.format(port=port).split())
    if lang == "python":
        return f"""# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /install /usr/local
COPY --chown=app:app . .
USER app
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:{port}/health')"
CMD [{cmd_json}]"""
    return f"""# syntax=docker/dockerfile:1
FROM node:20-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev

FROM node:20-slim
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY --chown=node:node . .
USER node
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=3s CMD node -e "fetch('http://localhost:{port}/health').then(r=>process.exit(r.ok?0:1))"
CMD [{cmd_json}]"""


# ── Kubernetes Agent ─────────────────────────────────────────────────
def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    spec = doc.get("spec") or {}
    return (spec.get("template") or {}).get("spec") or (spec if doc.get("kind") == "Pod" else {})


@tool
def validate_k8s_manifest(manifest: str) -> str:
    """Validate Kubernetes YAML (one or more documents): required fields, and for workloads missing
    resource requests/limits, readiness/liveness probes, runAsNonRoot, privilege escalation, unpinned image
    tags, single replicas and absence of a PodDisruptionBudget."""
    try:
        docs = [d for d in load_yaml_docs(manifest) if isinstance(d, dict)]
    except ValueError as exc:
        return needs_input("validate_k8s_manifest", str(exc))
    if not docs:
        return needs_input("validate_k8s_manifest", "Kubernetes YAML manifests")
    findings: list[str] = []
    kinds = [d.get("kind", "?") for d in docs]
    for d in docs:
        name = f"{d.get('kind', '?')}/{(d.get('metadata') or {}).get('name', '?')}"
        for field in ("apiVersion", "kind", "metadata"):
            if field not in d:
                findings.append(f"{name}: missing '{field}'")
        if d.get("kind") in ("Deployment", "StatefulSet") and int((d.get("spec") or {}).get("replicas", 1)) < 2:
            findings.append(f"{name}: fewer than 2 replicas (no redundancy)")
        pod = _pod_spec(d)
        pod_non_root = bool((pod.get("securityContext") or {}).get("runAsNonRoot"))
        for c in pod.get("containers") or []:
            cname = f"{name}/{c.get('name', '?')}"
            image = str(c.get("image", ""))
            if image.endswith(":latest") or (":" not in image.split("/")[-1] and "@" not in image):
                findings.append(f"{cname}: image '{image}' not pinned to a tag/digest")
            res = c.get("resources") or {}
            for part in ("requests", "limits"):
                if not res.get(part):
                    findings.append(f"{cname}: no resource {part}")
            for probe in ("readinessProbe", "livenessProbe"):
                if probe not in c:
                    findings.append(f"{cname}: no {probe}")
            sc = c.get("securityContext") or {}
            if not (sc.get("runAsNonRoot") or pod_non_root):
                findings.append(f"{cname}: securityContext.runAsNonRoot is not true")
            if sc.get("allowPrivilegeEscalation", True):
                findings.append(f"{cname}: allowPrivilegeEscalation not set to false")
    if any(k in ("Deployment", "StatefulSet") for k in kinds) and "PodDisruptionBudget" not in kinds:
        findings.append("no PodDisruptionBudget for the workload")
    return report("Kubernetes manifest validation", findings, f"documents: {', '.join(kinds)}.")


@tool
def generate_k8s_manifest(app_name: str, image: str, port: int = 8000, replicas: int = 3) -> str:
    """Generate production Kubernetes manifests for an app: Deployment (probes, resources, non-root security
    context), Service, HorizontalPodAutoscaler and PodDisruptionBudget. `image` must include a tag."""
    name = re.sub(r"[^a-z0-9-]", "-", app_name.strip().lower()).strip("-")
    if not name or ":" not in image.split("/")[-1]:
        return needs_input("generate_k8s_manifest", "app_name and an image with an explicit tag, e.g. repo/app:1.2.3")
    return f"""apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, labels: {{app: {name}}}}}
spec:
  replicas: {max(2, replicas)}
  selector: {{matchLabels: {{app: {name}}}}}
  template:
    metadata: {{labels: {{app: {name}}}}}
    spec:
      securityContext: {{runAsNonRoot: true, seccompProfile: {{type: RuntimeDefault}}}}
      containers:
        - name: {name}
          image: {image}
          ports: [{{containerPort: {port}}}]
          resources: {{requests: {{cpu: 250m, memory: 256Mi}}, limits: {{cpu: "1", memory: 512Mi}}}}
          readinessProbe: {{httpGet: {{path: /health, port: {port}}}, periodSeconds: 5}}
          livenessProbe: {{httpGet: {{path: /health, port: {port}}}, initialDelaySeconds: 10, periodSeconds: 10}}
          securityContext: {{allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {{drop: [ALL]}}}}
---
apiVersion: v1
kind: Service
metadata: {{name: {name}}}
spec: {{selector: {{app: {name}}}, ports: [{{port: 80, targetPort: {port}}}]}}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {{name: {name}}}
spec:
  scaleTargetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  minReplicas: {max(2, replicas)}
  maxReplicas: {max(2, replicas) * 4}
  metrics: [{{type: Resource, resource: {{name: cpu, target: {{type: Utilization, averageUtilization: 70}}}}}}]
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: {{name: {name}}}
spec: {{minAvailable: 1, selector: {{matchLabels: {{app: {name}}}}}}}"""


# ── CI/CD Agent ──────────────────────────────────────────────────────
@tool
def generate_ci_pipeline(stack: str, image_name: str) -> str:
    """Generate a GitHub Actions workflow for a 'python' or 'node' service: lint, test, build and push a
    SHA-tagged image to GHCR, then deploy to staging and (with approval) production via environments."""
    key = stack.strip().lower()
    if key not in ("python", "node") or not image_name.strip():
        return needs_input("generate_ci_pipeline", "stack 'python' or 'node' and an image_name")
    if key == "python":
        setup = """      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt ruff pytest
      - run: ruff check .
      - run: pytest -q"""
    else:
        setup = """      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
      - run: npm ci
      - run: npm run lint
      - run: npm test"""
    name = image_name.strip()
    image = f"ghcr.io/${{{{ github.repository_owner }}}}/{name}:${{{{ github.sha }}}}"
    deploy = f'kubectl set image deployment/{name} app="{image}"'
    return f"""name: ci-cd
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
  packages: write
  id-token: write
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
{setup}
  build:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}
      - uses: docker/build-push-action@v6
        with:
          push: true
          tags: {image}
          cache-from: type=gha
          cache-to: type=gha,mode=max
  deploy-staging:
    needs: build
    environment: staging
    runs-on: ubuntu-latest
    steps:
      - run: {deploy}
  deploy-production:
    needs: deploy-staging
    environment: production  # configure required reviewers on this environment
    runs-on: ubuntu-latest
    steps:
      - run: {deploy}"""


@tool
def check_pipeline_status(repo: str) -> str:
    """Fetch the latest GitHub Actions workflow runs for a repository ('owner/name') from the GitHub REST API
    and summarize pass/fail rate and durations. Uses GITHUB_TOKEN if set (required for private repos)."""
    if not _REPO.match(repo.strip()) or any(p in (".", "..") for p in repo.strip().split("/")):
        return needs_input("check_pipeline_status", "a GitHub repository as 'owner/name'")
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        data = http_get_json(f"https://api.github.com/repos/{repo.strip()}/actions/runs",
                             params={"per_page": 10}, headers=headers)
    except ToolUnavailable as exc:
        return unavailable("check_pipeline_status", str(exc))
    runs = data.get("workflow_runs") or []
    if not runs:
        return f"{repo}: no workflow runs found."
    lines, concl = [], []
    for r in runs:
        concl.append(r.get("conclusion") or r.get("status"))
        lines.append(f"- {r.get('name')} #{r.get('run_number')} on {r.get('head_branch')}: "
                     f"{r.get('conclusion') or r.get('status')} ({r.get('created_at')})")
    passed = concl.count("success")
    return f"{repo}: last {len(runs)} runs — {passed} success, {concl.count('failure')} failure.\n" + "\n".join(lines)
