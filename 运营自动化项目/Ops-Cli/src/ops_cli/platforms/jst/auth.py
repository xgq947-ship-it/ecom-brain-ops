from ops_cli.output import CommandResponse
from ops_cli.platforms.auth_shared import AuthTarget
from ops_cli.platforms.auth_shared import capture_auth_target
from ops_cli.platforms.auth_shared import check_auth_target
from ops_cli.platforms.auth_shared import ensure_auth_target


JST_SITE = "jst_erp"
JST_ORDER_SCENE = "order_list"
TARGET = AuthTarget(platform="jst", site=JST_SITE, scene=JST_ORDER_SCENE)


def check_auth() -> CommandResponse:
    return check_auth_target(TARGET)


def ensure_auth() -> CommandResponse:
    return ensure_auth_target(TARGET)


def capture_auth() -> CommandResponse:
    return capture_auth_target(TARGET)
