"""Read Lite's projects with the full app's own reader.

``project_parity`` keeps what Lite wrote in ``parity/out/projects/lite``
when ``CALLISTO_KEEP_PROJECTS`` is set. Each must open with
``read_project``, its ``meta.json`` must be exactly what ``json.dumps``
writes for it, and its arrays must load without pickles.

usage: python lite/parity/check_lite_projects.py
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.backend.session.project_session import read_project  # noqa: E402

folder = REPO / "lite" / "parity" / "out" / "projects" / "lite"
failures = 0
for path in sorted(folder.glob("*.efaproj")):
    payload = read_project(str(path))
    raw = zipfile.ZipFile(path).read("meta.json").decode("utf-8")
    again = json.dumps(json.loads(raw), indent=2, sort_keys=True, ensure_ascii=False)
    same = raw == again
    failures += not same
    print(f"{path.name}: {len(payload.arrays)} arrays, meta {'identical' if same else 'DIFFERS'} to json.dumps")
sys.exit(1 if failures else 0)
