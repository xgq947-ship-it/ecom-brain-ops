from ops_cli.output import CommandResponse
from ops_cli.platforms.auth_shared import AuthTarget
from ops_cli.platforms.auth_shared import capture_auth_target
from ops_cli.platforms.auth_shared import check_auth_target
from ops_cli.platforms.auth_shared import ensure_auth_target


def check_auth() -> CommandResponse:
    return check_auth_target(TARGET)


def ensure_auth() -> CommandResponse:
    return ensure_auth_target(TARGET)


def capture_auth() -> CommandResponse:
    return capture_auth_target(TARGET)


TMCS_SITE = "tmall_chaoshi"
TMCS_DEFAULT_SCENE = "maochao_item_search"
TARGET = AuthTarget(platform="tmcs", site=TMCS_SITE, scene=TMCS_DEFAULT_SCENE)
