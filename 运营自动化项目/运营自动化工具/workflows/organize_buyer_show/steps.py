"""买家秀文件整理 workflow 的 step handler。

移植 Hermes skill `organize-buyer-show`，把"整理买家秀数据包"做成 step 化流程：

1. 删除图片 ≤N 张的低质量买家秀（默认 N=3）。
2. 去掉 SKU 层级，把所有买家秀平铺到根目录，清理空目录。

纯本地文件操作，不涉及任何平台调用。

安全策略（遵守 CLAUDE.md §4 / §7）：
- 删除、移动等破坏性动作只在 `not dry_run and flags.execute` 时执行。
- `--dry-run`：只扫描预览，绝不删除/移动。
- 无 `--execute`：即便非 dry-run 也只预览，提示加 `--execute` 才真正执行（删除不可逆）。
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from core.runtime import parse_workflow_args, StepContext, failure_result, success_result

IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}


# ---------------------------------------------------------------------------
# 纯函数：扫描 / 计划（可被测试单独 import）
# ---------------------------------------------------------------------------

def _is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMG_EXTS and not name.startswith(".")


def find_buyer_shows(base: Path) -> list[tuple[str, int, str]]:
    """递归定位买家秀叶子目录（含图片、无子目录）。

    返回 [(相对路径, 图片数, 绝对路径)]，与 Hermes skill 逻辑一致：
    - 叶子目录（有图片且无子目录）记为一个买家秀。
    - 有子目录则继续递归（支持数据包内嵌套 SKU 层级）。
    """
    base = Path(base)
    results: list[tuple[str, int, str]] = []

    def _walk(path: Path) -> None:
        for item in sorted(os.listdir(path)):
            if item.startswith("."):
                continue
            item_path = path / item
            if not item_path.is_dir():
                continue
            has_subdirs = any(
                (item_path / d).is_dir() and not d.startswith(".")
                for d in os.listdir(item_path)
            )
            imgs = [f for f in os.listdir(item_path) if _is_image(f)]
            if imgs and not has_subdirs:
                results.append((os.path.relpath(item_path, base), len(imgs), str(item_path)))
            elif has_subdirs:
                _walk(item_path)

    _walk(base)
    return results


def plan(base: Path, min_images: int) -> dict:
    """生成整理计划：低于等于阈值的删除，其余保留。"""
    buyers = find_buyer_shows(base)
    to_delete = [(n, c, p) for n, c, p in buyers if c <= min_images]
    to_keep = [(n, c, p) for n, c, p in buyers if c > min_images]
    return {"total": len(buyers), "to_delete": to_delete, "to_keep": to_keep}


# ---------------------------------------------------------------------------
# step handlers
# ---------------------------------------------------------------------------

def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", default=None, help="买家秀目标根目录")
    parser.add_argument("--min-images", type=int, default=3, help="删除阈值：图片数 ≤ 该值的买家秀视为低质")
    parser.add_argument("--no-flatten", action="store_true", help="只删低质，不去 SKU 层级")
    parser.add_argument("--execute", action="store_true", help="真正执行删除/平铺（破坏性动作必须显式开启）")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags

    if not flags.path:
        if flags.dry_run:
            ctx.state["no_input"] = True
            return success_result(outputs={"skipped": True, "reason": "缺少 --path（dry-run 安全预览）"})
        return failure_result("缺少必填参数：--path（买家秀目标根目录）")

    base = Path(flags.path).expanduser()
    if not base.is_dir():
        if flags.dry_run:
            ctx.state["no_input"] = True
            return success_result(outputs={"skipped": True, "reason": f"目标路径不存在：{base}"})
        return failure_result(f"目标路径不存在：{base}")

    ctx.state["base"] = base
    # 真正执行 = 非 dry-run 且显式 --execute；否则只预览（删除不可逆，需显式确认）
    ctx.state["will_execute"] = bool(flags.execute) and not flags.dry_run
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "path": str(base),
            "min_images": flags.min_images,
            "flatten": not flags.no_flatten,
            "will_execute": ctx.state["will_execute"],
        }
    )


def scan_preview(ctx: StepContext):
    """递归扫描并分类（只读），等价于 Hermes skill 的「预览扫描」。"""
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "无有效输入"})

    flags = ctx.state["flags"]
    result = plan(ctx.state["base"], flags.min_images)
    ctx.state["plan"] = result
    return success_result(
        outputs={
            "total": result["total"],
            "to_delete_count": len(result["to_delete"]),
            "to_keep_count": len(result["to_keep"]),
            "to_delete": [{"name": n, "images": c} for n, c, _ in result["to_delete"]],
            "to_keep": [{"name": n, "images": c} for n, c, _ in result["to_keep"]],
        }
    )


def delete_low_quality(ctx: StepContext):
    """第1步：删除图片 ≤ 阈值的低质买家秀（shutil.rmtree）。"""
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "无有效输入"})
    if not ctx.state.get("will_execute"):
        reason = "dry-run 不删除任何目录" if ctx.state["flags"].dry_run else "未带 --execute，仅预览不删除"
        return success_result(
            outputs={"skipped": True, "reason": reason, "would_delete": len(ctx.state["plan"]["to_delete"])}
        )

    base = ctx.state["base"]
    deleted = []
    for name, count, path in ctx.state["plan"]["to_delete"]:
        shutil.rmtree(path)
        deleted.append({"name": name, "images": count})
    ctx.state["deleted"] = deleted
    return success_result(outputs={"deleted_count": len(deleted), "deleted": deleted})


def flatten_sku(ctx: StepContext):
    """第2步：去掉 SKU 层级，把买家秀移到根目录，清理空目录。"""
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "无有效输入"})
    flags = ctx.state["flags"]
    if flags.no_flatten:
        return success_result(outputs={"skipped": True, "reason": "--no-flatten：跳过去层级"})
    if not ctx.state.get("will_execute"):
        reason = "dry-run 不移动任何目录" if flags.dry_run else "未带 --execute，仅预览不移动"
        return success_result(outputs={"skipped": True, "reason": reason})

    base = ctx.state["base"]

    # 收集买家秀，先做重名冲突检查（有冲突直接中止，杜绝覆盖）。
    # 同时检测「目标根目录已存在」和「同一批次内多个买家秀重名」两类冲突。
    buyers: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root, _dirs, files in os.walk(base, topdown=True):
        if Path(root) == base:
            continue
        imgs = [f for f in files if _is_image(f)]
        if imgs:
            buyer_name = os.path.basename(root)
            if (base / buyer_name).exists() or buyer_name in seen:
                return failure_result(
                    f"重名冲突：{buyer_name}（来自 {os.path.relpath(root, base)}），已中止，未移动任何文件"
                )
            seen.add(buyer_name)
            buyers.append((buyer_name, root))

    moved = []
    for name, src in buyers:
        shutil.move(src, str(base / name))
        moved.append(name)

    # 清理空目录（os.rmdir 非空自动跳过，杜绝误删）
    cleaned = 0
    for root, _dirs, _files in os.walk(base, topdown=False):
        if Path(root) == base:
            continue
        try:
            os.rmdir(root)
            cleaned += 1
        except OSError:
            pass

    ctx.state["moved"] = moved
    return success_result(outputs={"moved_count": len(moved), "cleaned_dirs": cleaned, "moved": moved})


def verify_collect(ctx: StepContext):
    """验证 + 汇总最终状态。"""
    flags = ctx.state["flags"]
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "无有效输入"})

    base = ctx.state["base"]
    final = []
    total_imgs = 0
    for item in sorted(os.listdir(base)):
        item_path = base / item
        if item_path.is_dir() and not item.startswith("."):
            imgs = [f for f in os.listdir(item_path) if _is_image(f)]
            final.append({"name": item, "images": len(imgs)})
            total_imgs += len(imgs)

    return success_result(
        outputs={
            "task": "organize_buyer_show",
            "dry_run": flags.dry_run,
            "executed": ctx.state.get("will_execute", False),
            "final_buyer_count": len(final),
            "final_image_count": total_imgs,
            "deleted": ctx.state.get("deleted", []),
            "moved": ctx.state.get("moved", []),
            "final": final,
        }
    )
