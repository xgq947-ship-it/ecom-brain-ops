"""公司 NAS 域的共享基础设施：挂载、产品资料根目录、品牌/类目常量、编码归一化。

company_nas_index 与 company_nas_listing 两个 workflow 共用本模块，避免业务逻辑
互相 import。本模块只做 NAS 文件系统访问与纯文本归一化，不碰 Ops-Cli 平台
（猫超/聚水潭），也不写任何 workflow 业务编排。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from core.config_loader import get_path

NAS_URL = "https://suolong.synology.me:5006"
NAS_MOUNT_NAME = "suolong.synology.me"
DEFAULT_NAS_MOUNT = get_path("company_nas_mount")

BRAND_FOLDERS = {
    "奥克斯": "1.奥克斯",
    "志高": "2.志高",
    "苏泊尔": "4.苏泊尔",
    "QTQ": "5.QTQ",
    "佳健仕": "6.佳健仕",
    "蓝宝": "7.蓝宝",
    "名创优品": "8.名创优品",
    "礼品": "9.礼品图",
    "南极人": "10.南极人",
}

NAS_CATEGORIES = (
    "10.奥克斯500强修改",
    "11.南极人角标修改",
    "5.联想",
    "6.索隆",
    "7.俞兆林 北极绒",
    "8.按摩椅(旧)",
    "分销产品",
    "刮痧仪",
    "办公椅",
    "加热围巾",
    "品牌方苏泊尔详情",
    "按摩椅",
    "按摩床垫",
    "按摩垫",
    "按摩座垫",
    "按摩披肩",
    "按摩枕",
    "按摩棒",
    "按摩靠垫",
    "拔罐仪",
    "披肩",
    "揉腹仪",
    "榻榻米",
    "甩脂机",
    "甩脂腰带",
    "电竞椅",
    "盐袋",
    "筋膜枪",
    "腰腹按摩器",
    "腰部按摩器",
    "膝盖按摩器",
    "膝部按摩",
    "赠品PNG",
    "足部按摩器",
    "趴趴枕",
    "足疗机",
    "足浴盆",
    "护膝",
    "护颈仪",
    "护眼仪",
    "护腰带",
    "头部按摩器",
    "小腿按摩器",
    "手部按摩器",
)

SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True)


def active_nas_mount() -> Path | None:
    if DEFAULT_NAS_MOUNT.exists():
        return DEFAULT_NAS_MOUNT
    volumes = DEFAULT_NAS_MOUNT.parent
    matches = sorted(
        path for path in volumes.glob(f"{NAS_MOUNT_NAME}*")
        if path.exists() and path.is_dir()
    )
    return matches[0] if matches else None


def nas_product_root() -> Path:
    configured = get_path("company_nas_product_root")
    if configured.exists():
        return configured
    mount = active_nas_mount() or DEFAULT_NAS_MOUNT
    return mount / "产品资料（运营）" / "1.产品资料"


def is_mounted() -> bool:
    return active_nas_mount() is not None


def mount_nas() -> None:
    if is_mounted():
        return
    result = run_command(["osascript", "-e", f'tell application "Finder" to mount volume "{NAS_URL}"'])
    if result.returncode != 0:
        raise SystemExit(f"NAS 挂载失败，请确认公司网络和钥匙串认证：{result.stderr.strip() or result.stdout.strip()}")
    mount = active_nas_mount()
    if not mount:
        raise SystemExit(f"NAS 挂载后仍找不到挂载点：{DEFAULT_NAS_MOUNT}")


def unmount_nas() -> dict[str, Any]:
    mount = active_nas_mount()
    if not mount:
        return {"attempted": False, "success": True, "message": "NAS 未挂载"}
    result = run_command(["umount", str(mount)])
    if result.returncode != 0 and "Resource busy" in (result.stderr or result.stdout):
        fallback = run_command(["diskutil", "unmount", str(mount)])
        return {
            "attempted": True,
            "success": fallback.returncode == 0,
            "message": (
                (fallback.stderr.strip() or fallback.stdout.strip())
                if fallback.returncode == 0
                else f"{result.stderr.strip() or result.stdout.strip()} | diskutil: {fallback.stderr.strip() or fallback.stdout.strip()}"
            ),
        }
    return {
        "attempted": True,
        "success": result.returncode == 0,
        "message": result.stderr.strip() or result.stdout.strip(),
    }


def normalize_code_text(value: Any) -> str:
    return re.sub(r"[\s\-/_.]+", "", str(value or "").strip()).lower()
