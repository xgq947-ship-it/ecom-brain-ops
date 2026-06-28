from ops_cli.output import CommandResponse


def create_listing() -> CommandResponse:
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="listing create",
        data={"listing_id": "mock-listing-001", "status": "draft", "mode": "mock"},
    )
