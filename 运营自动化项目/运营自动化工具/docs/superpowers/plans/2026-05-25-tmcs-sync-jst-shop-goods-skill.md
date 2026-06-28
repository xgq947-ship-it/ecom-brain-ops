# 猫超商品信息同步聚水潭 Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 `tmcs_sync_jst_shop_goods` 接入 `run.py` 统一入口，并补齐正式 skill 触发文档。

**Architecture:** 现有 `skills/tmcs_sync_jst_shop_goods` 继续承担输入解析、Excel 生成和调用 `Ops-Cli` 的业务编排；新增 `tasks/` 下的薄适配入口供 `run.py` 调度。平台 API 和导入行为仍由 `Ops-Cli` 独占。

**Tech Stack:** Python 3, argparse, pytest, Ops-Cli JSON commands

---

### Task 1: 统一任务注册与适配入口

**Files:**
- Create: `tasks/tmcs_sync_jst_shop_goods/main.py`
- Modify: `core/task_registry.py`
- Modify: `run.py`
- Test: `tests/test_tmcs_sync_jst_shop_goods.py`

- [ ] **Step 1: Write failing tests**

增加测试，断言 `resolve_task("聚水潭商品信息同步猫超")`、`resolve_task("猫超商品同步聚水潭")` 均解析到 `tmcs_sync_jst_shop_goods`，且注册脚本位于 `tasks/tmcs_sync_jst_shop_goods/main.py`。

- [ ] **Step 2: Run test to verify failure**

Run: `PYTHONPATH=. <项目根>/Ops-Cli/.venv/bin/python -m pytest tests/test_tmcs_sync_jst_shop_goods.py -q`

Expected: FAIL because the task is not registered.

- [ ] **Step 3: Implement minimal adapter and registry entry**

新增 task adapter，将命令行参数原样转交现有 `skills/tmcs_sync_jst_shop_goods/main.py`；在任务表增加中文 aliases 和针对 `猫超/聚水潭/同步` 的明确规则；在 `run.py` 声明该任务需要 `openpyxl`。

- [ ] **Step 4: Run tests to verify pass**

Run: `PYTHONPATH=. <项目根>/Ops-Cli/.venv/bin/python -m pytest tests/test_tmcs_sync_jst_shop_goods.py -q`

Expected: PASS.

### Task 2: 正式 Skill 与使用文档

**Files:**
- Create: `skills/tmcs_sync_jst_shop_goods/SKILL.md`
- Modify: `skills/tmcs_sync_jst_shop_goods/skill.yaml`
- Modify: `skills/tmcs_sync_jst_shop_goods/README.md`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `docs/ops_cli_integration.md`

- [ ] **Step 1: Write failing metadata assertions**

增加测试，断言标准 `SKILL.md` 存在，且 `skill.yaml` 的正式入口使用 `python3 run.py 聚水潭商品信息同步猫超`。

- [ ] **Step 2: Run test to verify failure**

Run: `PYTHONPATH=. <项目根>/Ops-Cli/.venv/bin/python -m pytest tests/test_tmcs_sync_jst_shop_goods.py -q`

Expected: FAIL because no standard `SKILL.md` exists and metadata still references the direct script entry.

- [ ] **Step 3: Add skill instructions and sync docs**

写入触发词、标准执行命令、批量输入、真实写入回报项，并明确平台动作只调用 `Ops-Cli`；同步项目文档中的入口示例。

- [ ] **Step 4: Verify project entry and tests**

Run: `python3 run.py --list`

Run: `PYTHONPATH=. <项目根>/Ops-Cli/.venv/bin/python -m pytest tests/test_tmcs_sync_jst_shop_goods.py tests/test_ops_cli_client.py -q`

Expected: list includes `tmcs_sync_jst_shop_goods`; tests pass.

### Task 3: Regression Verification

**Files:**
- Verify: `运营自动化工具/`
- Verify: `Ops-Cli/`

- [ ] **Step 1: Run business regression tests**

Run: `PYTHONPATH=<项目根>/运营自动化工具 <项目根>/Ops-Cli/.venv/bin/python -m pytest tests/test_tmcs_sync_jst_shop_goods.py tests/test_ops_cli_client.py -q`

Expected: PASS.

- [ ] **Step 2: Run Ops-Cli regression tests**

Run: `./.venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 3: Check patch hygiene**

Run: `git diff --check`

Expected: no output.
