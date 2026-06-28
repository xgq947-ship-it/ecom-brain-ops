# AGENTS.md - AI Knowledge Base 入口规则（骨架模板）

本目录是配套运营自动化项目（见同级 `../运营自动化项目`）的共享 AI Knowledge Base，
供 Hermes / Claude Code / Codex 读取。

> ⚠️ 这是**分发骨架**：目录结构与机制已就位，但正文内容应由配套运营项目通过
> `ai_knowledge_base_update` workflow 自动生成填充——请勿在此手工堆砌业务知识。

进入本目录后必须先读 `00-总览/Hermes读取入口.md`（骨架中为空，由 workflow 生成），
并按其中的**必读顺序与任务决策树**继续读取（版本 → 能力地图 → 工作流总览 →
命令映射 → 当前项目状态）。该文件是读取顺序的唯一权威来源。

如果历史 memory 与本知识库冲突，以本知识库为准。

## 更新规则

- 优先通过运营项目 workflow 更新（`<运营自动化项目>` / `<本目录>` 替换为实际路径）：

  ```bash
  python3 run.py workflow ai_knowledge_base_update \
    --source-root <运营自动化项目> \
    --kb-root <本目录>
  ```

- 只更新 `<!-- AUTO-GENERATED:START -->` 到 `<!-- AUTO-GENERATED:END -->` 之间的内容，
  除非用户明确要求修改人工区。
- 不随意新建目录，不改现有目录结构。
- 新增 workflow 后必须同步 `01-工作流/<workflow_id>.md`、总览文件、命令映射、当前状态，并运行校验。
- 校验命令：`cd <运营自动化项目> && python3 scripts/validate_ai_knowledge_base.py`

## 红线

禁止写入 Cookie、Token、Authorization、账号密码、客户隐私和真实订单敏感详情。
