#!/usr/bin/env python3
"""
validate_ai_knowledge_base.py
==============================
校验电商运营 AI 知识库的完整性和规范性。

用法：
    cd ~/Desktop/电商Brain/02-运营店铺
    python scripts/validate_ai_knowledge_base.py

退出码：
    0  全部通过
    1  存在 ERROR 级别问题
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ─── 路径配置 ────────────────────────────────────────────────────────────────

SOURCE_ROOT = Path.home() / "Desktop" / "电商Brain" / "02-运营店铺"
KB_ROOT = Path.home() / "Desktop" / "电商Brain-AI-Knowledge"
WORKFLOWS_DIR = SOURCE_ROOT / "运营自动化工具/workflows"
LOG_FILE = KB_ROOT / "logs/knowledge_validate.log"

# 必须存在的知识库目录
REQUIRED_DIRS = [
    "00-总览", "01-工作流", "02-平台能力",
    "03-SOP", "04-项目文档", "05-运行报告",
    "06-需求池", "07-提示词", "08-决策记录",
    "99-归档",
]

# 必须存在的核心文件
REQUIRED_FILES = [
    "AGENTS.md",
    "KNOWLEDGE_INDEX.md",
    "SYNC_RULES.md",
    "CHANGELOG.md",
    "README.md",
    "00-总览/知识库版本.md",
    "00-总览/系统能力地图.md",
    "00-总览/自动化工作流总览.md",
    "00-总览/当前项目状态.md",
    "00-总览/Hermes读取入口.md",
    "00-总览/Workflow命令映射表.md",
    "03-SOP/新增自动化工作流SOP.md",
    "03-SOP/新增平台能力SOP.md",
    "03-SOP/Codex开发交付SOP.md",
    "03-SOP/Hermes调用项目知识库SOP.md",
    "04-项目文档/架构分层说明.md",
    "04-项目文档/干跑规范.md",
]

# workflow 文件必须包含的节标题（支持中文）
REQUIRED_SECTIONS = [
    "## 状态",
    "## 所属平台",
    "## 触发方式",
    "## 业务目标",
    "## 执行流程",
    "## 依赖能力",
    "## 相关文件",
]

# workflow 文件推荐包含的节（缺失时 WARN，不报 ERROR）
RECOMMENDED_SECTIONS = [
    "## 输入",
    "## 输出",
    "## 已知问题",
    "## 下次优化",
    "## Hermes",
    "## Codex",
]

AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"

# ─── 结果模型 ─────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    level: str   # "OK" | "WARN" | "ERROR"
    message: str

@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def ok(self, name: str, msg: str = ""):
        self.results.append(CheckResult(name, "OK", msg))

    def warn(self, name: str, msg: str):
        self.results.append(CheckResult(name, "WARN", msg))

    def error(self, name: str, msg: str):
        self.results.append(CheckResult(name, "ERROR", msg))

    @property
    def has_error(self) -> bool:
        return any(r.level == "ERROR" for r in self.results)

    def summary(self) -> str:
        ok = sum(1 for r in self.results if r.level == "OK")
        warn = sum(1 for r in self.results if r.level == "WARN")
        err = sum(1 for r in self.results if r.level == "ERROR")
        return f"总计 {len(self.results)} 项：✅ {ok} OK，⚠️ {warn} WARN，❌ {err} ERROR"


# ─── 日志配置 ────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("knowledge_validate")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# ─── 各项检查 ─────────────────────────────────────────────────────────────────

def check_kb_root_exists(report: Report) -> bool:
    if KB_ROOT.exists() and KB_ROOT.is_dir():
        report.ok("kb_root_exists", f"{KB_ROOT}")
        return True
    else:
        report.error("kb_root_exists", f"知识库目录不存在：{KB_ROOT}，请先运行 sync 脚本")
        return False


def check_required_dirs(report: Report) -> None:
    for d in REQUIRED_DIRS:
        path = KB_ROOT / d
        if path.exists() and path.is_dir():
            report.ok(f"dir/{d}")
        else:
            report.error(f"dir/{d}", f"必要目录缺失：{path}")


def check_required_files(report: Report) -> None:
    for f in REQUIRED_FILES:
        path = KB_ROOT / f
        if path.exists():
            report.ok(f"file/{f}")
        else:
            report.error(f"file/{f}", f"必要文件缺失：{path}")


def check_workflow_files(report: Report) -> None:
    """检查每个 source workflow 对应的 KB 文件是否存在且规范。"""
    if not WORKFLOWS_DIR.exists():
        report.error("workflows_dir", f"源 workflows 目录不存在：{WORKFLOWS_DIR}")
        return

    seen_ids: dict[str, str] = {}   # id -> file
    kb_wf_dir = KB_ROOT / "01-工作流"

    for wf_dir in sorted(WORKFLOWS_DIR.iterdir()):
        if (
            not wf_dir.is_dir()
            or wf_dir.name.startswith("_")
            or wf_dir.name == "demo"
            or not (wf_dir / "workflow.py").exists()
        ):
            continue
        wf_id = wf_dir.name

        # 检查重复
        if wf_id in seen_ids:
            report.error(f"wf/{wf_id}/duplicate", f"workflow_id 重复：{wf_id}")
        else:
            seen_ids[wf_id] = str(wf_dir)

        kb_file = kb_wf_dir / f"{wf_id}.md"

        # 检查 KB 文件是否存在
        if not kb_file.exists():
            report.error(f"wf/{wf_id}/kb_file_missing",
                         f"KB 文件缺失：{kb_file}  → 运行 sync 脚本生成")
            continue

        content = kb_file.read_text(encoding="utf-8")

        # 检查 YAML frontmatter
        if content.startswith("---"):
            report.ok(f"wf/{wf_id}/frontmatter")
        else:
            report.error(f"wf/{wf_id}/frontmatter",
                         f"缺少 YAML frontmatter（文件应以 --- 开头）：{kb_file}")

        # 检查 AUTO-GENERATED 区域
        has_start = AUTO_START in content
        has_end = AUTO_END in content
        if has_start and has_end:
            report.ok(f"wf/{wf_id}/auto_generated_section")
        else:
            report.error(f"wf/{wf_id}/auto_generated_section",
                         f"缺少 AUTO-GENERATED 标记：{kb_file}")

        # 检查必需节标题
        for section in REQUIRED_SECTIONS:
            # 检查 AUTO-GENERATED 内或全文
            if re.search(re.escape(section), content, re.IGNORECASE):
                report.ok(f"wf/{wf_id}/section/{section.lstrip('# ').split()[0]}")
            else:
                report.error(
                    f"wf/{wf_id}/section/{section.lstrip('# ').split()[0]}",
                    f"缺少必需节 {section!r}：{kb_file}"
                )

        # 检查推荐节标题（WARN）
        for section in RECOMMENDED_SECTIONS:
            found = any(s in content for s in [section, section.replace("## ", "## ")])
            if not found:
                # 模糊匹配
                keyword = section.strip("# ").strip()
                if keyword and keyword not in content:
                    report.warn(
                        f"wf/{wf_id}/recommended/{keyword}",
                        f"推荐补充节 {section!r}：{kb_file.name}"
                    )

        # 检查 workflow_id frontmatter 字段
        if f"workflow_id: {wf_id}" in content:
            report.ok(f"wf/{wf_id}/frontmatter_wf_id")
        else:
            report.warn(f"wf/{wf_id}/frontmatter_wf_id",
                        f"frontmatter 中缺少 workflow_id: {wf_id}")


def check_no_extra_wf_in_kb(report: Report) -> None:
    """检查 KB 中是否有源项目不存在的 workflow 文件（孤立文件）。"""
    if not WORKFLOWS_DIR.exists():
        return
    source_ids = {
        d.name for d in WORKFLOWS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_") and d.name != "demo" and (d / "workflow.py").exists()
    }
    kb_wf_dir = KB_ROOT / "01-工作流"
    if not kb_wf_dir.exists():
        return
    for kb_file in sorted(kb_wf_dir.glob("*.md")):
        wf_id = kb_file.stem
        if wf_id not in source_ids:
            report.warn(f"kb_orphan/{wf_id}",
                        f"KB 中存在孤立文件（源项目无对应 workflow）：{kb_file.name}")


def check_sync_log_exists(report: Report) -> None:
    if LOG_FILE.exists():
        report.ok("sync_log_exists", str(LOG_FILE))
    else:
        report.warn("sync_log_exists", f"同步日志不存在，请先运行 sync 脚本：{LOG_FILE}")


def check_version_doc(report: Report) -> None:
    path = KB_ROOT / "00-总览/知识库版本.md"
    if not path.exists():
        report.error("version_doc/exists", f"缺少版本文件：{path}")
        return
    content = path.read_text(encoding="utf-8")
    if "## 最后更新时间" in content:
        report.ok("version_doc/updated_at")
    else:
        report.error("version_doc/updated_at", f"版本文件缺少“最后更新时间”：{path}")
    if "## 当前 workflow 数量" in content:
        report.ok("version_doc/workflow_count")
    else:
        report.error("version_doc/workflow_count", f"版本文件缺少“当前 workflow 数量”：{path}")


def _extract_section_value(content: str, heading: str) -> str | None:
    pattern = rf"^## {re.escape(heading)}\s*\n+(.+?)(?:\n## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def _version_workflow_count() -> int | None:
    path = KB_ROOT / "00-总览/知识库版本.md"
    if not path.exists():
        return None
    value = _extract_section_value(path.read_text(encoding="utf-8"), "当前 workflow 数量")
    if not value:
        return None
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def _version_updated_date() -> str | None:
    path = KB_ROOT / "00-总览/知识库版本.md"
    if not path.exists():
        return None
    value = _extract_section_value(path.read_text(encoding="utf-8"), "最后更新时间")
    if not value:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else None


def _require_count_in_doc(report: Report, relative_path: str, expected_count: int, patterns: list[str]) -> None:
    path = KB_ROOT / relative_path
    if not path.exists():
        report.error(f"count/{relative_path}", f"缺少文件：{path}")
        return
    content = path.read_text(encoding="utf-8")
    found_any = False
    mismatches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, content, re.MULTILINE | re.DOTALL):
            found_any = True
            count = int(match.group(1))
            if count != expected_count:
                mismatches.append(str(count))
    if mismatches:
        report.error(
            f"count/{relative_path}",
            f"workflow 数量与源码不一致：文件中为 {', '.join(mismatches)}，源码为 {expected_count}",
        )
    elif found_any:
        report.ok(f"count/{relative_path}")
    else:
        report.warn(f"count/{relative_path}", f"未找到可校验的 workflow 数量：{path}")


def check_entry_consistency(report: Report) -> None:
    """校验入口文件和总览文件的 workflow 数量、同步日期不漂移。"""
    source_count = len(_source_workflow_ids())
    version_count = _version_workflow_count()
    if version_count is None:
        report.error("count/00-总览/知识库版本.md", "无法读取版本文件 workflow 数量")
    elif version_count != source_count:
        report.error(
            "count/00-总览/知识库版本.md",
            f"版本文件 workflow 数量与源码不一致：版本文件为 {version_count}，源码为 {source_count}",
        )
    else:
        report.ok("count/00-总览/知识库版本.md")

    _require_count_in_doc(
        report,
        "README.md",
        source_count,
        [r"01-工作流/[^\n]*（(\d+) 个"],
    )
    _require_count_in_doc(
        report,
        "00-总览/Workflow命令映射表.md",
        source_count,
        [r"全量派发表（共 (\d+) 个"],
    )
    _require_count_in_doc(
        report,
        "00-总览/当前项目状态.md",
        source_count,
        [r"Workflow 总数 \| (\d+) 个", r"有中文入口的 \| (\d+) 个"],
    )

    version_date = _version_updated_date()
    readme_path = KB_ROOT / "README.md"
    if version_date and readme_path.exists():
        readme = readme_path.read_text(encoding="utf-8")
        if f"最后同步时间：{version_date}" in readme and f"updated: {version_date}" in readme:
            report.ok("entry/README.md/date")
        else:
            report.error(
                "entry/README.md/date",
                f"README 同步日期与知识库版本不一致，应为 {version_date}",
            )

    sync_rules = KB_ROOT / "SYNC_RULES.md"
    if sync_rules.exists():
        content = sync_rules.read_text(encoding="utf-8")
        required_terms = ["ai_knowledge_base_update", "validate_ai_knowledge_base.py", "AUTO-GENERATED"]
        missing = [term for term in required_terms if term not in content]
        if missing:
            report.error("entry/SYNC_RULES.md/content", f"SYNC_RULES 缺少关键规则：{', '.join(missing)}")
        else:
            report.ok("entry/SYNC_RULES.md/content")


def _source_workflow_ids() -> set[str]:
    if not WORKFLOWS_DIR.exists():
        return set()
    return {
        d.name for d in WORKFLOWS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_") and d.name != "demo" and (d / "workflow.py").exists()
    }


def _check_workflow_refs_in_doc(
    report: Report,
    relative_path: str,
    *,
    allow_missing: set[str] | None = None,
    level: str = "warn",
) -> None:
    """校验文档是否覆盖全部 source workflow。

    level="error"：用于必须穷举全部 workflow 的总览表，缺行直接报 ERROR，
                   避免出现"数量声明对得上但表里少一行"的静默漂移。
    level="warn" ：用于导航类文档（如 Hermes读取入口），不要求列全。
    """
    allow_missing = allow_missing or set()
    path = KB_ROOT / relative_path
    if not path.exists():
        report.error(f"doc/{relative_path}", f"缺少文件：{path}")
        return
    content = path.read_text(encoding="utf-8")
    source_ids = _source_workflow_ids()
    missing = sorted(wf_id for wf_id in source_ids if wf_id not in content and wf_id not in allow_missing)
    if missing:
        emit = report.error if level == "error" else report.warn
        emit(
            f"doc/{relative_path}/workflow_refs",
            f"未覆盖全部 workflow：缺少 {', '.join(missing[:8])}" + (" ..." if len(missing) > 8 else "")
        )
    else:
        report.ok(f"doc/{relative_path}/workflow_refs")


def check_overview_coverage(report: Report) -> None:
    # 唯一派发表必须穷举全部 workflow，缺行报 ERROR
    _check_workflow_refs_in_doc(report, "00-总览/Workflow命令映射表.md", level="error")
    # 其余总览/导航文件已瘦身为指针，不要求列全
    _check_workflow_refs_in_doc(report, "00-总览/Hermes读取入口.md", level="warn")


def check_dispatch_fresh(report: Report) -> None:
    """派发表必须是 gen_workflow_dispatch.py 的最新产物（确定性、防漂移）。"""
    gen = SOURCE_ROOT / "scripts/gen_workflow_dispatch.py"
    if not gen.exists():
        report.error("dispatch/generator", f"缺少生成器：{gen}")
        return
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(gen), "--check"],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        report.ok("dispatch/fresh")
    else:
        report.error(
            "dispatch/fresh",
            "派发表已过期，请运行 `python3 scripts/gen_workflow_dispatch.py` 重新生成"
            + (f"：{proc.stdout.strip()}" if proc.stdout.strip() else ""),
        )


def _load_hermes_auto_safe() -> set[str] | None:
    """读取唯一真相源 HERMES_AUTO_SAFE（在 sync_ai_knowledge_base.py 中定义）。

    优先 import；失败再退回正则解析，确保校验器不因导入问题而漏检。
    """
    try:
        sys.path.insert(0, str(SOURCE_ROOT / "scripts"))
        from sync_ai_knowledge_base import HERMES_AUTO_SAFE  # type: ignore
        return set(HERMES_AUTO_SAFE)
    except Exception:
        try:
            text = (SOURCE_ROOT / "scripts/sync_ai_knowledge_base.py").read_text(encoding="utf-8")
            block = text[text.index("HERMES_AUTO_SAFE"):]
            block = block[: block.index("}") + 1]
            return set(re.findall(r'"([a-z0-9_]+)"', block))
        except Exception:
            return None


# 承载「可自动/需确认」分类的唯一总览表（其余文档已瘦身为指针）
HERMES_CLASS_DOCS = [
    "00-总览/Workflow命令映射表.md",
]


def check_frontmatter_hermes(report: Report) -> None:
    """各 workflow 文件 frontmatter 的 hermes_auto 必须与白名单 HERMES_AUTO_SAFE 一致。

    frontmatter 是派发表的真相源，这里在源头处把分类钉死，避免生成表后才发现不符。
    """
    safe = _load_hermes_auto_safe()
    if safe is None:
        report.error("frontmatter_hermes/source", "无法读取 HERMES_AUTO_SAFE 白名单")
        return
    wf_dir = KB_ROOT / "01-工作流"
    mismatches: list[str] = []
    for path in sorted(wf_dir.glob("*.md")):
        wid = path.stem
        content = path.read_text(encoding="utf-8")
        m = re.search(r"^hermes_auto:\s*(true|false)\s*$", content, re.MULTILINE)
        if not m:
            mismatches.append(f"{wid}（缺 hermes_auto 字段）")
            continue
        actual = m.group(1) == "true"
        expected = wid in safe
        if actual != expected:
            mismatches.append(f"{wid}（frontmatter={actual}，白名单应为={expected}）")
    if mismatches:
        report.error("frontmatter_hermes", "frontmatter hermes_auto 与白名单不一致：" + "；".join(mismatches))
    else:
        report.ok("frontmatter_hermes")


def check_hermes_auto_consistency(report: Report) -> None:
    """校验总览表的「可自动/需确认」分类与白名单 HERMES_AUTO_SAFE 严格一致。

    白名单是唯一真相源；任何总览表与之不符即报 ERROR，避免 Hermes 误确认/误直跑。
    """
    safe = _load_hermes_auto_safe()
    if safe is None:
        report.error("hermes_auto/source", "无法读取 HERMES_AUTO_SAFE 白名单，分类一致性未校验")
        return

    for relative_path in HERMES_CLASS_DOCS:
        path = KB_ROOT / relative_path
        if not path.exists():
            report.error(f"hermes_auto/{relative_path}", f"缺少文件：{path}")
            continue
        mismatches: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.search(r"`([a-z0-9_]+)`", line)
            if not m:
                continue
            wid = m.group(1)
            if "可自动调用" in line:
                actual = "可自动"
            elif "需确认" in line:
                actual = "需确认"
            else:
                continue
            expected = "可自动" if wid in safe else "需确认"
            if actual != expected:
                mismatches.append(f"{wid}（表内={actual}，白名单应为={expected}）")
        if mismatches:
            report.error(
                f"hermes_auto/{relative_path}",
                "分类与白名单不一致：" + "；".join(mismatches),
            )
        else:
            report.ok(f"hermes_auto/{relative_path}")


def check_special_docs(report: Report) -> None:
    hermes_sop = KB_ROOT / "03-SOP/Hermes调用项目知识库SOP.md"
    if hermes_sop.exists():
        content = hermes_sop.read_text(encoding="utf-8")
        if AUTO_START in content and AUTO_END in content:
            report.ok("special/hermes_sop/auto_generated")
        else:
            report.error("special/hermes_sop/auto_generated", f"缺少 AUTO-GENERATED 标记：{hermes_sop}")
    xhs_prompt = KB_ROOT / "07-提示词/Hermes任务分发提示词.md"
    if xhs_prompt.exists():
        report.ok("special/hermes_prompt_exists")
    else:
        report.warn("special/hermes_prompt_exists", f"建议存在 Hermes 提示词：{xhs_prompt}")
    hermes_entry = KB_ROOT / "00-总览/Hermes读取入口.md"
    if hermes_entry.exists():
        content = hermes_entry.read_text(encoding="utf-8")
        if "如果 Hermes memory 与 AI Knowledge Base 内容冲突，以 AI Knowledge Base 为准" in content:
            report.ok("special/hermes_entry/memory_rule")
        else:
            report.error("special/hermes_entry/memory_rule", "Hermes读取入口缺少“memory 与知识库冲突时，以知识库为准”的规则")


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main() -> int:
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("knowledge_validate START")

    report = Report()

    # 1. 知识库根目录
    logger.info("1. 检查知识库根目录...")
    if not check_kb_root_exists(report):
        logger.error("知识库目录不存在，终止校验")
        _print_report(report, logger)
        return 1

    # 2. 必要目录
    logger.info("2. 检查必要目录...")
    check_required_dirs(report)

    # 3. 必要文件
    logger.info("3. 检查必要文件...")
    check_required_files(report)

    # 4. Workflow 文件
    logger.info("4. 检查 workflow 文件...")
    check_workflow_files(report)

    # 5. 孤立 KB 文件
    logger.info("5. 检查孤立 KB 文件...")
    check_no_extra_wf_in_kb(report)

    # 6. 同步日志
    logger.info("6. 检查同步日志...")
    check_sync_log_exists(report)

    # 7. 版本文件
    logger.info("7. 检查版本文件...")
    check_version_doc(report)

    # 8. 总览覆盖 + 派发表新鲜度
    logger.info("8. 检查总览覆盖与派发表新鲜度...")
    check_overview_coverage(report)
    check_dispatch_fresh(report)

    # 9. 特殊文档
    logger.info("9. 检查特殊文档...")
    check_special_docs(report)

    # 10. Hermes 可自动/需确认 分类一致性（白名单 ↔ 派发表 ↔ frontmatter）
    logger.info("10. 检查 Hermes 分类一致性...")
    check_hermes_auto_consistency(report)
    check_frontmatter_hermes(report)

    # 11. 入口文件与总览一致性
    logger.info("11. 检查入口文件与总览一致性...")
    check_entry_consistency(report)

    # 输出报告
    _print_report(report, logger)

    if report.has_error:
        logger.error("校验结果：存在 ERROR，请修复后重新运行")
        return 1
    else:
        logger.info("校验结果：通过 ✅")
        return 0


def _print_report(report: Report, logger: logging.Logger) -> None:
    logger.info("-" * 40)
    errors = [r for r in report.results if r.level == "ERROR"]
    warns = [r for r in report.results if r.level == "WARN"]

    if errors:
        logger.info("❌ ERROR 项目：")
        for r in errors:
            logger.error(f"  [{r.name}] {r.message}")

    if warns:
        logger.info("⚠️  WARN 项目：")
        for r in warns:
            logger.warning(f"  [{r.name}] {r.message}")

    logger.info(report.summary())
    logger.info("validate DONE")
    logger.info("=" * 60)

    # 写入校验报告文件
    report_path = KB_ROOT / "logs/knowledge_validate.log"
    # 报告已通过 FileHandler 实时写入，无需额外写文件


if __name__ == "__main__":
    sys.exit(main())
