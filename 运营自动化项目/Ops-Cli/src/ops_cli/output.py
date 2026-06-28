import json
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Console


console = Console()


class CommandResponse(BaseModel):
    success: bool
    platform: str
    command: str
    data: dict[str, Any] = Field(default_factory=dict)


def emit_response(response: CommandResponse, *, as_json: bool) -> None:
    payload = response.model_dump()
    if as_json:
        console.print_json(data=payload)
        return

    console.print(f"[bold green]success[/bold green]: {response.success}")
    console.print(f"[bold]platform[/bold]: {response.platform}")
    console.print(f"[bold]command[/bold]: {response.command}")
    console.print_json(data=payload["data"])
