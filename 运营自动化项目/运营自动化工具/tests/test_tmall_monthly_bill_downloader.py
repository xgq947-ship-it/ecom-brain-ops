from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.tmall_monthly_bill import downloader


def test_bill_downloader_enables_interactive_recovery(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run_ops_json(args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return {"success": True, "platform": "tmcs", "command": "bill download"}

    monkeypatch.setattr(downloader, "run_ops_json", fake_run_ops_json)
    monkeypatch.setattr(sys, "argv", ["downloader", "--last-month"])

    assert downloader.main() == 0
    assert observed["kwargs"] == {"interactive_recovery": True}
