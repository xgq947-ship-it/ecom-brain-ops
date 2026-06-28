# Tmall Monthly Bill Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the monthly TMCS bill workbook to add reconciliation and promotion source sheets plus a profit summary block in `开票表`, while continuing to source all new data exclusively through existing CLI commands.

**Architecture:** Keep `tasks/tmall_monthly_bill/main.py` as the entrypoint and reduce `processor.py` to orchestration plus shared workbook helpers. Add three focused services under `tasks/tmall_monthly_bill/services/` for reconciliation sheet import, promotion sheet import, and profit summary calculation/rendering. Reuse `clients/ops_cli_client.py` for all new data acquisition and operate on a single `openpyxl` workbook instance through the full write flow.

**Tech Stack:** Python, `openpyxl`, `decimal.Decimal`, existing `Ops-Cli` JSON contract, `pytest`

---

### Task 1: Add failing tests for source sheet import and profit summary rendering

**Files:**
- Create: `<项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py`
- Modify: none
- Test: `<项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook

from tasks.tmall_monthly_bill.services.profit_summary_service import render_profit_summary
from tasks.tmall_monthly_bill.services.promotion_service import write_promotion_sheet
from tasks.tmall_monthly_bill.services.reconciliation_service import write_reconciliation_sheet


def _sheet_values(sheet):
    return list(sheet.iter_rows(values_only=True))


def test_rebuilds_reconciliation_sheet_with_original_header_order(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active.title = "开票表"
    workbook.create_sheet("对账单列表")

    source = tmp_path / "对账单列表.xlsx"
    data = Workbook()
    sheet = data.active
    sheet.title = "Sheet1"
    sheet.append(["账单编号", "创建时间", "状态"])
    sheet.append(["B001", "2026-04-01", "成功"])
    data.save(source)

    write_reconciliation_sheet(workbook, source)

    target = workbook["对账单列表"]
    assert _sheet_values(target) == [
        ("账单编号", "创建时间", "状态"),
        ("B001", "2026-04-01", "成功"),
    ]


def test_writes_promotion_sheet_from_csv_without_changing_column_order(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active.title = "开票表"

    source = tmp_path / "万象台推广账单_2026-04.csv"
    source.write_text("收支类型,金额,备注\n支出,12.50,投放A\n", encoding="utf-8-sig")

    write_promotion_sheet(workbook, "万相台推广数据表格", source)

    target = workbook["万相台推广数据表格"]
    assert _sheet_values(target) == [
        ("收支类型", "金额", "备注"),
        ("支出", 12.5, "投放A"),
    ]


def test_renders_profit_summary_in_invoice_sheet_right_side(tmp_path: Path) -> None:
    workbook = Workbook()
    invoice = workbook.active
    invoice.title = "开票表"
    invoice.append(["后端商品编码", "商品数量", "票扣", "开票金额", "成本"])
    invoice.append(["A1", 2, -10.5, 120.3, 40])
    invoice.append(["A2", 3, "-5.00", "200.20", "50"])

    cost = workbook.create_sheet("成本表")
    cost.append(["后端商品编码", "金额"])
    cost.append(["A1", 80])
    cost.append(["A2", "150.00"])

    charge = workbook.create_sheet("账扣表格")
    charge.append(["含税金额"])
    charge.append([-12.34])

    wxt = workbook.create_sheet("万相台推广数据表格")
    wxt.append(["收支类型", "金额"])
    wxt.append(["支出", "30.25"])
    wxt.append(["收入", "5.00"])

    zdx = workbook.create_sheet("智多星推广数据表格")
    zdx.append(["类型", "收支金额"])
    zdx.append(["从冻结中转出", "18.75"])
    zdx.append(["其他", "1.00"])

    render_profit_summary(workbook, month_label="4月份利润表")

    start_col = invoice.max_column - 2
    assert invoice.cell(58, start_col).value == "4月份利润表"
    assert invoice.cell(59, start_col).value == "销售金额（开票金额）"
    assert invoice.cell(59, start_col + 1).value == 5
    assert invoice.cell(59, start_col + 2).value == 320.5
    assert invoice.cell(63, start_col + 2).value == 12.34
    assert invoice.cell(64, start_col + 2).value == Decimal("28.16")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: FAIL with import errors because the new services do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# Create placeholder modules with the exported functions used by the tests:
# - write_reconciliation_sheet(...)
# - write_promotion_sheet(...)
# - render_profit_summary(...)
# The first pass only needs enough structure for the tests to import and fail on assertions.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py
git commit -m "test: cover monthly bill source sheets and profit summary"
```

### Task 2: Implement workbook and source parsing helpers plus the three services

**Files:**
- Create: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/services/__init__.py`
- Create: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/services/reconciliation_service.py`
- Create: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/services/promotion_service.py`
- Create: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/services/profit_summary_service.py`
- Modify: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/processor.py`
- Test: `<项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_profit_summary_falls_back_to_invoice_cost_when_cost_sheet_missing() -> None:
    workbook = Workbook()
    invoice = workbook.active
    invoice.title = "开票表"
    invoice.append(["商品数量", "票扣", "开票金额", "成本"])
    invoice.append([2, 0, 100, 20])
    invoice.append([1, 0, 60, 30])
    workbook.create_sheet("账扣表格").append(["含税金额"])
    workbook["账扣表格"].append([0])
    workbook.create_sheet("万相台推广数据表格").append(["收支类型", "金额"])
    workbook["万相台推广数据表格"].append(["支出", 10])
    workbook.create_sheet("智多星推广数据表格").append(["类型", "金额"])
    workbook["智多星推广数据表格"].append(["从冻结中转出", 5])

    render_profit_summary(workbook, month_label="4月份利润表")

    start_col = invoice.max_column - 2
    assert invoice.cell(60, start_col + 2).value == Decimal("70.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: FAIL because fallback cost aggregation is not implemented yet.

- [ ] **Step 3: Write minimal implementation**

```python
# In processor.py:
# - add helper functions for deleting/recreating sheets, parsing decimals, coercing cell values, and setting widths
#
# In reconciliation_service.py:
# - load an Excel file with openpyxl
# - read the first worksheet preserving header order
# - replace the workbook sheet named "对账单列表"
#
# In promotion_service.py:
# - parse .xlsx via openpyxl and .csv via csv module with utf-8-sig / gb18030 fallback
# - replace target sheet while preserving column order
#
# In profit_summary_service.py:
# - locate source sheets by name
# - resolve columns by header text, never fixed indexes
# - compute all metrics with Decimal
# - fallback to invoice cost * quantity when cost sheet missing
# - write the 7-row summary block at row 58 and column invoice.max_column + 2
# - apply borders, fills, bold fonts, alignment, and number formats
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add <项目根>/运营自动化工具/tasks/tmall_monthly_bill/processor.py <项目根>/运营自动化工具/tasks/tmall_monthly_bill/services <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py
git commit -m "feat: add monthly bill source sheet and profit services"
```

### Task 3: Wire Ops-Cli download orchestration into the monthly bill flow

**Files:**
- Modify: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/main.py`
- Modify: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/processor.py`
- Modify: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/downloader.py`
- Test: `<项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import Mock

from tasks.tmall_monthly_bill import processor


def test_process_invokes_cli_downloaded_source_imports(monkeypatch, tmp_path: Path) -> None:
    bill_dir = tmp_path
    # Build minimal HDB inputs and master tables here, then monkeypatch:
    # - processor.fetch_statement_list_path -> reconciliation source file
    # - processor.fetch_promotion_paths -> {"wxt": path1, "zdx": path2}
    # Verify the resulting workbook contains the three new sheets.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: FAIL because processor does not yet orchestrate the new services.

- [ ] **Step 3: Write minimal implementation**

```python
# In processor.py:
# - add helper to derive bill date range from HDB filenames
# - add function to call run_ops_json(["--json", "tmcs", "bill", "download", ... "--download-statement-list"])
# - add function to call run_ops_json(["--json", "tmcs", "promotion-bill", "download", "--source", ...])
# - extract returned file paths
# - pass those paths into the three services before workbook.save(...)
#
# In main.py:
# - keep existing flow, but ensure the processor invocation has access to the
#   statement list path and promotion source paths via returned metadata.
#
# In downloader.py:
# - keep compatibility, only adjust helper behavior if needed for source path extraction.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add <项目根>/运营自动化工具/tasks/tmall_monthly_bill/main.py <项目根>/运营自动化工具/tasks/tmall_monthly_bill/processor.py <项目根>/运营自动化工具/tasks/tmall_monthly_bill/downloader.py <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py
git commit -m "feat: orchestrate monthly bill cli source imports"
```

### Task 4: Update docs and verify the full flow

**Files:**
- Modify: `<项目根>/运营自动化工具/tasks/tmall_monthly_bill/README.md`
- Modify: `<项目根>/运营自动化工具/docs/superpowers/specs/2026-05-21-tmall-monthly-bill-upgrade-design.md`
- Test: `<项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_readme_examples_reference_new_output_sheets() -> None:
    readme = Path("<项目根>/运营自动化工具/tasks/tmall_monthly_bill/README.md").read_text(encoding="utf-8")
    assert "对账单列表" in readme
    assert "万相台推广数据表格" in readme
    assert "智多星推广数据表格" in readme
    assert "利润汇总" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: FAIL because the README does not yet describe the new workbook outputs.

- [ ] **Step 3: Write minimal implementation**

```markdown
Update README to describe:
- the three new source sheets
- the profit summary block on the right side of `开票表`
- that new data is sourced through `Ops-Cli`
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add <项目根>/运营自动化工具/tasks/tmall_monthly_bill/README.md <项目根>/运营自动化工具/tests/test_tmall_monthly_bill_processor.py
git commit -m "docs: describe upgraded monthly bill workbook output"
```
