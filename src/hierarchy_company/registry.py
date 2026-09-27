"""The org chart as immutable data (FR-01, ADR-03). Graphs are generated from this registry."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import BaseTool

from hierarchy_company.tools import backend as be
from hierarchy_company.tools import database as db
from hierarchy_company.tools import devops as ops
from hierarchy_company.tools import frontend as fe
from hierarchy_company.tools import qa
from hierarchy_company.tools import security as sec


@dataclass(frozen=True)
class SpecialistSpec:
    name: str                       # display name used in notes and trace, e.g. "IAM Agent"
    role: str                       # scope, used in prompts and routing option lists
    tools: tuple[BaseTool, ...]


@dataclass(frozen=True)
class TeamSpec:
    key: str                        # routing key, e.g. "security_team"
    label: str                      # human label, e.g. "Security"
    description: str                # one-line routing description (FR-11)
    specialists: tuple[SpecialistSpec, ...]

    @property
    def supervisor_label(self) -> str:
        return f"{self.label} Supervisor"

    @property
    def specialist_names(self) -> list[str]:
        return [s.name for s in self.specialists]


@dataclass(frozen=True)
class DivisionSpec:
    key: str                        # routing key, e.g. "operations"
    description: str
    teams: tuple[TeamSpec, ...]

    @property
    def director_label(self) -> str:
        return f"{self.key.capitalize()} Director"

    @property
    def team_keys(self) -> list[str]:
        return [t.key for t in self.teams]


@dataclass(frozen=True)
class Org:
    divisions: tuple[DivisionSpec, ...]

    @property
    def teams(self) -> list[TeamSpec]:
        return [t for d in self.divisions for t in d.teams]

    def division(self, key: str) -> DivisionSpec:
        return next(d for d in self.divisions if d.key == key)

    def team(self, key: str) -> TeamSpec:
        return next(t for t in self.teams if t.key == key)

    def division_of(self, team_key: str) -> str:
        return next(d.key for d in self.divisions if team_key in d.team_keys)


def _s(name: str, role: str, *tools: BaseTool) -> SpecialistSpec:
    return SpecialistSpec(name, role, tools)


ORG = Org(divisions=(
    DivisionSpec(
        "engineering",
        "building software — frontend UI/React/accessibility, backend APIs/auth/microservices, "
        "databases/schemas/queries",
        (
            TeamSpec("frontend_team", "Frontend", "UI, React components, accessibility, client-side rendering", (
                _s("React Agent", "React components, hooks, state management and re-render performance",
                   fe.lint_react_component, fe.analyze_render_triggers),
                _s("UI Agent", "visual layout, responsive CSS and design tokens",
                   fe.audit_css_layout, fe.generate_design_tokens),
                _s("Accessibility Agent", "WCAG compliance, color contrast, ARIA and keyboard navigation",
                   fe.check_wcag_contrast, fe.audit_html_accessibility),
            )),
            TeamSpec("backend_team", "Backend",
                     "REST APIs, authentication/authorization (JWT, OAuth), microservice design", (
                _s("API Agent", "REST API design, endpoints, status codes and OpenAPI specs",
                   be.design_rest_endpoints, be.validate_openapi_spec),
                _s("Auth Agent", "authentication and authorization: JWT, OAuth, sessions, token lifecycles",
                   be.generate_jwt_config, be.inspect_jwt),
                _s("Microservice Agent", "service boundaries, inter-service dependencies and resilience",
                   be.map_service_dependencies, be.check_service_resilience),
            )),
            TeamSpec("database_team", "Database", "SQL queries, schema design, indexes, slow-query optimization", (
                _s("SQL Agent", "writing and rewriting SQL queries and reading execution plans",
                   db.analyze_sql_query, db.explain_sql_query),
                _s("Schema Agent", "table design, data types, constraints and indexes",
                   db.inspect_table_schema, db.suggest_indexes),
                _s("Query Optimization Agent", "slow-query diagnosis and measuring the impact of index changes",
                   db.analyze_explain_plan, db.get_slow_query_stats),
            )),
        ),
    ),
    DivisionSpec(
        "operations",
        "shipping and protecting software — QA/testing, DevOps (Docker, Kubernetes, CI/CD), "
        "security (vulnerabilities, IAM, secrets)",
        (
            TeamSpec("qa_team", "QA", "unit, integration and regression testing, test plans, release quality", (
                _s("Unit Test Agent", "unit tests, test skeletons and code coverage",
                   qa.generate_unit_test_skeleton, qa.measure_code_coverage),
                _s("Integration Test Agent", "integration tests, external-service stubs and test fixtures",
                   qa.list_integration_points, qa.create_test_fixture),
                _s("Regression Agent", "regression test planning, change-risk analysis and flaky tests",
                   qa.find_changed_modules, qa.get_flaky_test_history),
            )),
            TeamSpec("devops_team", "DevOps", "Docker containers, Kubernetes deployment, CI/CD pipelines", (
                _s("Docker Agent", "containerizing applications and hardening Dockerfiles",
                   ops.generate_dockerfile, ops.scan_dockerfile),
                _s("Kubernetes Agent", "Kubernetes manifests, deployments, scaling and cluster policies",
                   ops.generate_k8s_manifest, ops.validate_k8s_manifest),
                _s("CI/CD Agent", "build/test/deploy pipelines and pipeline health",
                   ops.generate_ci_pipeline, ops.check_pipeline_status),
            )),
            TeamSpec("security_team", "Security", "vulnerabilities, IAM/permissions, secrets and credential leaks", (
                _s("Vulnerability Agent", "dependency vulnerabilities and CVE assessment",
                   sec.scan_dependencies, sec.lookup_vulnerability),
                _s("IAM Agent", "cloud IAM permissions, access-key usage and blast-radius analysis",
                   sec.analyze_iam_policy, sec.get_access_key_last_used),
                _s("Secrets Agent", "leaked credentials, secret scanning and secret rotation",
                   sec.scan_for_secrets, sec.get_rotation_runbook),
            )),
        ),
    ),
))
