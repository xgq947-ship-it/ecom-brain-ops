#!/usr/bin/env python3
"""确定性生成器：从 26 个 KB workflow 文件的 frontmatter 生成唯一「工作流派发表」。

真相源 = `01-工作流/<id>.md` 的 frontmatter（cn_name / platform / status /
triggers / hermes_auto / dry_run_cmd / run_cmd）。本脚本只读 frontmatter，
重建 `00-总览/Workflow命令映射表.md` 的 AUTO-GENERATED 区，幂等。

派生：单纯由 id 决定的规范命令不入 frontmatter；中文别名命令存 frontmatter。

用法：
  python3 gen_workflow_dispatch.py            # 写入
  python3 gen_workflow_dispatch.py --check    # 只校验是否最新（不一致 exit 1）
"""
from __future__ import annotations
import sys, pathlib, re

DEFAULT_KB_ROOT = pathlib.Path.home() / "Desktop" / "电商Brain-AI-Knowledge"
AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"


def _kb_root() -> pathlib.Path:
    if "--kb-root" in sys.argv:
        return pathlib.Path(sys.argv[sys.argv.index("--kb-root") + 1]).expanduser()
    return DEFAULT_KB_ROOT


KB_ROOT = _kb_root()
WF_DIR = KB_ROOT / "01-工作流"
DISPATCH = KB_ROOT / "00-总览/Workflow命令映射表.md"


def parse_frontmatter(text: str) -> dict:
    """极简 frontmatter 解析：平铺键、引号字符串、布尔、流式列表。"""
    if not text.startswith("---"):
        return {}
    end = text.index("\n---", 3)
    out: dict = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', val)
            out[key] = [i.replace('\\"', '"') for i in items]
        elif val in ("true", "false"):
            out[key] = val == "true"
        else:
            out[key] = val.strip('"')
    return out


def load_workflows() -> list[dict]:
    wfs = []
    for path in sorted(WF_DIR.glob("*.md")):
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        if fm.get("workflow_id"):
            wfs.append(fm)
    return wfs


def cmd_cell(val: str) -> str:
    """命令型值渲染为 inline code；说明型文字按纯文本（去掉内部反引号、转义竖线）。"""
    val = (val or "").strip()
    if not val:
        return ""
    if val.startswith("python3 ") or val.startswith("ops "):
        return f"`{val}`"
    return val.replace("`", "").replace("|", "\\|")


def build_block(wfs: list[dict]) -> str:
    auto = "✅ 可自动调用"
    confirm = "⚠️ 需确认"
    lines: list[str] = []
    lines.append("## 最后生成时间")
    lines.append("")
    lines.append("由 `scripts/gen_workflow_dispatch.py` 从各 workflow 文件 frontmatter 确定性生成，请勿手改本区。")
    lines.append("")
    lines.append(f"## 全量派发表（共 {len(wfs)} 个）")
    lines.append("")
    lines.append("| 中文名 | workflow_id | 平台 | 状态 | dry-run 命令 | 真实执行 | Hermes |")
    lines.append("|---|---|---|---|---|---|---|")
    for wf in sorted(wfs, key=lambda w: w["workflow_id"]):
        h = auto if wf.get("hermes_auto") else confirm
        lines.append(
            f"| {wf.get('cn_name','')} | `{wf['workflow_id']}` | {wf.get('platform','')} "
            f"| {wf.get('status','')} | {cmd_cell(wf.get('dry_run_cmd',''))} | {cmd_cell(wf.get('run_cmd',''))} | {h} |"
        )
    lines.append("")
    lines.append("## 触发词反查（中文需求 → workflow_id）")
    lines.append("")
    lines.append("| 触发词 | workflow_id |")
    lines.append("|---|---|")
    pairs: list[tuple[str, str]] = []
    for wf in wfs:
        for t in wf.get("triggers", []):
            pairs.append((t, wf["workflow_id"]))
    for trig, wid in sorted(pairs, key=lambda p: (p[1], p[0])):
        lines.append(f"| {trig} | `{wid}` |")
    lines.append("")
    return "\n".join(lines)


def render(wfs: list[dict]) -> str:
    content = DISPATCH.read_text(encoding="utf-8")
    block = build_block(wfs)
    head = content[: content.index(AUTO_START) + len(AUTO_START)]
    tail = content[content.index(AUTO_END):]
    return f"{head}\n\n{block}\n{tail}"


def main() -> int:
    wfs = load_workflows()
    new = render(wfs)
    if "--check" in sys.argv:
        cur = DISPATCH.read_text(encoding="utf-8")
        if cur != new:
            print("❌ 派发表已过期，请运行：python3 scripts/gen_workflow_dispatch.py")
            return 1
        print(f"✅ 派发表最新（{len(wfs)} 个 workflow）")
        return 0
    DISPATCH.write_text(new, encoding="utf-8")
    print(f"✅ 已生成派发表：{len(wfs)} 个 workflow → {DISPATCH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
