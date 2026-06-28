from ops_cli.output import CommandResponse


def list_orders() -> CommandResponse:
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="order list",
        data={"items": [], "total": 0, "mode": "mock"},
    )
