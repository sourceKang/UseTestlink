from __future__ import annotations

import json
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from testlink_agent_core.token_budget import build_token_budget_report

    print(json.dumps(build_token_budget_report(root), ensure_ascii=False, indent=2))
