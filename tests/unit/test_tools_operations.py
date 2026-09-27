"""AT-02 real behaviour of operations tools (QA, DevOps, Security) · AT-24 honest unavailability · AT-26 safety."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from hierarchy_company.tools import devops as ops
from hierarchy_company.tools import qa
from hierarchy_company.tools import security as sec

FAKE_AWS_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"  # AWS documentation example, split so secret scanners ignore it


# ── QA ───────────────────────────────────────────────────────────────
SOURCE = '''class Cart:
    def total(self, items, tax):
        if tax < 0:
            raise ValueError("negative")
        return sum(items)

async def refund(charge_id):
    raise PermissionError

def _private():
    pass
'''


def test_at02_unit_test_skeleton_from_ast() -> None:
    out = qa.generate_unit_test_skeleton.invoke({"source": SOURCE})
    assert "def test_cart_total_happy_path" in out and "pytest.raises(ValueError)" in out
    assert "async def test_refund_raises_permissionerror" in out and "_private" not in out
    compile(out.split("\n", 1)[1], "skeleton", "exec")  # generated code is valid Python


def test_at02_coverage_report(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text('<coverage line-rate="0.64" branch-rate="0.48"><packages><package><classes>'
                   '<class filename="pay/refund.py" line-rate="0.2"/><class filename="pay/charge.py" line-rate="0.9"/>'
                   '</classes></package></packages></coverage>')
    out = qa.measure_code_coverage.invoke({"coverage_xml_path": str(xml)})
    assert "lines 64.0%" in out and "branches 48.0%" in out and out.index("refund.py") < out.index("charge.py")
    assert qa.measure_code_coverage.invoke({"coverage_xml_path": str(tmp_path / "none.xml")}).startswith("UNAVAILABLE")


def test_at02_integration_points() -> None:
    out = qa.list_integration_points.invoke({"source": "r = requests.post(URL)\ns3 = boto3.client('s3')\n"
                                                       "conn = psycopg2.connect(DSN)\nsubprocess.run(['ls'])"})
    for kind in ("HTTP call", "AWS SDK", "SQL database", "Subprocess"):
        assert kind in out


def test_at02_fixture_generator_produces_valid_code() -> None:
    out = qa.create_test_fixture.invoke({"service": "Stripe", "endpoints": "GET /charges, post /refunds"})
    assert '("POST", "/refunds")' in out and "def stripe_client" in out
    compile(out, "fixture", "exec")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "one")
    (tmp_path / "a.py").write_text("x = 1\n" + "y = 2\n" * 300)
    (tmp_path / "b.py").write_text("z = 3\n")
    git("add", ".")
    git("commit", "-qm", "two")
    return tmp_path


def test_at02_changed_modules_from_real_git(git_repo: Path) -> None:
    out = qa.find_changed_modules.invoke({"repo_path": str(git_repo), "since_ref": "HEAD~1"})
    assert "HIGH   a.py (+300/-0)" in out and "LOW    b.py (+1/-0)" in out


def test_at26_git_ref_injection_blocked(git_repo: Path, tmp_path: Path) -> None:
    for ref in ("--output=/tmp/x", "HEAD;rm -rf /", "$(id)"):
        assert qa.find_changed_modules.invoke({"repo_path": str(git_repo), "since_ref": ref}).startswith("INPUT NEEDED")
    assert qa.find_changed_modules.invoke({"repo_path": str(tmp_path / "nope")}).startswith("UNAVAILABLE")


def test_at02_flaky_tests_from_junit(tmp_path: Path) -> None:
    for i, failed in enumerate([True, False, False, True]):
        body = '<failure message="x"/>' if failed else ""
        (tmp_path / f"run{i}.xml").write_text(
            f'<testsuite><testcase classname="t" name="flaky">{body}</testcase>'
            f'<testcase classname="t" name="stable"/></testsuite>')
    out = qa.get_flaky_test_history.invoke({"junit_dir": str(tmp_path)})
    assert "t::flaky: failed 2/4 runs (50%)" in out and "stable" not in out.split("finding")[-1]
    assert qa.get_flaky_test_history.invoke({"junit_dir": str(tmp_path / "x")}).startswith("UNAVAILABLE")


# ── DevOps ───────────────────────────────────────────────────────────
def test_at02_dockerfile_scan_rules() -> None:
    df = "FROM python\nADD . /app\nENV API_KEY=abc\nRUN apt-get install -y curl\nRUN pip install x\n" \
         "RUN curl -s https://x.sh | sh"
    out = ops.scan_dockerfile.invoke({"dockerfile": df})
    for expected in ("not pinned", "COPY instead of ADD", "secret-like variable", "--no-install-recommends",
                     "--no-cache-dir", "piping a download", "runs as root", "no HEALTHCHECK"):
        assert expected in out


@pytest.mark.parametrize("framework", ["flask", "fastapi", "django", "node"])
def test_at02_generated_dockerfiles_pass_own_scanner(framework: str) -> None:
    df = ops.generate_dockerfile.invoke({"framework": framework, "port": 9000})
    assert "EXPOSE 9000" in df
    out = ops.scan_dockerfile.invoke({"dockerfile": df})
    assert "no issues found" in out or set(out.split("\n")[1:]) <= {
        x for x in out.split("\n")[1:] if "COPY . copies" in x}


def test_at02_k8s_validation_and_generator_roundtrip() -> None:
    bad = "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\nspec:\n  template:\n    spec:\n" \
          "      containers: [{name: api, image: api}]"
    out = ops.validate_k8s_manifest.invoke({"manifest": bad})
    for expected in ("fewer than 2 replicas", "not pinned", "no resource limits", "no readinessProbe",
                     "runAsNonRoot", "no PodDisruptionBudget"):
        assert expected in out
    generated = ops.generate_k8s_manifest.invoke({"app_name": "api", "image": "ghcr.io/x/api:1.0.0"})
    assert "no issues found" in ops.validate_k8s_manifest.invoke({"manifest": generated})
    assert ops.generate_k8s_manifest.invoke({"app_name": "api", "image": "api"}).startswith("INPUT NEEDED")


def test_at02_ci_pipeline_is_valid_yaml() -> None:
    import yaml

    wf = yaml.safe_load(ops.generate_ci_pipeline.invoke({"stack": "python", "image_name": "api"}))
    assert set(wf["jobs"]) == {"test", "build", "deploy-staging", "deploy-production"}
    assert wf["jobs"]["deploy-production"]["environment"] == "production"


class _Resp:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code, self._payload = status, payload

    def json(self) -> dict:
        return self._payload


def test_at02_pipeline_status_parses_github_response(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = {"workflow_runs": [{"name": "CI", "run_number": 7, "head_branch": "main", "conclusion": "success",
                               "created_at": "t1"},
                              {"name": "CI", "run_number": 6, "head_branch": "main", "conclusion": "failure",
                               "created_at": "t0"}]}
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, headers=kwargs.get("headers"))
        return _Resp(200, runs)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    out = ops.check_pipeline_status.invoke({"repo": "acme/api"})
    assert "1 success, 1 failure" in out and "CI #7 on main: success" in out
    assert seen["url"].endswith("/repos/acme/api/actions/runs") and seen["headers"]["Authorization"] == "Bearer t0ken"


def test_at24_pipeline_status_unavailable_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    assert ops.check_pipeline_status.invoke({"repo": "acme/api"}).startswith("UNAVAILABLE")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(404, {}))
    assert ops.check_pipeline_status.invoke({"repo": "acme/api"}).startswith("UNAVAILABLE")


def test_at26_repo_slug_validated() -> None:
    for repo in ("../etc", "acme", "acme/api/../x", "a b/c"):
        assert ops.check_pipeline_status.invoke({"repo": repo}).startswith("INPUT NEEDED")


# ── Security ─────────────────────────────────────────────────────────
def test_at02_dependency_scan_queries_osv(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}

    def fake_post(url, json=None, **kwargs):
        sent.update(url=url, json=json)
        return _Resp(200, {"results": [{"vulns": [{"id": "GHSA-j8r2-6x86-q33q"}]}, {}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = sec.scan_dependencies.invoke({"manifest": "requests==2.25.1\nflask==3.0.0\ndjango>=4\n# c"})
    assert sent["url"] == "https://api.osv.dev/v1/querybatch"
    assert sent["json"]["queries"][0] == {"package": {"name": "requests", "ecosystem": "PyPI"}, "version": "2.25.1"}
    assert "requests==2.25.1: 1 advisory(ies): GHSA-j8r2-6x86-q33q" in out and "flask" not in out.split("finding")[1]
    assert "Unchecked (unpinned): django" in out


def test_at02_dependency_scan_npm_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}
    def fake_post(url, json=None, **kwargs):
        sent.update(json=json)
        return _Resp(200, {"results": [{}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = sec.scan_dependencies.invoke({"manifest": json.dumps({"dependencies": {"lodash": "^4.17.20", "x": "*"}})})
    assert sent["json"]["queries"][0]["package"] == {"name": "lodash", "ecosystem": "npm"}
    assert "no issues found" in out and "Unchecked (unpinned): x" in out


def test_at24_dependency_scan_unavailable_when_osv_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(httpx, "post", boom)
    assert sec.scan_dependencies.invoke({"manifest": "requests==2.25.1"}).startswith("UNAVAILABLE")


def test_at02_vulnerability_lookup_follows_alias_for_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    records = {
        "CVE-2023-32681": {"id": "CVE-2023-32681", "summary": "Proxy header leak", "aliases": ["GHSA-j8r2-6x86-q33q"],
                           "affected": [{"ranges": [{"type": "GIT", "events": [{"fixed": "abc123"}]}]}]},
        "GHSA-j8r2-6x86-q33q": {"affected": [{"package": {"ecosystem": "PyPI", "name": "requests"},
                                              "ranges": [{"type": "ECOSYSTEM", "events": [{"fixed": "2.31.0"}]}]}]},
    }
    monkeypatch.setattr(httpx, "get", lambda url, **k: _Resp(200, records[url.rsplit("/", 1)[1]]))
    out = sec.lookup_vulnerability.invoke({"vuln_id": "CVE-2023-32681"})
    assert "Proxy header leak" in out and "PyPI/requests fixed in 2.31.0" in out and "abc123" not in out
    assert sec.lookup_vulnerability.invoke({"vuln_id": "../../etc"}).startswith("INPUT NEEDED")


def test_at02_iam_policy_analysis() -> None:
    policy = {"Statement": [
        {"Sid": "Admin", "Effect": "Allow", "Action": "*", "Resource": "*"},
        {"Sid": "Esc", "Effect": "Allow", "Action": ["iam:PassRole", "secretsmanager:GetSecretValue"], "Resource": "*"},
        {"Sid": "Scoped", "Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::b/*"},
        {"Sid": "Deny", "Effect": "Deny", "Action": "*", "Resource": "*"}]}
    out = sec.analyze_iam_policy.invoke({"policy_json": json.dumps(policy)})
    assert "Admin: Action '*' is full administrator access" in out and "services reachable: ALL services" in out
    assert "Esc: privilege-escalation actions ['iam:passrole']" in out and "Esc: data-read actions" in out
    assert "Scoped" not in out and "Deny" not in out
    assert sec.analyze_iam_policy.invoke({"policy_json": "{nope"}).startswith("INPUT NEEDED")


def test_at24_access_key_lookup_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3
    from botocore.exceptions import NoCredentialsError

    class _Client:
        def get_access_key_last_used(self, **kw):
            raise NoCredentialsError()

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Client())
    out = sec.get_access_key_last_used.invoke({"access_key_id": FAKE_AWS_KEY_ID})
    assert out.startswith("UNAVAILABLE") and "no AWS credentials" in out
    assert sec.get_access_key_last_used.invoke({"access_key_id": "AKIA123"}).startswith("INPUT NEEDED")


def test_at02_access_key_lookup_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    class _Client:
        def get_access_key_last_used(self, **kw):
            return {"UserName": "ci-deploy", "AccessKeyLastUsed": {"LastUsedDate": "2026-09-27", "ServiceName": "s3",
                                                                    "Region": "us-east-1"}}

        def list_attached_user_policies(self, **kw):
            return {"AttachedPolicies": [{"PolicyName": "AmazonS3FullAccess"}]}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Client())
    out = sec.get_access_key_last_used.invoke({"access_key_id": FAKE_AWS_KEY_ID})
    assert "owner ci-deploy" in out and "service s3" in out and "AmazonS3FullAccess" in out
    assert FAKE_AWS_KEY_ID not in out  # key id is redacted in output


def test_at02_secret_scanner_detects_and_redacts() -> None:
    text = (f"aws_access_key_id = {FAKE_AWS_KEY_ID}\n"
            "db_password = 'Xk9#pL2$vQ8!mZ'\n"
            "url = postgres://admin:hunter22@db:5432/app\n"
            "GITHUB_TOKEN=ghp_" + "a1B2" * 9 + "\n"
            "timeout = 30\npassword_hint = 'aaaaaaaaaa'\n")
    out = sec.scan_for_secrets.invoke({"text": text})
    assert "line 1: AWS access key id" in out and "line 2: high-entropy value" in out
    assert "line 3: Password in URL" in out and "line 4: GitHub token" in out
    assert "line 5" not in out and "line 6" not in out
    assert FAKE_AWS_KEY_ID not in out and "hunter22" not in out


def test_at02_rotation_runbooks() -> None:
    out = sec.get_rotation_runbook.invoke({"secret_type": "AWS key"})
    assert "update-access-key" in out and "--status Inactive" in out
    assert sec.get_rotation_runbook.invoke({"secret_type": "unicorn"}).startswith("INPUT NEEDED")
