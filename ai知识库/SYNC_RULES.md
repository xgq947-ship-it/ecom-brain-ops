# SYNC_RULES

AI Knowledge Base 更新以配套运营项目的 workflow 为主（`<运营自动化项目>` / `<本目录>`
替换为实际路径）：

```bash
cd <运营自动化项目>/运营自动化工具
python3 run.py workflow ai_knowledge_base_update \
  --source-root <运营自动化项目> \
  --kb-root <本目录>
```

## 同步原则

- 当前 AI 会话负责理解项目变化并生成知识库内容。
- workflow 负责生成更新请求包、应用 `updates.json`、刷新版本、执行校验。
- `scripts/sync_ai_knowledge_base.py` 只作为 legacy / fallback。
- 只更新 AUTO-GENERATED 区域，人工区除非用户明确要求不改。
- **workflow 路由信息单源化**：cn_name / platform / triggers / hermes_auto /
  dry_run_cmd / run_cmd 的真相源是 `01-工作流/<id>.md` 的 frontmatter；全量派发表
  `00-总览/Workflow命令映射表.md` 由 `scripts/gen_workflow_dispatch.py` 确定性生成，
  **不要手改其 AUTO 区**。
- 新增 / 修改 workflow：改 `01-工作流/<id>.md` frontmatter → 跑生成器 → 跑校验。
- 删除或废弃 workflow 时，优先归档到 `99-归档/`，不得静默保留 active 状态。
- 更新后必须运行 `python3 scripts/gen_workflow_dispatch.py && python3 scripts/validate_ai_knowledge_base.py`。

## 禁止

- 随意新增目录或改变 `00-总览` 到 `08-决策记录` 的结构。
- 写入 Cookie、Token、Authorization、账号密码、客户隐私。
- 编造不存在的 workflow、CLI 命令或平台能力。
- 让 README、系统能力地图、当前项目状态出现不同 workflow 数量。
