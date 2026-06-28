# ai_knowledge_base_update - AI知识库同步更新

这个 workflow 不内嵌某个固定模型客户端。

它负责统一这条流程：

1. 读取固定提示词
2. 扫描最新项目
3. 把更新请求包交给当前 AI 会话理解
4. 按 AI 产出的 `updates.json` 或直接编辑结果更新知识库
5. 调用 `scripts/validate_ai_knowledge_base.py` 校验

覆盖范围：

- `00-总览`
- `01-工作流`
- `02-平台能力`
- `03-SOP`
- `04-项目文档`
- `07-提示词`
- `08-决策记录`

并支持对源项目已删除 / 已废弃的 workflow 文档输出 `action: "archive"`，归档到 `99-归档/`。

## 入口

```bash
cd <项目根>/运营自动化工具

# 生成 prompt bundle，不落盘知识库
python3 run.py workflow ai_knowledge_base_update --source-root <项目根> --kb-root <ai知识库根>

# AI 生成 updates.json 后，交给 workflow 落盘并校验
python3 run.py workflow ai_knowledge_base_update \
  --source-root <项目根> \
  --kb-root <ai知识库根> \
  --updates-file /absolute/path/to/updates.json
```

## 说明

- 固定提示词模板：`workflows/ai_knowledge_base_update/prompt_template.md`
- 输出请求包：`运营自动化工具/runtime/ai_knowledge_base_update/latest_update_request.md`
- 只允许更新 `AUTO-GENERATED` 区域
- `scripts/sync_ai_knowledge_base.py` 仍保留，但只作为 legacy / fallback
