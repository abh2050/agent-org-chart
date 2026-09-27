"""`hierarchy-company-ui`: start the Streamlit app with the project theme."""

from __future__ import annotations

import sys
from pathlib import Path

APP = Path(__file__).with_name("app.py")
THEME = {
    "theme.primaryColor": "#0B7C6E",
    "theme.font": "sans serif",
    "browser.gatherUsageStats": "false",
    "client.toolbarMode": "minimal",   # no Deploy button for a local tool
    "server.maxUploadSize": "1",       # MB; only the first 50 KB of each file is sent anyway
}


def main() -> None:
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        sys.exit('Streamlit is not installed. Run: pip install -e ".[ui]"')
    flags = [f"--{k}={v}" for k, v in THEME.items()]
    sys.argv = ["streamlit", "run", str(APP), *flags, *sys.argv[1:]]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
