from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import resolve_task

from workflows.tmcs_price_competitiveness_lookup import cache, steps
from workflows.tmcs_price_competitiveness_lookup.workflow import build_workflow


def _rows(*codes_titles) -> list:
    return [
        {"item_id": code, "sku_id": f"sku-{code}", "title": title}
        for code, title in codes_titles
    ]


def _list_payload(rows: list, *, simulated: bool = False, list_date: str | None = None) -> dict:
    return {
        "success": True,
        "platform": "tmcs",
        "command": "price-competitiveness list",
        "data": {
            "rows": rows,
            "total_rows": len(rows),
            "list_date": list_date or date.today().isoformat(),
            "captured_at": "2026-06-22T00:00:00",
            "source": "simulated" if simulated else "page",
            "simulated": simulated,
            "scene": "tmall_chaoshi/price_competitiveness_lookup",
            "screenshot_path": None,
            "dry_run": simulated,
            "artifacts": [],
            "context_path": "/tmp/x.json",
        },
    }


def _run(monkeypatch, tmp_path: Path, args: list[str], *, dry_run: bool, payload):
    seen: list = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen.append((list(command), interactive_recovery))
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)
    # 缓存目录隔离到 tmp，避免读写真实 runtime 缓存
    cache_dir = tmp_path / "pc_cache"
    monkeypatch.setattr(cache, "cache_dir", lambda: cache_dir)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(build_workflow(), inputs={"dry_run": dry_run, "args": args}, dry_run=dry_run)
    return run, seen, runner, cache_dir


def _step_outputs(runner: WorkflowRunner, step_id: str) -> dict:
    path = runner.last_run_dir / "steps" / f"{step_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["outputs"]


# 1. workflow 可以注册（新步骤名）
def test_workflow_registers() -> None:
    wf = discover_workflow("tmcs_price_competitiveness_lookup")
    assert wf.id == "tmcs_price_competitiveness_lookup"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "load_list",
        "match_codes",
        "collect_outputs",
    ]


# 2. 中文入口可以解析
def test_chinese_alias_resolves() -> None:
    assert resolve_task("猫超价格竞争力查询") == "tmcs_price_competitiveness_lookup"
    assert resolve_task("天猫超市价格竞争力查询") == "tmcs_price_competitiveness_lookup"
    assert resolve_task("商品价格竞争力查询") == "tmcs_price_competitiveness_lookup"
    assert resolve_task("猫超价格力查询") == "tmcs_price_competitiveness_lookup"


# 3. 缺少商品编码报 PRODUCT_CODE_REQUIRED，且不调用 Ops-Cli
def test_missing_product_code(monkeypatch, tmp_path: Path) -> None:
    run, seen, runner, _ = _run(
        monkeypatch, tmp_path, args=[], dry_run=False, payload=_list_payload([]),
    )
    assert run.status == "failed"
    assert any("PRODUCT_CODE_REQUIRED" in err for err in run.errors)
    assert seen == []
    assert _step_outputs(runner, "check_inputs")["error_code"] == "PRODUCT_CODE_REQUIRED"


# 4. 单个：列表含该编码 → 存在
def test_single_exists(monkeypatch, tmp_path: Path) -> None:
    code = "1042043620771"
    run, seen, runner, _ = _run(
        monkeypatch, tmp_path, args=["--product-code", code], dry_run=False,
        payload=_list_payload(_rows((code, "按摩器"), ("1040897246648", "足疗机"))),
    )
    assert run.status == "success"
    command, interactive = seen[0]
    assert command[:4] == ["--json", "tmcs", "price-competitiveness", "list"]
    assert interactive is True
    out = _step_outputs(runner, "collect_outputs")
    assert out["found"] == [code]
    assert "【存在】" in out["message"]
    assert out["results"][0]["matched_items"][0]["item_id"] == code


# 5. 单个：列表为空 → 不存在
def test_single_not_exists_empty(monkeypatch, tmp_path: Path) -> None:
    code = "999999999999"
    run, _, runner, _ = _run(
        monkeypatch, tmp_path, args=["--product-code", code], dry_run=False,
        payload=_list_payload([]),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["missing"] == [code]
    assert "【不存在】" in out["message"]


# 6. 单个：列表只有其它编码 → 不存在
def test_single_not_exists_other_codes(monkeypatch, tmp_path: Path) -> None:
    code = "1042043620771"
    run, _, runner, _ = _run(
        monkeypatch, tmp_path, args=["--product-code", code], dry_run=False,
        payload=_list_payload(_rows(("1040897246648", "甲"), ("1046002963130", "乙"))),
    )
    out = _step_outputs(runner, "collect_outputs")
    assert out["found"] == []
    assert out["missing"] == [code]


# 7. 批量：部分命中
def test_batch_partial(monkeypatch, tmp_path: Path) -> None:
    rows = _rows(("100", "a"), ("200", "b"), ("300", "c"))
    run, _, runner, _ = _run(
        monkeypatch, tmp_path, args=["--product-codes", "100,999,300"], dry_run=False,
        payload=_list_payload(rows),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["found"] == ["100", "300"]
    assert out["missing"] == ["999"]
    assert "共查询 3 个商品编码" in out["message"]


# 8. 缓存命中：写好当天缓存后再查，不调用 Ops-Cli
def test_cache_hit_skips_ops(monkeypatch, tmp_path: Path) -> None:
    code = "1042043620771"
    today = date.today().isoformat()
    # 先用一次真实抓取写入缓存
    run1, seen1, _, cache_dir = _run(
        monkeypatch, tmp_path, args=["--product-code", code], dry_run=False,
        payload=_list_payload(_rows((code, "按摩器"))),
    )
    assert run1.status == "success"
    assert len(seen1) == 1
    assert (cache_dir / f"list_{today}.json").exists()

    # 第二次查询（同 tmp，缓存已存在）→ 不应再调用 Ops-Cli
    run2, seen2, runner2, _ = _run(
        monkeypatch, tmp_path, args=["--product-code", code], dry_run=False,
        payload=RuntimeError("不应调用 Ops-Cli"),
    )
    assert run2.status == "success"
    assert seen2 == []
    out = _step_outputs(runner2, "collect_outputs")
    assert out["from_cache"] is True
    assert out["found"] == [code]


# 9. --refresh 即使有缓存也重抓
def test_refresh_forces_ops(monkeypatch, tmp_path: Path) -> None:
    code = "100"
    _run(
        monkeypatch, tmp_path, args=["--product-code", code], dry_run=False,
        payload=_list_payload(_rows((code, "x"))),
    )
    run, seen, runner, _ = _run(
        monkeypatch, tmp_path, args=["--product-code", code, "--refresh"], dry_run=False,
        payload=_list_payload(_rows((code, "x"))),
    )
    assert run.status == "success"
    assert len(seen) == 1  # refresh 强制再调用 Ops-Cli
    assert _step_outputs(runner, "collect_outputs")["from_cache"] is False


# 10. dry-run 透传 --dry-run、不写缓存、不进真实 subprocess
def test_dry_run_passes_flag_and_no_cache(monkeypatch, tmp_path: Path) -> None:
    run, seen, runner, cache_dir = _run(
        monkeypatch, tmp_path, args=["--product-code", "TEST123", "--dry-run"], dry_run=True,
        payload=_list_payload([], simulated=True),
    )
    assert run.status == "dry_run_success"
    command, interactive = seen[0]
    assert "--dry-run" in command
    assert interactive is False
    # 模拟数据不落缓存
    assert not cache_dir.exists() or not any(cache_dir.glob("*.json"))
    out = _step_outputs(runner, "collect_outputs")
    assert out["simulated"] is True


def test_dry_run_does_not_invoke_subprocess(monkeypatch, tmp_path: Path) -> None:
    import subprocess

    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应进入真实 subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    run, _, _, _ = _run(
        monkeypatch, tmp_path, args=["--product-code", "TEST123", "--dry-run"], dry_run=True,
        payload=_list_payload([], simulated=True),
    )
    assert run.status == "dry_run_success"


# 11. Ops-Cli 失败时 workflow 输出清晰错误
def test_ops_failure_propagates(monkeypatch, tmp_path: Path) -> None:
    run, _, _, _ = _run(
        monkeypatch, tmp_path, args=["--product-code", "X"], dry_run=False,
        payload=RuntimeError("Ops-Cli 执行失败 [AUTH_REQUIRED]：检测到猫超登录页"),
    )
    assert run.status == "failed"
    assert any("AUTH_REQUIRED" in err for err in run.errors)
