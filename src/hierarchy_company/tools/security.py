"""Security team tools: OSV.dev vulnerability data, IAM policy analysis, AWS key usage, secret detection."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from langchain_core.tools import tool

from hierarchy_company.tools._common import (
    ToolUnavailable,
    http_get_json,
    http_post_json,
    needs_input,
    report,
    unavailable,
)

OSV = "https://api.osv.dev/v1"
_VULN_ID = re.compile(r"^(CVE-\d{4}-\d{4,}|GHSA(-[23456789cfghjmpqrvwx]{4}){3}|PYSEC-\d{4}-\d+|[A-Z]+-\d{4}-\d+)$", re.I)
_ACCESS_KEY_ID = re.compile(r"^(AKIA|ASIA)[A-Z0-9]{16}$")


# ── Vulnerability Agent ──────────────────────────────────────────────
def _parse_manifest(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return ([(ecosystem, name, version)], [unpinned names]) from requirements.txt or package.json text."""
    pinned: list[tuple[str, str, str]] = []
    unpinned: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
        for section in ("dependencies", "devDependencies"):
            for name, spec in (data.get(section) or {}).items():
                m = re.fullmatch(r"[\^~=]?\s*(\d+\.\d+\.\d+[\w.-]*)", str(spec))
                (pinned.append(("npm", name, m.group(1))) if m else unpinned.append(name))
        return pinned, unpinned
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.fullmatch(r"([A-Za-z0-9_.\[\]-]+?)(\[[^\]]*\])?\s*==\s*([\w.+-]+)", line)
        if m:
            pinned.append(("PyPI", m.group(1), m.group(3)))
        else:
            unpinned.append(re.split(r"[<>=!~ ;\[]", line, maxsplit=1)[0])
    return pinned, unpinned


@tool
def scan_dependencies(manifest: str) -> str:
    """Check pinned dependencies from requirements.txt text or package.json text against the OSV.dev
    vulnerability database (live, no key needed) and list known advisories per package. Unpinned
    dependencies are reported as unchecked."""
    try:
        pinned, unpinned = _parse_manifest(manifest)
    except json.JSONDecodeError:
        return needs_input("scan_dependencies", "requirements.txt lines (pkg==x.y.z) or package.json text")
    if not pinned:
        return needs_input("scan_dependencies", "at least one pinned dependency (pkg==x.y.z or \"pkg\": \"x.y.z\")")
    queries = [{"package": {"name": n, "ecosystem": e}, "version": v} for e, n, v in pinned[:100]]
    try:
        results = http_post_json(f"{OSV}/querybatch", {"queries": queries}).get("results", [])
    except ToolUnavailable as exc:
        return unavailable("scan_dependencies", str(exc))
    findings = []
    for (_eco, name, version), res in zip(pinned, results, strict=False):
        ids = [v["id"] for v in res.get("vulns") or []]
        if ids:
            findings.append(f"{name}=={version}: {len(ids)} advisory(ies): {', '.join(ids[:8])}"
                            + (" …" if len(ids) > 8 else ""))
    summary = f"{len(pinned)} pinned package(s) checked against OSV.dev."
    if unpinned:
        summary += f" Unchecked (unpinned): {', '.join(unpinned[:10])}."
    return report("Dependency vulnerability scan", findings, summary)


@tool
def lookup_vulnerability(vuln_id: str) -> str:
    """Look up a vulnerability by ID (CVE-…, GHSA-…, PYSEC-…) in OSV.dev (live) and return its summary,
    severity, aliases, affected packages and the fixed versions."""
    vid = vuln_id.strip()
    if not _VULN_ID.match(vid):
        return needs_input("lookup_vulnerability", "a vulnerability ID such as CVE-2023-32681 or GHSA-j8r2-6x86-q33q")
    try:
        v = http_get_json(f"{OSV}/vulns/{vid}")
        if not any(a.get("package") for a in v.get("affected") or []):
            # NVD-sourced CVE records carry no package data; follow the first GHSA/PYSEC alias for it
            alias = next((a for a in v.get("aliases") or [] if a.startswith(("GHSA-", "PYSEC-"))), None)
            if alias:
                v = {**v, "affected": http_get_json(f"{OSV}/vulns/{alias}").get("affected", [])}
    except ToolUnavailable as exc:
        return unavailable("lookup_vulnerability", str(exc))
    severity = ", ".join(f"{s.get('type')} {s.get('score')}" for s in v.get("severity") or []) or \
        (v.get("database_specific") or {}).get("severity", "not rated")
    affected = []
    for a in v.get("affected") or []:
        pkg = a.get("package")
        if not pkg:
            continue
        fixed = [e["fixed"] for r in a.get("ranges") or [] if r.get("type") != "GIT"
                 for e in r.get("events") or [] if "fixed" in e]
        affected.append(f"{pkg.get('ecosystem')}/{pkg.get('name')} fixed in {', '.join(fixed) or 'no fix listed'}")
    return (f"{v.get('id')}: {v.get('summary') or (v.get('details') or '')[:300]}\n"
            f"Severity: {severity}\nAliases: {', '.join(v.get('aliases') or []) or 'none'}\n"
            f"Affected: {'; '.join(affected[:6]) or 'not listed'}\nPublished: {v.get('published', '?')}")


# ── IAM Agent ────────────────────────────────────────────────────────
_PRIV_ESC = {"iam:passrole", "iam:createaccesskey", "iam:attachuserpolicy", "iam:attachrolepolicy",
             "iam:putuserpolicy", "iam:putrolepolicy", "iam:createpolicyversion", "iam:updateassumerolepolicy",
             "sts:assumerole", "lambda:createfunction", "iam:*"}
_DATA_READ = {"s3:getobject", "s3:*", "secretsmanager:getsecretvalue", "ssm:getparameter", "ssm:getparameters",
              "kms:decrypt", "dynamodb:scan", "rds:*"}


def _as_list(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


@tool
def analyze_iam_policy(policy_json: str) -> str:
    """Statically analyze an AWS IAM policy document (JSON): wildcard actions/resources, privilege-escalation
    actions, broad data-read permissions, NotAction/NotResource and missing conditions; then summarize the
    blast radius (services and resources reachable)."""
    try:
        policy = json.loads(policy_json)
    except json.JSONDecodeError:
        return needs_input("analyze_iam_policy", "an IAM policy document as JSON")
    statements = policy.get("Statement") if isinstance(policy, dict) else None
    if statements is None:
        return needs_input("analyze_iam_policy", "a policy with a Statement field")
    statements = [statements] if isinstance(statements, dict) else statements
    findings: list[str] = []
    services: set[str] = set()
    for n, st in enumerate(statements, 1):
        if st.get("Effect") != "Allow":
            continue
        sid = st.get("Sid") or f"#{n}"
        actions = [a.lower() for a in _as_list(st.get("Action"))]
        resources = _as_list(st.get("Resource"))
        services |= {a.split(":")[0] for a in actions}
        if "NotAction" in st or "NotResource" in st:
            findings.append(f"{sid}: Allow with NotAction/NotResource grants everything not listed")
        if "*" in actions:
            findings.append(f"{sid}: Action '*' is full administrator access")
        wild_svc = [a for a in actions if a.endswith(":*")]
        if wild_svc:
            findings.append(f"{sid}: service-wide wildcards {wild_svc}")
        star_res = "*" in resources
        esc = sorted({a for a in actions if a in _PRIV_ESC or a == "*"})
        if esc and star_res:
            findings.append(f"{sid}: privilege-escalation actions {esc} on Resource '*'")
        reads = sorted({a for a in actions if a in _DATA_READ})
        if reads and star_res:
            findings.append(f"{sid}: data-read actions {reads} on every resource")
        if star_res and not st.get("Condition"):
            findings.append(f"{sid}: Resource '*' with no Condition (no IP/MFA/tag restriction)")
    radius = "ALL services" if "*" in services else ", ".join(sorted(services)) or "none"
    return report("IAM policy analysis", findings, f"{len(statements)} statement(s); services reachable: {radius}.")


@tool
def get_access_key_last_used(access_key_id: str) -> str:
    """Query AWS IAM (live, via boto3 and the default credential chain) for an access key's owner, last-used
    time/service/region and the owner's attached policies. Requires AWS credentials with IAM read access."""
    key = access_key_id.strip()
    if not _ACCESS_KEY_ID.match(key):
        return needs_input("get_access_key_last_used", "a full AWS access key id (AKIA… or ASIA…, 20 characters)")
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        return unavailable("get_access_key_last_used", "boto3 is not installed (pip install boto3)")
    try:
        iam = boto3.client("iam")
        info = iam.get_access_key_last_used(AccessKeyId=key)
        user = info.get("UserName")
        last = info.get("AccessKeyLastUsed") or {}
        policies = iam.list_attached_user_policies(UserName=user).get("AttachedPolicies", []) if user else []
    except NoCredentialsError:
        return unavailable("get_access_key_last_used", "no AWS credentials configured")
    except (ClientError, BotoCoreError) as exc:
        return unavailable("get_access_key_last_used", f"AWS error: {str(exc)[:160]}")
    return (f"Key {key[:4]}…{key[-4:]}: owner {user or 'unknown'}; last used {last.get('LastUsedDate', 'never')} "
            f"service {last.get('ServiceName', 'N/A')} region {last.get('Region', 'N/A')}.\n"
            f"Attached policies: {', '.join(p['PolicyName'] for p in policies) or 'none'}")


# ── Secrets Agent ────────────────────────────────────────────────────
_DETECTORS = {
    "AWS access key id": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "AWS secret access key": r"(?i)aws.{0,20}(?:secret|private).{0,20}['\"=:\s]([A-Za-z0-9/+=]{40})\b",
    "GitHub token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{80,}\b",
    "Slack token/webhook": r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b|https://hooks\.slack\.com/services/[A-Za-z0-9/]+",
    "OpenAI API key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
    "Stripe live key": r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b",
    "Private key block": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
    "Password in URL": r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s@/]{3,})@",
}
_GENERIC = re.compile(
    r"(?i)(?<![a-z])(secret|token|passw(?:or)?d|api[_-]?key)\w*\s*[:=]\s*(?:['\"]([^'\"\n]{8,})['\"]|([^\s'\"#]{8,}))")


def _entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _redact(value: str) -> str:
    return value[:4] + "…" + value[-2:] if len(value) > 8 else "…"


@tool
def scan_for_secrets(text: str) -> str:
    """Scan text, code or config for leaked credentials using provider-specific patterns (AWS, GitHub, Slack,
    OpenAI, Stripe, private keys, passwords in URLs) plus high-entropy assignments to secret-named variables.
    Matches are reported with line numbers and redacted values."""
    if not text.strip():
        return needs_input("scan_for_secrets", "text or code to scan")
    findings: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, pattern in _DETECTORS.items():
            for m in re.finditer(pattern, line):
                findings.append(f"line {n}: {kind} ({_redact(m.group(m.lastindex or 0))})")
        for m in _GENERIC.finditer(line):
            value = m.group(2) or m.group(3)
            if _entropy(value) >= 3.5 and not any(value in f for f in findings):
                findings.append(f"line {n}: high-entropy value assigned to '{m.group(1)}' ({_redact(value)})")
    return report("Secret scan", findings, f"{len(text.splitlines())} line(s) scanned.")


_RUNBOOKS = {
    "aws_access_key": [
        "Deactivate now: aws iam update-access-key --access-key-id <ID> --status Inactive --user-name <USER>",
        "Review use: CloudTrail lookup-events --lookup-attributes AttributeKey=AccessKeyId,AttributeValue=<ID>",
        "Remove attacker persistence: new IAM users/keys/roles, EC2 instances, Lambda functions created by the key",
        "Issue a replacement only if still needed; prefer IAM roles/OIDC for CI instead of long-lived keys",
        "Delete the old key: aws iam delete-access-key --access-key-id <ID> --user-name <USER>",
        "Purge from git history (git filter-repo), then force-push and invalidate forks/caches",
    ],
    "github_token": [
        "Revoke at github.com/settings/tokens (or the org's credential inventory)",
        "Review the org audit log for actions by the token since exposure",
        "Replace with a fine-grained token scoped to specific repositories with an expiry, or a GitHub App",
    ],
    "database_password": [
        "Rotate: ALTER ROLE <role> WITH PASSWORD '<new>'; update the secret store and roll dependent services",
        "Terminate existing sessions of the role (pg_terminate_backend) and review connection logs",
        "Move the credential into a secret manager with automatic rotation",
    ],
    "slack_webhook": ["Regenerate the webhook URL in the Slack app settings", "Update consumers; delete the old URL"],
    "private_key": [
        "Revoke/replace the key pair and any certificates issued for it",
        "Remove the public key from authorized_keys / deploy keys / cloud key pairs",
        "Audit access logs for use of the key since exposure",
    ],
}


@tool
def get_rotation_runbook(secret_type: str) -> str:
    """Return the step-by-step containment and rotation runbook for a leaked secret type: aws_access_key,
    github_token, database_password, slack_webhook or private_key."""
    key = re.sub(r"\W+", "_", secret_type.strip().lower())
    aliases = {"aws": "aws_access_key", "aws_key": "aws_access_key", "github": "github_token",
               "db_password": "database_password", "password": "database_password", "ssh_key": "private_key"}
    key = aliases.get(key, key)
    if key not in _RUNBOOKS:
        return needs_input("get_rotation_runbook", f"secret_type one of {sorted(_RUNBOOKS)}")
    return f"Runbook ({key}):\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(_RUNBOOKS[key], 1))
