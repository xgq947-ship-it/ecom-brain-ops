"""业务 workflow 包：每个子目录是一个 step 化的业务流程。

约定：每个 workflow 子目录提供 workflow.py，并导出 build_workflow() -> Workflow。
平台动作仍只能通过 clients/ops_cli_client.py 调 Ops-Cli；这里不写任何平台逻辑。
"""
