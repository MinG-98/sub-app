#!/usr/bin/env python3
"""Run one bounded Nezha collection pass."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.models import make_session_factory  # noqa: E402
from app.traffic import run_collector  # noqa: E402


def main():
    db_path = os.environ.get("SUB_APP_DB", os.path.join(ROOT, "data.db"))
    session_factory = make_session_factory(db_path)
    db = session_factory()
    try:
        result = run_collector(db)
        print(json.dumps({"ok": True, **result}, ensure_ascii=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
