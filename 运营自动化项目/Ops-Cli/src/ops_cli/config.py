from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict


OPS_CLI_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = OPS_CLI_ROOT.parent


def _downloads_dir() -> str:
    return str(Path.home() / "Downloads")


def _sessionhub_root() -> str:
    return str(OPS_CLI_ROOT / "sessionhub")


def _jst_product_source_path() -> str:
    return str(PROJECT_ROOT / "主数据" / "聚水潭商品资料（最新）.xlsx")


def _tmcs_product_import_path() -> str:
    return str(Path.home() / "Downloads" / "猫超商品列表导出.xlsx")


def _tmcs_product_latest_path() -> str:
    return str(PROJECT_ROOT / "主数据" / "猫超商品列表导出 (最新）.xlsx")


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jst_base_url: str = ""
    jst_cookie: str = ""
    tmcs_base_url: str = ""
    tmcs_cookie: str = ""
    sessionhub_root: str = _sessionhub_root()
    primary_chrome_cdp_url: str = ""
    jst_order_stats_store: str = "（猫超）福安市启明工贸有限公司（肖国清）"
    jst_shops: str = ""
    jst_product_source_path: str = _jst_product_source_path()
    jst_product_keep_brands: tuple[str, ...] = ("奥克斯", "苏泊尔")
    tmcs_product_import_path: str = _tmcs_product_import_path()
    tmcs_product_latest_path: str = _tmcs_product_latest_path()
    tmcs_bill_download_dir: str = _downloads_dir()
    logs_dir: Path = Path("logs")
    data_dir: Path = Path("data")
    sandbox_dir: Path = Path("sandbox")
    runtime_dir: Path = Path("runtime")


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    env_path = Path.cwd() / ".env"
    raw = dotenv_values(env_path) if env_path.exists() else {}
    keep_brands_raw = raw.get("JST_PRODUCT_KEEP_BRANDS", "") or "奥克斯,苏泊尔"
    keep_brands = tuple(part.strip() for part in keep_brands_raw.split(",") if part.strip())
    return AppConfig(
        jst_base_url=raw.get("JST_BASE_URL", "") or "",
        jst_cookie=raw.get("JST_COOKIE", "") or "",
        tmcs_base_url=raw.get("TMCS_BASE_URL", "") or "",
        tmcs_cookie=raw.get("TMCS_COOKIE", "") or "",
        sessionhub_root=raw.get("SESSIONHUB_ROOT", "") or _sessionhub_root(),
        primary_chrome_cdp_url=raw.get("PRIMARY_CHROME_CDP_URL", "") or "",
        jst_order_stats_store=raw.get("JST_ORDER_STATS_STORE", "") or "（猫超）福安市启明工贸有限公司（肖国清）",
        jst_shops=raw.get("JST_SHOPS", "") or "",
        jst_product_source_path=raw.get("JST_PRODUCT_SOURCE_PATH", "") or _jst_product_source_path(),
        jst_product_keep_brands=keep_brands or ("奥克斯", "苏泊尔"),
        tmcs_product_import_path=raw.get("TMCS_PRODUCT_IMPORT_PATH", "") or _tmcs_product_import_path(),
        tmcs_product_latest_path=raw.get("TMCS_PRODUCT_LATEST_PATH", "") or _tmcs_product_latest_path(),
        tmcs_bill_download_dir=raw.get("TMCS_BILL_DOWNLOAD_DIR", "") or _downloads_dir(),
    )
