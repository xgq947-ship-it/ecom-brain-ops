#!/usr/bin/env python3
"""
sync_ai_knowledge_base.py
=========================
legacy / fallback：规则脚本刷新 AI 知识库索引。

用法：
    cd ~/Desktop/电商Brain/02-运营店铺
    python scripts/sync_ai_knowledge_base.py

特性：
- 可重复运行，幂等
- 只更新 <!-- AUTO-GENERATED:START --> 到 <!-- AUTO-GENERATED:END --> 之间的内容
- 不覆盖人工补充内容
- 自动扫描 workflows/ 和 tasks/*.yaml，生成 01-工作流/*.md
- 更新 00-总览/系统能力地图.md、00-总览/自动化工作流总览.md、00-总览/当前项目状态.md
- 默认不覆盖 `source: ai-updated` 的 AI 管理文档
- 输出日志到 logs/knowledge_sync.log
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─── 路径配置 ────────────────────────────────────────────────────────────────

SOURCE_ROOT = Path.home() / "Desktop" / "电商Brain" / "02-运营店铺"
KB_ROOT = Path.home() / "Desktop" / "电商Brain-AI-Knowledge"
WORKFLOWS_DIR = SOURCE_ROOT / "运营自动化工具/workflows"
TASKS_DIR = SOURCE_ROOT / "运营自动化工具/tasks"
LOG_FILE = KB_ROOT / "logs/knowledge_sync.log"

TODAY = datetime.now().strftime("%Y-%m-%d")

AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

# ─── 平台映射 ─────────────────────────────────────────────────────────────────

PLATFORM_MAP = {
    "tmall_monthly_bill":              "tmcs",
    "tmall_product_list":              "tmcs",
    "tmcs_fulfillment_watch":          "tmcs",
    "tmcs_fund_table_generate":        "tmcs",
    "tmcs_sku_roi":                    "local",
    "tmcs_sync_jst_shop_goods":        "multi",
    "tmcs_xp_workorder_watch":         "tmcs",
    "tmcs_zdx_fullsite_plan_create":   "tmcs",
    "jst_brush_reimburse_workorder":   "jst",
    "jst_massage_chair_order_remark":  "jst",
    "jst_order_invoice_workorder":     "jst",
    "jst_order_label":                 "jst",
    "jst_pickup_watch":                "jst",
    "jst_product_sync":                "jst",
    "jst_shop_profit_snapshot":        "jst",
    "revenue_query":                   "jst",
    "append_brush_orders":             "local/jst",
    "buyer_show":                      "local",
    "company_nas_index":               "local/nas",
    "company_nas_listing":             "local/nas",
    "retry_queue":                     "local",
    "demo":                            "local",
}

HERMES_AUTO_SAFE = {
    # 知识库更新入口，只改 KB 文档、不碰平台/不打款，安全可自动
    "ai_knowledge_base_update",
    # AI 文件迭代优化：纯本地编排 claude/codex 打磨文件，不碰平台/不打款，原文件不动，安全可自动
    "ai_file_iterate",
    "tmcs_fulfillment_watch", "tmcs_realtime_inventory_watch", "jst_pickup_watch",
    "tmcs_xp_workorder_watch", "tmcs_marketing_risk_warning", "retry_queue",
    "tmcs_fund_table_generate",
    "tmcs_sku_roi", "jst_product_sync", "tmall_product_list",
    "company_nas_index",
    "append_brush_orders",
    # 写入型，但日常无脑跑、有去重/dry-run 守卫，已确认提级直接执行
    "jst_order_label", "jst_brush_reimburse_workorder",
    "jst_massage_chair_order_remark", "jst_order_invoice_workorder",
    # 换货已固化 JTable1 / ChangeBatchItem 接口流；仍由 --confirm-order-no 防误单
    "jst_order_exchange_resend",
    "tmall_monthly_bill", "buyer_show", "company_nas_listing",
    "tmcs_zdx_fullsite_plan_create",
    # 只读查询，dry-run 不发起真实请求，已确认提级直接执行
    "jst_order_logistics", "jst_shop_profit_snapshot", "revenue_query",
    "tmall_price_monitor",
}

# ─── 日志配置 ────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("knowledge_sync")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ─── AUTO-GENERATED 区域更新 ──────────────────────────────────────────────────

def replace_auto_section(content: str, new_body: str) -> str:
    """将 AUTO-GENERATED 区域替换为 new_body；若不存在则追加。"""
    start_idx = content.find(AUTO_START)
    end_idx = content.find(AUTO_END)
    if start_idx != -1 and end_idx != -1:
        before = content[: start_idx + len(AUTO_START)]
        after = content[end_idx:]
        return before + "\n\n" + new_body.strip() + "\n\n" + after
    # 不存在，追加
    return content.rstrip() + "\n\n" + AUTO_START + "\n\n" + new_body.strip() + "\n\n" + AUTO_END + "\n"


def read_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_file(path: Path, content: str, logger: logging.Logger) -> str:
    """写文件，返回 'NEW' / 'UPDATED' / 'UNCHANGED' / 'SKIPPED_AI_UPDATED'"""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_file(path)
    if (
        existing
        and "source: ai-updated" in existing
        and os.environ.get("AI_KB_LEGACY_ALLOW_OVERWRITE") != "1"
    ):
        logger.info(f"  {path.name} [SKIPPED_AI_UPDATED]")
        return "SKIPPED_AI_UPDATED"
    if existing == content:
        return "UNCHANGED"
    path.write_text(content, encoding="utf-8")
    return "NEW" if not existing else "UPDATED"


# ─── 源项目扫描 ───────────────────────────────────────────────────────────────

class WorkflowInfo:
    def __init__(self, wf_id: str):
        self.id = wf_id
        self.name_cn: str = wf_id           # 中文工作流名（来自 workflow.py）
        self.aliases: list[str] = []        # 中文触发词（来自 task.yaml）
        self.steps: list[tuple[str, str]] = []  # (step_id, step_desc)
        self.platform: str = PLATFORM_MAP.get(wf_id, "local")
        self.status: str = "active"
        self.readme_content: str = ""
        self.has_task_yaml: bool = False
        self.task_yaml_path: Optional[Path] = None

    @property
    def dry_run_cmd(self) -> str:
        return f"python3 run.py workflow {self.id} --dry-run"

    @property
    def run_cmd(self) -> str:
        if self.aliases:
            return f"python3 run.py {self.aliases[0]} (或 workflow {self.id})"
        return f"python3 run.py workflow {self.id}"

    @property
    def hermes_auto(self) -> str:
        return "✅ 可自动调用" if self.id in HERMES_AUTO_SAFE else "⚠️ 需确认"

    @property
    def hermes_invoke_block(self) -> str:
        """Hermes 调用建议正文：auto-safe 给出直接执行命令并说明默认行为；
        其余仍只推荐 dry-run，提示需先确认。"""
        if self.id == "jst_order_exchange_resend":
            return (
                "用户给出订单号和换入商品编码时可直接执行换货；换货提交走 "
                "`JTable1 / ChangeBatchItem` 接口，不点击页面「确定」。真实执行仍必须把 "
                "`--confirm-order-no` 设为同一个订单号。\n\n"
                "```bash\n"
                "# 换货预览\n"
                "python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode exchange --sku-code 换入商品编码 --dry-run\n"
                "# 换货直接执行\n"
                "python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode exchange --sku-code 换入商品编码 --execute --confirm-order-no 订单号\n"
                "# 补发执行仍走已确认补发模板\n"
                "python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode resend --execute --confirm-order-no 订单号\n"
                "```"
            )
        if self.id in HERMES_AUTO_SAFE:
            return (
                "无参数即按 workflow 默认行为直接执行，无需追问参数。\n\n"
                "```bash\n"
                f"# 直接执行（按默认参数）\n"
                f"python3 run.py workflow {self.id}\n"
                f"# 安全预览（不写入/不发送）\n"
                f"python3 run.py workflow {self.id} --dry-run\n"
                "```"
            )
        return (
            "```bash\n"
            "# 推荐调用方式\n"
            f"python3 run.py workflow {self.id} --dry-run\n"
            "```"
        )


def parse_workflow_py(wf_dir: Path, info: WorkflowInfo) -> None:
    """从 workflow.py 提取 workflow id、中文名、steps。"""
    wf_py = wf_dir / "workflow.py"
    if not wf_py.exists():
        return
    src = wf_py.read_text(encoding="utf-8")

    # 提取 _make_workflow("id", "name", [...]) 中的名称
    m = re.search(r'_make_workflow\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', src)
    if m:
        info.name_cn = m.group(2)

    # 提取 step("id", "desc", ...) 定义
    step_pattern = re.findall(r'step\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', src)
    info.steps = step_pattern


def parse_task_yaml(info: WorkflowInfo) -> None:
    """从 tasks/*.yaml 或 tasks/*/task.yaml 提取中文别名。"""
    # 优先检查平级 yaml 文件
    flat_yaml = TASKS_DIR / f"{info.id}.yaml"
    if flat_yaml.exists():
        info.has_task_yaml = True
        info.task_yaml_path = flat_yaml
        _extract_aliases_from_yaml(flat_yaml, info)
        return

    # 检查子目录 task.yaml
    sub_yaml = TASKS_DIR / info.id / "task.yaml"
    if sub_yaml.exists():
        info.has_task_yaml = True
        info.task_yaml_path = sub_yaml
        _extract_aliases_from_yaml(sub_yaml, info)


def _extract_aliases_from_yaml(yaml_path: Path, info: WorkflowInfo) -> None:
    src = yaml_path.read_text(encoding="utf-8")
    # 简单行解析（避免引入 PyYAML 依赖）
    in_aliases = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("aliases:"):
            in_aliases = True
            continue
        if in_aliases:
            if stripped.startswith("- ") and not stripped.startswith("- ["):
                alias = stripped[2:].strip().strip("'\"")
                if alias and alias not in info.aliases:
                    info.aliases.append(alias)
            elif stripped.startswith("fuzzy_keywords:") or (stripped and not stripped.startswith("-")):
                in_aliases = False


def scan_workflows() -> list[WorkflowInfo]:
    """扫描所有 workflow 目录，返回 WorkflowInfo 列表。"""
    infos: list[WorkflowInfo] = []
    if not WORKFLOWS_DIR.exists():
        return infos
    for wf_dir in sorted(WORKFLOWS_DIR.iterdir()):
        if (
            not wf_dir.is_dir()
            or wf_dir.name.startswith("_")
            or wf_dir.name == "demo"
            or not (wf_dir / "workflow.py").exists()
        ):
            continue
        info = WorkflowInfo(wf_dir.name)
        parse_workflow_py(wf_dir, info)
        parse_task_yaml(info)
        readme = wf_dir / "README.md"
        if readme.exists():
            info.readme_content = readme.read_text(encoding="utf-8")
        infos.append(info)
    return infos


# ─── 知识库文件生成 ───────────────────────────────────────────────────────────

def frontmatter(type_: str, platform: str, wf_id: str) -> str:
    return f"""---
type: {type_}
status: active
platform: {platform}
updated: {TODAY}
source: auto-generated
workflow_id: {wf_id}
---"""


def _format_step_table(info: WorkflowInfo) -> str:
    if not info.steps:
        return "_步骤信息待补充_"
    rows = ["| Step | 说明 |", "|---|---|"]
    for sid, sdesc in info.steps:
        rows.append(f"| `{sid}` | {sdesc} |")
    return "\n".join(rows)


def _format_aliases(info: WorkflowInfo) -> str:
    if not info.aliases:
        return f"`python3 run.py workflow {info.id} --dry-run`"
    lines = []
    for a in info.aliases[:3]:  # 最多展示3个
        if not a.startswith(info.id) and a != info.id:
            lines.append(f"python3 run.py {a} --dry-run")
    lines.append(f"python3 run.py workflow {info.id} --dry-run")
    return "```bash\n" + "\n".join(lines) + "\n```"


def generate_workflow_auto_body(info: WorkflowInfo) -> str:
    """生成 workflow KB 文件的 AUTO-GENERATED 区域内容。"""
    # 从 README 中提取关键段落
    readme = info.readme_content

    # 尝试提取入口命令
    entry_block = ""
    m = re.search(r'## 入口\n(.*?)(?=\n## |\Z)', readme, re.DOTALL)
    if m:
        entry_block = m.group(1).strip()

    # 尝试提取步骤表
    step_table_from_readme = ""
    m2 = re.search(r'\| step\s*\|.*?\n(?:\|.*?\n)+', readme, re.IGNORECASE)
    if m2:
        step_table_from_readme = m2.group(0).strip()

    alias_list = ", ".join(f"`{a}`" for a in info.aliases[:5]) if info.aliases else "_(无，仅 workflow 命令)_"

    body = f"""## 状态

active

## 所属平台

{info.platform}

## 中文名称

{info.name_cn}

## 触发方式（触发词）

{alias_list}

```bash
# dry-run（安全预览）
python3 run.py workflow {info.id} --dry-run
```

{entry_block if entry_block else ""}

## 业务目标

_详见 `01-工作流/{info.id}.md` 人工描述区域_

## 执行流程（Steps）

{step_table_from_readme if step_table_from_readme else _format_step_table(info)}

## 依赖能力

_详见 `01-工作流/{info.id}.md` 人工描述区域_

## 相关文件

- `运营自动化工具/workflows/{info.id}/`
{f"- `运营自动化工具/tasks/{info.task_yaml_path.relative_to(TASKS_DIR)}`" if info.has_task_yaml else "_(无独立 task.yaml，仅通过 workflow 命令触发)_"}

## Hermes 调用建议

{info.hermes_auto}

{info.hermes_invoke_block}

## Codex 开发建议

- 新增功能时在 `workflows/{info.id}/steps.py` 中添加新 step
- 禁止在此 workflow 层直接请求平台 URL / Cookie / Token
- 必须支持 `--dry-run`，危险操作用 `if not ctx.dry_run:` 守卫"""

    return body.strip()


def sync_workflow_kb_file(info: WorkflowInfo, logger: logging.Logger) -> str:
    """同步单个 workflow 的 KB 文件，返回操作状态。"""
    kb_file = KB_ROOT / f"01-工作流/{info.id}.md"
    existing = read_file(kb_file)

    if not existing:
        # 新建文件
        fm = frontmatter("workflow", info.platform, info.id)
        auto_body = generate_workflow_auto_body(info)
        content = f"""{fm}

# {info.name_cn} (`{info.id}`)

{AUTO_START}

{auto_body}

{AUTO_END}

---
_以下为人工补充区域，重复运行不会覆盖_
"""
    else:
        # 只更新 AUTO-GENERATED 区域
        auto_body = generate_workflow_auto_body(info)
        # 同步更新 frontmatter 中的 updated 日期
        content = re.sub(
            r'^updated: \d{4}-\d{2}-\d{2}',
            f'updated: {TODAY}',
            existing,
            flags=re.MULTILINE
        )
        content = replace_auto_section(content, auto_body)

    status = write_file(kb_file, content, logger)
    logger.info(f"  workflow/{info.id}.md [{status}]")
    return status


# ─── 总览文件生成 ─────────────────────────────────────────────────────────────

def generate_system_map_body(infos: list[WorkflowInfo]) -> str:
    tmcs_rows = [i for i in infos if "tmcs" in i.platform or "tmall" in i.id]
    jst_rows = [i for i in infos if i.platform == "jst"]
    local_rows = [i for i in infos if i.platform in ("local", "local/jst", "local/nas")]
    multi_rows = [i for i in infos if i.platform == "multi"]

    def table(rows: list[WorkflowInfo]) -> str:
        lines = ["| workflow_id | 中文名 | 触发词（主要） | dry-run |",
                 "|---|---|---|---|"]
        for i in rows:
            main_alias = i.aliases[0] if i.aliases else "—"
            lines.append(f"| `{i.id}` | {i.name_cn} | `{main_alias}` | ✅ |")
        return "\n".join(lines)

    return f"""## 最后同步时间

{TODAY}

## 猫超（TMCS）业务能力

{table(tmcs_rows)}

## 聚水潭（JST）业务能力

{table(jst_rows)}

## 跨平台能力

{table(multi_rows)}

## 本地 / NAS 能力

{table(local_rows)}

## 全部 Workflow 数量

共 **{len(infos)}** 个（不含 demo）

## 平台层能力（Ops-Cli）

- 猫超：商品同步、库存、账单、推广账单、XP工单、物流履约、推广计划
- 聚水潭：商品资料、订单打标/备注、物流、揽收、统计、利润、发票工单、店铺商品导入
- 浏览器：9222 SessionHub、双浏览器学习

详见：`02-平台能力/tmcs猫超.md`、`02-平台能力/jst聚水潭.md`"""


def generate_workflow_overview_body(infos: list[WorkflowInfo]) -> str:
    rows = ["| 中文名 | workflow_id | 平台 | dry-run 命令 | Hermes |",
            "|---|---|---|---|---|"]
    for i in infos:
        rows.append(
            f"| {i.name_cn} | `{i.id}` | {i.platform} | "
            f"`python3 run.py workflow {i.id} --dry-run` | {i.hermes_auto} |"
        )

    return f"""## 最后同步时间

{TODAY}

## 全量 Workflow 表

{chr(10).join(rows)}

## 常用中文入口速查

```bash
cd ~/Desktop/电商Brain/02-运营店铺/运营自动化工具

# 列出所有任务
python3 run.py --list

# 查看最近运行记录
python3 run.py runs --limit 10
```

### 猫超系列

```bash
python3 run.py 猫超账单整理 --dry-run
python3 run.py 更新猫超商品列表 --dry-run
python3 run.py 猫超履约监控 --dry-run
python3 run.py 猫超工单监控
python3 run.py 猫超单品ROI测算 --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py 创建智多星全站推广计划 --item-id 123456789 --daily-budget 100 --dry-run
```

### 聚水潭系列

```bash
python3 run.py 更新聚水潭资料 --dry-run
python3 run.py 聚水潭揽收监控 --notify --dry-run
python3 run.py 刷单订单插黄旗 --dry-run
python3 run.py 刷单报销登记 --dry-run
python3 run.py 聚水潭发票工单 --dry-run ...
python3 run.py 按摩椅订单自动备注 --dry-run
```

### 刷单流程（推荐顺序）

```bash
python3 run.py 刷单表格登记 --dry-run        # 1. 登记
python3 run.py 刷单订单插黄旗 --dry-run       # 2. 打标
python3 run.py 刷单报销登记 --dry-run         # 3. 报销
```"""


def generate_current_status_body(infos: list[WorkflowInfo]) -> str:
    """生成当前项目状态文档。"""
    # 从 git log 获取最近提交信息（已在调用方执行）
    return f"""## 最后同步时间

{TODAY}

## 项目总体状态

| 维度 | 状态 |
|---|---|
| Workflow 总数 | {len(infos)} 个（不含 demo） |
| 有中文入口的 | {sum(1 for i in infos if i.aliases)} 个 |
| 仅 workflow 命令 | {sum(1 for i in infos if not i.aliases)} 个 |
| 猫超相关 | {sum(1 for i in infos if "tmcs" in i.platform or "tmall" in i.id)} 个 |
| 聚水潭相关 | {sum(1 for i in infos if i.platform == "jst")} 个 |
| 已跑通监控类 | tmcs_fulfillment_watch（已真实跑通） |

## 最近变更（来自 git log）

_详见源项目：`git log --oneline -10`_

## 核心执行入口

```bash
# 业务层入口
cd ~/Desktop/电商Brain/02-运营店铺/运营自动化工具
python3 run.py --list
python3 run.py runs --limit 10

# 平台层入口
cd ~/Desktop/电商Brain/02-运营店铺/Ops-Cli
source .venv/bin/activate
ops --help
ops --json browser check --port 9222
```

## 主数据位置

| 文件 | 路径 |
|---|---|
| 猫超商品列表 | `主数据/猫超商品列表导出 (最新）.xlsx` |
| 聚水潭商品资料 | `主数据/聚水潭商品资料（最新）.xlsx` |
| 按摩椅资料表 | `主数据/按摩椅资料表.xlsx` |

## 知识库维护命令

```bash
# 同步知识库
cd ~/Desktop/电商Brain/02-运营店铺
python scripts/sync_ai_knowledge_base.py

# 校验知识库
python scripts/validate_ai_knowledge_base.py
```

## Workflow 完整清单

| workflow_id | 中文名 | 平台 | Hermes可自动 |
|---|---|---|---|
{chr(10).join(f"| `{i.id}` | {i.name_cn} | {i.platform} | {i.hermes_auto} |" for i in infos)}"""


def sync_overview_file(kb_path: Path, title: str, body: str, logger: logging.Logger) -> str:
    """更新总览类文件的 AUTO-GENERATED 区域。"""
    existing = read_file(kb_path)
    if not existing:
        content = f"""---
type: overview
updated: {TODAY}
source: auto-generated
---

# {title}

{AUTO_START}

{body.strip()}

{AUTO_END}

---
_以下为人工补充区域，重复运行不会覆盖_
"""
    else:
        content = re.sub(
            r'^updated: \d{4}-\d{2}-\d{2}',
            f'updated: {TODAY}',
            existing,
            flags=re.MULTILINE
        )
        content = replace_auto_section(content, body)

    status = write_file(kb_path, content, logger)
    logger.info(f"  {kb_path.name} [{status}]")
    return status


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main() -> int:
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("knowledge_sync START (legacy/fallback)")
    logger.info(f"source: {SOURCE_ROOT}")
    logger.info(f"target: {KB_ROOT}")

    # 确保知识库目录存在
    for d in ["00-总览", "01-工作流", "02-平台能力", "03-SOP",
              "04-项目文档", "05-运行报告", "06-需求池",
              "07-提示词", "08-决策记录", "99-归档", "logs"]:
        (KB_ROOT / d).mkdir(parents=True, exist_ok=True)

    # 扫描 workflows
    logger.info("Scanning workflows...")
    infos = scan_workflows()
    logger.info(f"  Found {len(infos)} workflows (excluding demo)")

    # 同步每个 workflow 的 KB 文件
    logger.info("Syncing 01-工作流/*.md ...")
    stats = {"NEW": 0, "UPDATED": 0, "UNCHANGED": 0, "SKIPPED_AI_UPDATED": 0}
    for info in infos:
        status = sync_workflow_kb_file(info, logger)
        stats[status] = stats.get(status, 0) + 1

    # 同步总览文件
    logger.info("Syncing 00-总览/*.md ...")

    sync_overview_file(
        KB_ROOT / "00-总览/系统能力地图.md",
        "系统能力地图",
        generate_system_map_body(infos),
        logger
    )

    sync_overview_file(
        KB_ROOT / "00-总览/自动化工作流总览.md",
        "自动化工作流总览",
        generate_workflow_overview_body(infos),
        logger
    )

    sync_overview_file(
        KB_ROOT / "00-总览/当前项目状态.md",
        "当前项目状态",
        generate_current_status_body(infos),
        logger
    )

    # 输出统计
    logger.info("-" * 40)
    logger.info(f"Workflow files: NEW={stats.get('NEW',0)}, "
                f"UPDATED={stats.get('UPDATED',0)}, "
                f"UNCHANGED={stats.get('UNCHANGED',0)}, "
                f"SKIPPED_AI_UPDATED={stats.get('SKIPPED_AI_UPDATED',0)}")
    logger.info(f"Total workflows synced: {len(infos)}")
    logger.info("knowledge_sync DONE")
    logger.info("=" * 60)

    print(f"\n✅ 同步完成：{len(infos)} 个 workflow，日志→ {LOG_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
