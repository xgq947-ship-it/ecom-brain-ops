from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "send_daily_profit_weixin.py"
SPEC = importlib.util.spec_from_file_location("send_daily_profit_weixin", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_weixin_daily_profit_entry_is_deprecated() -> None:
    assert MODULE.main() == 2
