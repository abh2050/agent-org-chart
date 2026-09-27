"""hierarchy_company: a 4-level hierarchical multi-agent system on LangGraph."""

from hierarchy_company.config import Settings
from hierarchy_company.factory import Organization, build_company
from hierarchy_company.runner import RunEvent, RunResult, append_attachments, run_company, stream_company

__version__ = "2.1.0"

__all__ = [
    "Organization", "RunEvent", "RunResult", "Settings", "__version__",
    "append_attachments", "build_company", "run_company", "stream_company",
]
