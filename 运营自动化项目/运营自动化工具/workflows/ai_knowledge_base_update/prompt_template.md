# AI Knowledge Base 同步更新固定提示词

严格按下面流程执行：

1. 读取固定提示词
2. 读取最新项目
3. 调用当前 AI 会话进行理解
4. 更新 AI Knowledge Base
5. 校验是否合格

执行要求：

- 只更新 `<!-- AUTO-GENERATED:START -->` 与 `<!-- AUTO-GENERATED:END -->` 之间的内容。
- 不覆盖 AUTO-GENERATED 区域外的人工补充内容。
- 每个 Markdown 文件都必须保留 YAML frontmatter。
- 无法确认的命令、状态、能力，明确写“待确认”。
- 不写入 Cookie、Token、API Key、账号密码、订单号、客户信息。
- 不编造不存在的 workflow、task、CLI 命令或平台能力。
- `scripts/sync_ai_knowledge_base.py` 视为 legacy / fallback，不再作为主更新机制。
- **不要手写 `00-总览/Workflow命令映射表.md`（派发表）**：它由 `scripts/gen_workflow_dispatch.py` 从各 `01-工作流/<id>.md` 的 frontmatter 确定性生成（ADR-003），落盘后 workflow 会自动重建。你只需维护每个 workflow 文件的 frontmatter 机读字段：`cn_name`、`platform`、`triggers`（触发词数组）、`hermes_auto`（true/false）、`dry_run_cmd`、`run_cmd`。
- 每个 workflow 的 `hermes_auto` 必须严格按 `scripts/sync_ai_knowledge_base.py` 里的 `HERMES_AUTO_SAFE` 白名单取值：在白名单内为 `true`，否则为 `false`。不得自行判断或保留旧值；该白名单是唯一真相源（校验器 `frontmatter_hermes` 闸门会拦不一致）。
- 覆盖目录至少包括：`00-总览`、`01-工作流`、`02-平台能力`、`03-SOP`、`04-项目文档`、`07-提示词`、`08-决策记录`。
- 重点检查：新增 workflow、修改 workflow、废弃 workflow、删除 workflow、平台能力变化、命令入口变化、dry-run 变化、Hermes 入口、SOP 过期、小红书相关提示词角度。
- 如果源项目已无对应 workflow 文档，优先输出 `action: "archive"`，归档到 `99-归档/`，不要静默保留旧状态。

如果当前 AI 会话要把结果交回 workflow 落盘，请输出一个 `updates.json`，格式如下：

```json
{
  "documents": [
    {
      "path": "01-工作流/example.md",
      "frontmatter": {
        "cn_name": "示例工作流",
        "platform": "local",
        "triggers": ["example", "示例工作流"],
        "hermes_auto": false,
        "dry_run_cmd": "python3 run.py workflow example --dry-run",
        "run_cmd": "python3 run.py workflow example"
      },
      "auto_generated_markdown": "## 状态\n\nactive\n"
    },
    {
      "path": "01-工作流/old_workflow.md",
      "action": "archive",
      "auto_generated_markdown": "## 状态\n\narchived\n"
    }
  ]
}
```
