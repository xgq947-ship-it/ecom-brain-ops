"""Shared helpers for CLI command execution."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import typer

from ops_cli.capabilities import capability_for_command
from ops_cli.config import get_config
from ops_cli.execution import capability_failure_response, run_capability
from ops_cli.logger import log_command, setup_logger
from ops_cli.output import CommandResponse, emit_response


def _get_json_flag(ctx: typer.Context) -> bool:
    return bool((ctx.obj or {}).get("json_output", False))


def _execute(
    ctx: typer.Context,
    *,
    command_name: str,
    params: dict[str, Any],
    handler: Callable[[], CommandResponse],
    force_json: bool = False,
) -> None:
    setup_logger()
    get_config()
    started_at = datetime.now().isoformat(timespec="seconds")
    command_parts = command_name.split()
    platform = command_parts[1]
    command = " ".join(command_parts[2:])
    spec = capability_for_command(platform, command)
    interactive_login = (ctx.obj or {}).get("interactive_login")
    try:
        response = run_capability(
            spec=spec,
            params=params,
            handler=handler,
            interactive_login=interactive_login,
        )
    except Exception as exc:
        response = capability_failure_response(
            spec=spec,
            params=params,
            exc=exc,
            interactive_login=interactive_login,
        )
        emit_response(response, as_json=_get_json_flag(ctx) or force_json)
        log_command(
            {
                "timestamp": started_at,
                "command": command_name,
                "params": params,
                "result": response.model_dump(),
            }
        )
        raise typer.Exit(code=1)

    emit_response(response, as_json=_get_json_flag(ctx) or force_json)
    log_command(
        {
            "timestamp": started_at,
            "command": command_name,
            "params": params,
            "result": response.model_dump(),
        }
    )
