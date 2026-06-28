from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ops_cli.capabilities import CapabilityExecution, CapabilitySpec, bind_capability_execution
from ops_cli.output import CommandResponse
from ops_cli.runtime_context import write_runtime_context


def _artifact_paths(data: dict[str, Any]) -> list[str]:
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list):
        return [str(item) for item in artifacts if item]
    paths: list[str] = []
    for key in ("output_path", "statement_list_path", "file_path"):
        value = data.get(key)
        if value:
            paths.append(str(value))
    downloaded = data.get("downloaded_files")
    if isinstance(downloaded, list):
        paths.extend(str(item) for item in downloaded if item)
    return list(dict.fromkeys(paths))


def _context_task_name(spec: CapabilitySpec) -> str:
    return f"capability_{spec.id.replace('.', '_').replace('-', '_')}"


def _update_existing_context(path: str | Path, recovery: dict[str, object]) -> None:
    context_path = Path(path)
    if not context_path.is_file():
        return
    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    outputs = payload.setdefault("outputs", {})
    if isinstance(outputs, dict):
        outputs["session_recovery"] = recovery
    context_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _decorate_success(
    spec: CapabilitySpec,
    params: dict[str, Any],
    response: CommandResponse,
    execution: CapabilityExecution,
) -> CommandResponse:
    data = response.data
    data.setdefault("capability_id", spec.id)
    data.setdefault("artifacts", _artifact_paths(data))
    if not response.success:
        data.setdefault("error_code", "PLATFORM_REQUEST_FAILED")
        data.setdefault("retryable", True)
        data.setdefault("required_scenes", list(spec.scenes))
        data.setdefault("recovery_hint", None)
    recovery = execution.recovery.as_dict()
    data["session_recovery"] = recovery
    if data.get("context_path"):
        _update_existing_context(str(data["context_path"]), recovery)
    else:
        context_path = write_runtime_context(
            task_name=_context_task_name(spec),
            status="success" if response.success else "failed",
            inputs=params,
            outputs={"capability_id": spec.id, "session_recovery": recovery},
            artifacts=data["artifacts"],
        )
        data["context_path"] = str(context_path)
    return response


def run_capability(
    *,
    spec: CapabilitySpec,
    params: dict[str, Any],
    handler: Callable[[], CommandResponse],
    interactive_login: bool | None,
) -> CommandResponse:
    with bind_capability_execution(
        spec,
        dry_run=bool(params.get("dry_run", False)),
        interactive_login=interactive_login,
    ) as execution:
        return _decorate_success(spec, params, handler(), execution)


def _classify_error(exc: Exception) -> tuple[str, bool, str | None]:
    text = str(exc)
    lowered = text.lower()
    # 聚水潭短信验证（如查询轨迹授权弹窗）：独立成结构化 AUTH_SMS_REQUIRED，
    # 让业务层（run.py 通用恢复）能区分「需要短信验证」与普通登录失效，从而创建
    # challenge + 经 Hermes 发飞书，而不是当成 PLATFORM_REQUEST_FAILED 处理。
    if any(token in text for token in ("需要完成短信验证", "短信验证", "授权验证")) or "AUTH_SMS_REQUIRED" in text:
        return (
            "AUTH_SMS_REQUIRED",
            False,
            "聚水潭需要短信验证。请在飞书回复验证码，Hermes 会填入 9222 浏览器完成验证后重试。",
        )
    if "FULFILLMENT_OVERVIEW_NOT_FOUND" in text:
        return (
            "FULFILLMENT_OVERVIEW_NOT_FOUND",
            False,
            "请先在主浏览器学习 天机 → 商家仓履约 → 日常考核 → 数据概览 页面后再读取履约数据。",
        )
    if "模板" in text or "template" in lowered:
        return "TEMPLATE_MISSING", False, None
    if any(word in lowered for word in ("auth", "session", "cookie", "401", "403", "unauthorized")) or any(
        word in text for word in ("登录", "鉴权", "scene 不可用", "Scene 校验")
    ):
        return "AUTH_REQUIRED", True, "请在交互终端执行同一命令，脚本会打开 9222 浏览器等待登录后继续。"
    if "捕获" in text or "复检" in text or "capture" in lowered:
        return "SCENE_CAPTURE_FAILED", True, "请在交互终端执行同一命令，完成登录后由脚本重新捕获 scene。"
    if any(word in text for word in ("Excel", "xlsx", "下载内容不是合法", "下载内容为空", "文件不存在")):
        return "ARTIFACT_INVALID", True, None
    return "PLATFORM_REQUEST_FAILED", True, None


def capability_failure_response(
    *,
    spec: CapabilitySpec,
    params: dict[str, Any],
    exc: Exception,
    interactive_login: bool | None,
) -> CommandResponse:
    code, retryable, recovery_hint = _classify_error(exc)
    response_diagnostics = getattr(exc, "response_diagnostics", None)
    with bind_capability_execution(
        spec,
        dry_run=bool(params.get("dry_run", False)),
        interactive_login=interactive_login,
    ) as execution:
        if code in {"AUTH_REQUIRED", "SCENE_CAPTURE_FAILED"}:
            execution.recovery.mark_required()
        recovery = execution.recovery.as_dict()
        outputs = {"capability_id": spec.id, "session_recovery": recovery}
        if isinstance(response_diagnostics, dict):
            outputs["response_diagnostics"] = response_diagnostics
        context_path = write_runtime_context(
            task_name=_context_task_name(spec),
            status="failed",
            inputs=params,
            outputs=outputs,
            errors=[str(exc)],
        )
    data = {
        "error": str(exc),
        "capability_id": spec.id,
        "artifacts": [],
        "context_path": str(context_path),
        "session_recovery": recovery,
        "error_code": code,
        "retryable": retryable,
        "required_scenes": list(spec.scenes),
        "recovery_hint": recovery_hint,
    }
    if isinstance(response_diagnostics, dict):
        data["response_diagnostics"] = response_diagnostics
    return CommandResponse(
        success=False,
        platform=spec.platform,
        command=spec.command,
        data=data,
    )
