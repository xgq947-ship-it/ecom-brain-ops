"""pytest 全局夹具。"""

from __future__ import annotations

import pytest

from core.notchflow_reporter import notchflow as _notchflow


@pytest.fixture(autouse=True)
def _silence_notchflow(monkeypatch):
    """测试绝不写真实 NotchFlow inbox：统一把 App 判活置为 False（等价于 App 没开）。

    历史 bug：test_run_workflow_sms_recovery 等用例调真实 `run_workflow`，其内部走
    run.py 的 `nf.failed / nf.waiting`。若用户本机 NotchFlow App 正在运行，flock 判活=在跑，
    就会把测试用的假事件（如「聚水潭揽收监控 FAILED 网络超时」「聚水潭商品利润宝贝分析
    WAITING 等待短信验证完成」）真写进生产 inbox 并弹到刘海。

    `run.notchflow` 与 `core.notchflow_reporter.notchflow` 是同一个单例，这里只需 patch 一处的
    `_app_running`，即可让所有上报路径在测试期间全部静默 no-op。autouse + monkeypatch 自动回滚。
    """
    monkeypatch.setattr(_notchflow, "_app_running", lambda: False)
