from __future__ import annotations

import os
from pathlib import Path

BASE_URL = "https://sminfo.mss.go.kr"
SEARCH_PATH = "/gc/sf/GSF002R0.print"
SEARCH_MENU_ID = "421010100"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / ".data"
DEFAULT_STATE_PATH = Path(
    os.getenv("SMINFO_STATE_PATH", str(DATA_DIR / "storage_state.json"))
)
DEFAULT_TIMEOUT_MS = int(os.getenv("SMINFO_TIMEOUT_MS", "45000"))
DEFAULT_BROWSER_CHANNEL = os.getenv("SMINFO_BROWSER_CHANNEL", "chrome").strip()
