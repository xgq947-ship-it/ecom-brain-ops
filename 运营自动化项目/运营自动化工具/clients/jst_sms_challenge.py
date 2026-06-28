"""聚水潭短信验证 challenge 状态管理（通用，跨所有 JST workflow）。

定位：这是 workflow / shell wrapper / Hermes 脚本共享的**唯一** challenge schema 实现，
避免三处各写一份导致漂移。本模块只负责 challenge 文件的读写与状态机，**不发飞书、不连平台**：
- 飞书通知留在 ~/.hermes 脚本（Hermes 编排层）。
- 连 9222 / 检测 / 填码留在 Ops-Cli。

核心契约 `/tmp/jst_sms_pending.json`：记录「是哪一个原始 JST workflow + 哪些参数」触发了
短信验证，使验证码弹窗过期后能用**原 workflow**（而非写死 jst_pickup_watch）重新触发。

并发约束（第一版）：同一时间只允许一个处于 waiting_code 的 challenge。

安全：challenge 内容绝不含验证码明文（验证码只在提交时经 ctx.state / argv 一次性传递）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# 新契约（优先）与旧两行格式（兼容揽收监控历史脚本）。可用环境变量覆盖，便于测试隔离。
PENDING_JSON = Path(os.environ.get("JST_SMS_PENDING_JSON", "/tmp/jst_sms_pending.json"))
PENDING_LEGACY = Path(os.environ.get("JST_SMS_PENDING_LEGACY", "/tmp/jst_sms_pending"))

DEFAULT_TTL_SECONDS = 300  # 验证码弹窗约 5 分钟过期

STATUS_WAITING = "waiting_code"
STATUS_VERIFIED = "verified"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"


def _now() -> datetime:
    return datetime.now().astimezone()


def _fmt(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# run/error 文本里代表「聚水潭需要短信验证」的信号（与 Ops-Cli _classify_error 对齐）。
_SMS_ERROR_SIGNALS = ("AUTH_SMS_REQUIRED", "需要完成短信验证", "短信验证", "授权验证")


def errors_indicate_sms(errors: Any) -> bool:
    """判断一次 workflow 运行的错误列表是否表示「触发了聚水潭短信验证」。纯字符串检查，不连平台。"""
    if not errors:
        return False
    if isinstance(errors, str):
        errors = [errors]
    try:
        blob = "\n".join(str(e) for e in errors)
    except TypeError:
        blob = str(errors)
    return any(token in blob for token in _SMS_ERROR_SIGNALS)


def is_jst_workflow(workflow_id: str) -> bool:
    """是否为聚水潭(JST) workflow。短信验证恢复只对 JST workflow 生效。"""
    return bool(workflow_id) and workflow_id.startswith("jst")


def build_resume_command(workflow_id: str, args: list[str]) -> str:
    """从原 workflow_id + args 拼回可直接重跑的命令。"""
    parts = ["python3", "run.py", "workflow", workflow_id, *args]
    return " ".join(parts)


def make_challenge(
    *,
    workflow_id: str,
    workflow_name: str = "",
    args: list[str] | None = None,
    phone_mask: str | None = None,
    context_path: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """纯函数：构造 challenge dict（不落盘）。resume/trigger 命令均由原 workflow 推导。"""
    moment = now or _now()
    args = list(args or [])
    command = build_resume_command(workflow_id, args)
    challenge_id = f"jst_sms_{moment.strftime('%Y%m%d_%H%M%S')}"
    return {
        "challenge_id": challenge_id,
        "platform": "jst",
        "status": STATUS_WAITING,
        "workflow_id": workflow_id,
        "workflow_name": workflow_name or workflow_id,
        "args": args,
        "resume_command": command,
        # 第一版触发命令 == 重跑命令（原 workflow 是只读监控时安全；有副作用的后续应升级 step resume）。
        "trigger_command": command,
        "phone_mask": phone_mask,
        "context_path": context_path,
        "created_at": _fmt(moment),
        "expires_at": _fmt(moment + timedelta(seconds=max(ttl_seconds, 1))),
        "feishu_message_id": None,
    }


def _read_json() -> dict[str, Any] | None:
    if not PENDING_JSON.exists():
        return None
    try:
        payload = json.loads(PENDING_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_legacy() -> dict[str, Any] | None:
    """兼容旧两行格式：第1行 workflow_id，第2行起为参数。"""
    if not PENDING_LEGACY.exists():
        return None
    try:
        lines = [ln.strip() for ln in PENDING_LEGACY.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return None
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    workflow_id = lines[0]
    # 旧文件历史上也可能只写了一个时间戳（非 workflow_id），此时退回 jst_pickup_watch。
    if workflow_id.replace("-", "").replace(":", "").replace(" ", "").isdigit():
        workflow_id = "jst_pickup_watch"
        args: list[str] = []
    else:
        args = lines[1:]
    challenge = make_challenge(workflow_id=workflow_id, args=args)
    challenge["source"] = "legacy_pending"
    return challenge


def _is_expired(challenge: dict[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = challenge.get("expires_at")
    if not expires_at:
        return False
    try:
        deadline = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return False
    moment = now or _now()
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=moment.tzinfo)
    return moment > deadline


def read_challenge() -> dict[str, Any] | None:
    """读取当前 challenge：优先新 JSON，回退旧两行格式。不做过期判定。"""
    return _read_json() or _read_legacy()


def read_challenge_path(path: str | Path) -> dict[str, Any] | None:
    """从指定路径读取 challenge（支持 --challenge-file 显式传入任意路径）。

    JSON 文件按新 schema 解析；若是旧两行格式则退化解析出 workflow_id + args。
    """
    p = Path(path)
    if not p.exists():
        return None
    text = ""
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None
    # 旧两行格式
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    challenge = make_challenge(workflow_id=lines[0], args=lines[1:])
    challenge["source"] = "legacy_pending"
    return challenge


def update_challenge_path(path: str | Path, status: str, **fields: Any) -> dict[str, Any] | None:
    """更新指定路径 challenge 的状态并写回。无法读取时返回 None。"""
    challenge = read_challenge_path(path)
    if challenge is None:
        return None
    challenge["status"] = status
    challenge.update(fields)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(challenge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return challenge


def active_challenge(*, now: datetime | None = None) -> dict[str, Any] | None:
    """返回仍在等待验证码且未过期的 challenge；已过期则落盘标记 expired 并返回 None。"""
    challenge = read_challenge()
    if challenge is None:
        return None
    if challenge.get("status") not in (STATUS_WAITING, None):
        return None
    if _is_expired(challenge, now=now):
        challenge["status"] = STATUS_EXPIRED
        _write_json(challenge)
        return None
    return challenge


def _write_json(challenge: dict[str, Any]) -> None:
    PENDING_JSON.parent.mkdir(parents=True, exist_ok=True)
    PENDING_JSON.write_text(json.dumps(challenge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_legacy(challenge: dict[str, Any]) -> None:
    lines = [str(challenge.get("workflow_id") or ""), *[str(a) for a in challenge.get("args") or []]]
    PENDING_LEGACY.parent.mkdir(parents=True, exist_ok=True)
    PENDING_LEGACY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_challenge(challenge: dict[str, Any]) -> dict[str, Any]:
    """落盘 challenge：写新 JSON，同时写旧两行格式以兼容存量脚本。"""
    _write_json(challenge)
    _write_legacy(challenge)
    return challenge


def create_challenge(
    *,
    workflow_id: str,
    workflow_name: str = "",
    args: list[str] | None = None,
    phone_mask: str | None = None,
    context_path: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """创建 challenge。单并发：已有未过期 waiting_code 时不新建，返回 (现有, False)。

    返回 (challenge, created)。created=False 表示沿用既有 challenge（调用方据此避免重复发飞书）。
    """
    existing = active_challenge(now=now)
    if existing is not None:
        return existing, False
    challenge = make_challenge(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        args=args,
        phone_mask=phone_mask,
        context_path=context_path,
        ttl_seconds=ttl_seconds,
        now=now,
    )
    write_challenge(challenge)
    return challenge, True


def mark_status(status: str, **fields: Any) -> dict[str, Any] | None:
    """更新当前 challenge 状态与字段并落盘。无 challenge 时返回 None。"""
    challenge = read_challenge()
    if challenge is None:
        return None
    challenge["status"] = status
    challenge.update(fields)
    _write_json(challenge)
    return challenge


def clear_challenge() -> None:
    """删除 challenge 文件（新 + 旧）。"""
    for path in (PENDING_JSON, PENDING_LEGACY):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def cancel_challenge() -> dict[str, Any] | None:
    """取消当前 challenge：先标记 cancelled（留痕），再清理文件。返回被取消的 challenge。"""
    challenge = read_challenge()
    if challenge is None:
        return None
    challenge["status"] = STATUS_CANCELLED
    clear_challenge()
    return challenge


def status_summary(challenge: dict[str, Any] | None) -> str:
    """生成给人看的状态文案（绝不含验证码明文）。"""
    if challenge is None:
        return "当前没有待处理的聚水潭短信验证。"
    return (
        f"challenge={challenge.get('challenge_id')} 状态={challenge.get('status')} "
        f"任务={challenge.get('workflow_name')} 手机号={challenge.get('phone_mask') or '未知'} "
        f"过期={challenge.get('expires_at')}"
    )
