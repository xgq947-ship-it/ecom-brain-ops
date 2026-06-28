"""示例平台插件（参考实现）。

⚠️ 本目录以下划线开头，会被平台发现逻辑（cli.py `_discover_and_register_platforms`）
**自动跳过**，因此不会被加载、不会污染真实命令——它纯粹是给你照抄的起点。

➡️ 新增一个平台：
  1. 复制本目录 `_example/` → `<你的平台名>/`（去掉下划线即激活）。
  2. 把下面的命令 / 处理函数换成你平台的真实逻辑（平台层可用 sessionhub 抓登录态、
     走 Playwright / CDP；业务规则不要写在这里）。
  3. 在 `sessionhub/config/sites/<你的平台名>.yaml` 写站点 / 场景配置（见 `_example.yaml`）。
  4. **无需改任何核心调度**：`ops` 启动时自动发现 `platforms/*/platform.py` 并调用 register()。

契约：每个平台目录必须有 `platform.py`，导出 `register(app, capabilities)`。
"""
from __future__ import annotations

import typer

from ops_cli.capabilities import CapabilitySpec
from ops_cli.cli_helpers import _execute


def _run_ping(*, name: str, dry_run: bool) -> dict:
    """示例处理函数：真实插件在这里调平台 API / 浏览器，返回 data 字典。"""
    if dry_run:
        return {"pong": True, "name": name, "dry_run": True, "note": "示例 dry-run，不访问任何平台"}
    return {"pong": True, "name": name}


def register(app: typer.Typer, capabilities: dict[str, CapabilitySpec]) -> None:
    example_app = typer.Typer(help="示例平台命令（参考实现）。", no_args_is_help=True)

    @example_app.command("ping")
    def example_ping(
        ctx: typer.Context,
        name: str = typer.Option("world", "--name", help="示例参数。"),
        dry_run: bool = typer.Option(False, "--dry-run", help="只返回模拟结果，不访问平台。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops example ping",
            params={"name": name, "dry_run": dry_run},
            handler=lambda: _run_ping(name=name, dry_run=dry_run),
        )

    # 声明能力：id / platform / command 是路由真相源；recovery_policy 控制登录态自动恢复策略。
    capabilities["example.ping"] = CapabilitySpec(
        id="example.ping",
        platform="example",
        command="ping",
        recovery_policy="never",
        artifact_types=(),
    )

    app.add_typer(example_app, name="example")
