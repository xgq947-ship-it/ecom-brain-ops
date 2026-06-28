from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ops_cli.capabilities import current_capability_execution
from ops_cli.capabilities import mark_scene_refreshed
from ops_cli.capabilities import recovery_must_fail_fast
from ops_cli.capabilities import require_interactive_recovery
from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.runtime_context import write_runtime_context

from ops_cli.platforms.tmcs.shared import TMCS_BILL_DOWNLOAD_URL
from ops_cli.platforms.tmcs.shared import TMCS_BILL_EXPORT_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_BILL_LIST_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_BILL_LIST_URL
from ops_cli.platforms.tmcs.shared import TMCS_BILL_QUERY_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.platforms.tmcs.shared import TMCS_STATEMENT_LIST_FILENAME
from ops_cli.platforms.tmcs.shared import bill_period_overlaps
from ops_cli.platforms.tmcs.shared import build_download_query_payload
from ops_cli.platforms.tmcs.shared import check_scene_or_fail
from ops_cli.platforms.tmcs.shared import ensure_scene_assets
from ops_cli.platforms.tmcs.shared import extract_export_task_id
from ops_cli.platforms.tmcs.shared import filter_cookies_for_url
from ops_cli.platforms.tmcs.shared import find_download_url
from ops_cli.platforms.tmcs.shared import find_success_file_url
from ops_cli.platforms.tmcs.shared import find_success_file_url_by_keywords
from ops_cli.platforms.tmcs.shared import form_encode
from ops_cli.platforms.tmcs.shared import gei_task_download_url
from ops_cli.platforms.tmcs.shared import is_probably_excel
from ops_cli.platforms.tmcs.shared import is_probable_auth_error
from ops_cli.platforms.tmcs.shared import load_scene_or_fail
from ops_cli.platforms.tmcs.shared import merge_cookie_header
from ops_cli.platforms.tmcs.shared import parse_form_post_data
from ops_cli.platforms.tmcs.shared import parse_json_post_data
from ops_cli.platforms.tmcs.shared import previous_month_range
from ops_cli.platforms.tmcs.shared import read_json
from ops_cli.platforms.tmcs.shared import resolve_download_content
from ops_cli.platforms.tmcs.shared import sanitize_replay_headers
from ops_cli.platforms.tmcs.shared import tmcs_download
from ops_cli.platforms.tmcs.shared import tmcs_request
from ops_cli.platforms.tmcs.shared import unique_path
from ops_cli.platforms.tmcs.shared import write_json


TEMPLATE_PATH = Path("data/tmcs/bill_download_template.json")
DEFAULT_STATEMENT_TYPES = ("DISTRIBUTE_BILL", "DELEGATION_BILL")
DEFAULT_LAST_MONTH_QUERY_GRACE_DAYS = 3


def _required_download_scenes(*, download_statement_list: bool) -> tuple[str, ...]:
    scenes = [TMCS_BILL_LIST_SCENE, TMCS_BILL_QUERY_SCENE]
    if download_statement_list:
        scenes.insert(1, TMCS_BILL_EXPORT_SCENE)
    return tuple(scenes)


def _template_path() -> Path:
    return Path.cwd() / TEMPLATE_PATH


def _sanitize_tmcs_headers(
    headers: dict[str, Any],
    cookies: list[dict[str, Any]] | None = None,
    *,
    target_url: str | None = None,
) -> dict[str, str]:
    cleaned = sanitize_replay_headers(headers, [])
    cookie_header = merge_cookie_header({}, filter_cookies_for_url(cookies, target_url or "")).get("cookie")
    if cookie_header:
        cleaned["cookie"] = cookie_header
    return cleaned


def _scene_entry(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": scene.get("url"),
        "method": scene.get("method"),
        "headers": _sanitize_tmcs_headers(
            scene.get("headers") or {},
            scene.get("cookies") or [],
            target_url=str(scene.get("url") or ""),
        ),
        "cookies": list(scene.get("cookies") or []),
    }


def _request_headers(scene: dict[str, Any], target_url: str) -> dict[str, str]:
    headers = sanitize_replay_headers(dict(scene.get("headers") or {}), [])
    cookies = scene.get("cookies") or []
    if cookies:
        cookie_header = merge_cookie_header({}, filter_cookies_for_url(cookies, target_url)).get("cookie")
        if cookie_header:
            headers["cookie"] = cookie_header
    elif scene.get("headers", {}).get("cookie"):
        headers["cookie"] = str(scene["headers"]["cookie"])
    return headers


def _write_template(*, list_scene: dict[str, Any], export_scene: dict[str, Any], query_scene: dict[str, Any]) -> Path:
    template = {
        "site": TMCS_SITE,
        "scenes": {
            "bill_list": TMCS_BILL_LIST_SCENE,
            "statement_export": TMCS_BILL_EXPORT_SCENE,
            "download_query": TMCS_BILL_QUERY_SCENE,
        },
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "defaults": {
            "output_dir": get_config().tmcs_bill_download_dir,
            "statement_bill_type_list": list(DEFAULT_STATEMENT_TYPES),
        },
        "bill_list": _scene_entry(list_scene),
        "statement_export": {
            **_scene_entry(export_scene),
            "post_data_form": export_scene.get("post_data_form") or parse_form_post_data(export_scene.get("post_data"), TMCS_BILL_EXPORT_SCENE),
        },
        "download_query": {
            **_scene_entry(query_scene),
            "post_data_json": query_scene.get("post_data_json") or parse_json_post_data(query_scene.get("post_data"), TMCS_BILL_QUERY_SCENE),
        },
    }
    path = _template_path()
    write_json(path, template)
    return path


def _load_template(*, allow_refresh: bool = True) -> dict[str, Any]:
    path = _template_path()
    if not path.exists():
        raise RuntimeError(f"未找到猫超账单下载模板：{path}。请先运行 `ops tmcs bill learn`。")
    template = read_json(path)
    bill_list = template.get("bill_list") or {}
    statement_export = template.get("statement_export") or {}
    download_query = template.get("download_query") or {}
    if not bill_list or (
        bill_list
        and statement_export
        and download_query
        and ("cookies" not in bill_list or "cookies" not in statement_export or "cookies" not in download_query)
    ):
        execution = current_capability_execution()
        if allow_refresh and (execution is None or not execution.dry_run):
            learn_bill_download(force=False)
        else:
            return template
        template = read_json(path)
    return template


def _normalize_dates(*, start: str | None, end: str | None, last_month: bool) -> tuple[date, date, date]:
    if last_month or (start is None and end is None):
        begin, finish = previous_month_range()
        period_begin = datetime.strptime(begin, "%Y-%m-%d").date()
        period_finish = datetime.strptime(finish, "%Y-%m-%d").date()
        query_finish = period_finish + timedelta(days=DEFAULT_LAST_MONTH_QUERY_GRACE_DAYS)
        return period_begin, period_finish, query_finish
    if not start or not end:
        raise RuntimeError("请同时传入 --start 和 --end，或使用 --last-month。")
    try:
        begin = datetime.strptime(start, "%Y-%m-%d").date()
        finish = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise RuntimeError("日期格式必须是 YYYY-MM-DD。") from exc
    if begin > finish:
        raise RuntimeError("--start 不能晚于 --end。")
    return begin, finish, finish


def _extract_bill_items(payload: dict[str, Any], *, start: date, end: date) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        raise RuntimeError(f"无法识别账单列表返回结构：{payload}")
    source = data.get("dataSource") or data.get("list") or data.get("records") or []
    if not isinstance(source, list):
        raise RuntimeError("账单列表字段不是数组")
    items: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for row in source:
        if not isinstance(row, dict):
            continue
        bill_code = row.get("billCode")
        if not bill_code:
            continue
        bill_code_text = str(bill_code)
        if bill_code_text in seen_codes:
            continue
        if bill_period_overlaps(bill_code_text, start, end):
            items.append(row)
            seen_codes.add(bill_code_text)
    return items


def _list_bill_items(
    *,
    headers: dict[str, str],
    start: date,
    end: date,
    filter_start: date | None = None,
    filter_end: date | None = None,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    period_start = filter_start or start
    period_end = filter_end or end
    all_items: list[dict[str, Any]] = []
    page_index = 1
    while True:
        params = [
            ("billCreateTimeStart", start.isoformat()),
            ("billCreateTimeEnd", end.isoformat()),
            ("pageIndex", page_index),
            ("pageSize", page_size),
        ]
        for bill_type in DEFAULT_STATEMENT_TYPES:
            params.append(("statementBillTypeList", bill_type))
        status_code, payload, _ = tmcs_request("GET", TMCS_BILL_LIST_URL, headers=headers, params=params, data_body=None, json_body=None)
        del status_code
        items = _extract_bill_items(payload, start=period_start, end=period_end)
        all_items.extend(items)
        total_count = None
        data = payload.get("data", payload)
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if isinstance(data, dict):
            total = data.get("totalCount", data.get("total", data.get("count")))
            try:
                total_count = int(total) if total is not None else None
            except (TypeError, ValueError):
                total_count = None
            source = data.get("dataSource") or data.get("list") or data.get("records") or []
        else:
            source = []
        if total_count is not None and page_index * page_size >= total_count:
            break
        if not isinstance(source, list) or len(source) < page_size:
            break
        page_index += 1
    return all_items


def _query_download_file_url(*, headers: dict[str, str], query_scene: dict[str, Any], bill_code: str) -> str | None:
    payload = build_download_query_payload(dict(query_scene.get("post_data_json") or {}))
    _, parsed_payload, _ = tmcs_request(
        str(query_scene.get("method") or "POST"),
        str(query_scene.get("url") or ""),
        headers=_request_headers(query_scene, str(query_scene.get("url") or "")),
        json_body=payload,
    )
    return find_success_file_url(parsed_payload, bill_code)


def _query_statement_list_url(*, headers: dict[str, str], query_scene: dict[str, Any]) -> str | None:
    payload = build_download_query_payload(dict(query_scene.get("post_data_json") or {}))
    _, parsed_payload, _ = tmcs_request(
        str(query_scene.get("method") or "POST"),
        str(query_scene.get("url") or ""),
        headers=_request_headers(query_scene, str(query_scene.get("url") or "")),
        json_body=payload,
    )
    return find_success_file_url_by_keywords(parsed_payload, ("对账单列表", "wdk-finance-statement-bill-dynamic-list", "statement-bill-dynamic-list"))


def _download_bill_file(*, headers: dict[str, str], query_scene: dict[str, Any], bill_code: str, output_dir: Path) -> Path:
    output_path = output_dir / f"{bill_code}.xlsx"
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    status_code, payload, content = tmcs_request(
        "GET",
        TMCS_BILL_DOWNLOAD_URL,
        headers=headers,
        params={"billCodes": bill_code},
        json_body=None,
        data_body=None,
        timeout=120.0,
    )
    del status_code
    try:
        resolved_content, _ = resolve_download_content(content=content, parsed_payload=payload, headers=headers)
    except RuntimeError:
        query_headers = _request_headers(query_scene, str(query_scene.get("url") or ""))
        file_url = None
        for _ in range(24):
            file_url = _query_download_file_url(headers=query_headers, query_scene=query_scene, bill_code=bill_code)
            if file_url:
                break
            time.sleep(5)
        if not file_url:
            raise RuntimeError(f"下载任务已触发，但未找到 {bill_code} 的下载地址")
        # OSS 签名 URL 会把请求头纳入签名校验，不能继续带业务站点 Cookie/Header。
        _, nested_payload, nested_content = tmcs_download(file_url, headers=None)
        resolved_content, _ = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=None)
    if not resolved_content or not is_probably_excel(resolved_content):
        raise RuntimeError(f"{bill_code} 下载内容不是合法 Excel")
    output_path.write_bytes(resolved_content)
    return output_path


def _download_statement_list(*, headers: dict[str, str], export_scene: dict[str, Any], query_scene: dict[str, Any], start: date, end: date, output_dir: Path) -> Path:
    form_data = dict(export_scene.get("post_data_form") or {})
    if "_scm_token_" not in form_data or "query" not in form_data:
        raise RuntimeError("账单导出 scene 缺少 _scm_token_ 或 query")
    query = json.loads(form_data["query"])
    query["billCreateTimeStart"] = start.isoformat()
    query["billCreateTimeEnd"] = end.isoformat()
    query["pageIndex"] = 1
    form_data["query"] = json.dumps(query, ensure_ascii=False, separators=(",", ":"))
    _, payload, _ = tmcs_request(
        str(export_scene.get("method") or "POST"),
        str(export_scene.get("url") or ""),
        headers=_request_headers(export_scene, str(export_scene.get("url") or "")),
        data_body=form_encode(form_data),
    )
    task_id = extract_export_task_id(payload)
    if not task_id:
        raise RuntimeError(f"对账单列表导出接口未返回 taskId：{json.dumps(payload, ensure_ascii=False)[:500]}")
    gei_url = gei_task_download_url(str(export_scene.get("url") or ""), task_id)
    content = b""
    for _ in range(12):
        _, nested_payload, nested_content = tmcs_download(gei_url, headers=_request_headers(export_scene, gei_url))
        if is_probably_excel(nested_content):
            content = nested_content
            break
        try:
            content, _ = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=_request_headers(export_scene, gei_url))
            if content and is_probably_excel(content):
                break
        except RuntimeError:
            content = b""
        time.sleep(2)
    if not content:
        query_headers = _request_headers(query_scene, str(query_scene.get("url") or ""))
        for _ in range(24):
            file_url = _query_statement_list_url(headers=query_headers, query_scene=query_scene)
            if file_url:
                _, final_payload, final_content = tmcs_download(file_url, headers=None)
                content, _ = resolve_download_content(content=final_content, parsed_payload=final_payload, headers=None)
                if content and is_probably_excel(content):
                    break
            time.sleep(5)
    if not content or not is_probably_excel(content):
        raise RuntimeError("对账单列表下载内容不是合法 Excel")
    output_path = unique_path(output_dir / TMCS_STATEMENT_LIST_FILENAME)
    output_path.write_bytes(content)
    return output_path


def learn_bill_download(*, force: bool = False) -> CommandResponse:
    inputs = {"site": TMCS_SITE, "scenes": [TMCS_BILL_LIST_SCENE, TMCS_BILL_EXPORT_SCENE, TMCS_BILL_QUERY_SCENE], "force": force}
    list_path = None
    export_path = None
    query_path = None
    try:
        list_scene, list_check, list_path = ensure_scene_assets(
            site=TMCS_SITE,
            scene=TMCS_BILL_LIST_SCENE,
            force=force,
            next_command="ops tmcs auth capture",
        )
    except RuntimeError as exc:
        list_scene = load_scene_or_fail(TMCS_SITE, TMCS_BILL_LIST_SCENE, next_command="ops tmcs auth capture")
        list_path = Path(get_config().sessionhub_root).expanduser().resolve() / "data" / "sessions" / TMCS_SITE / f"{TMCS_BILL_LIST_SCENE}.json"
        list_check = {"status": "template_only", "warning": str(exc)}
    try:
        export_scene, export_check, export_path = ensure_scene_assets(
            site=TMCS_SITE,
            scene=TMCS_BILL_EXPORT_SCENE,
            force=force,
            next_command="ops tmcs auth capture",
        )
    except RuntimeError as exc:
        export_scene = load_scene_or_fail(TMCS_SITE, TMCS_BILL_EXPORT_SCENE, next_command="ops tmcs auth capture")
        export_path = Path(get_config().sessionhub_root).expanduser().resolve() / "data" / "sessions" / TMCS_SITE / f"{TMCS_BILL_EXPORT_SCENE}.json"
        export_check = {"status": "template_only", "warning": str(exc)}
    try:
        query_scene, query_check, query_path = ensure_scene_assets(
            site=TMCS_SITE,
            scene=TMCS_BILL_QUERY_SCENE,
            force=force,
            next_command="ops tmcs auth capture",
        )
    except RuntimeError as exc:
        query_scene = load_scene_or_fail(TMCS_SITE, TMCS_BILL_QUERY_SCENE, next_command="ops tmcs auth capture")
        query_path = Path(get_config().sessionhub_root).expanduser().resolve() / "data" / "sessions" / TMCS_SITE / f"{TMCS_BILL_QUERY_SCENE}.json"
        query_check = {"status": "template_only", "warning": str(exc)}
    template_path = _write_template(list_scene=list_scene, export_scene=export_scene, query_scene=query_scene)
    context_path = write_runtime_context(
        task_name="tmcs_bill_learn",
        status="success",
        inputs=inputs,
        outputs={
            "template_path": str(template_path),
            "list_scene_path": str(list_path),
            "export_scene_path": str(export_path),
            "query_scene_path": str(query_path),
            "list_check": list_check,
            "export_check": export_check,
            "query_check": query_check,
        },
        artifacts=[str(list_path), str(export_path), str(query_path), str(template_path)],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="bill learn",
        data={
            "site": TMCS_SITE,
            "bill_list_scene": TMCS_BILL_LIST_SCENE,
            "statement_export_scene": TMCS_BILL_EXPORT_SCENE,
            "download_query_scene": TMCS_BILL_QUERY_SCENE,
            "template_path": str(template_path),
            "context_path": str(context_path),
            "next_command": "ops --json tmcs bill download --last-month",
        },
    )


def run_bill_download(
    *,
    start: str | None = None,
    end: str | None = None,
    last_month: bool = False,
    download_statement_list: bool = False,
    dry_run: bool = False,
) -> CommandResponse:
    begin, finish, query_finish = _normalize_dates(start=start, end=end, last_month=last_month)

    if dry_run:
        template = _load_template(allow_refresh=False)
        output_dir = Path(str((template.get("defaults") or {}).get("output_dir") or get_config().tmcs_bill_download_dir)).expanduser()
        scene_warnings: list[str] = []
        for scene_name in _required_download_scenes(download_statement_list=download_statement_list):
            try:
                check_scene_or_fail(TMCS_SITE, scene_name, next_command="ops tmcs bill learn")
            except RuntimeError as exc:
                if recovery_must_fail_fast():
                    raise
                scene_warnings.append(str(exc))
        context_path = write_runtime_context(
            task_name="tmcs_bill_download_run",
            status="success",
            inputs={
                "start": begin.isoformat(),
                "end": finish.isoformat(),
                "query_start": begin.isoformat(),
                "query_end": query_finish.isoformat(),
                "query_grace_days": DEFAULT_LAST_MONTH_QUERY_GRACE_DAYS if query_finish != finish else 0,
                "last_month": last_month,
                "download_statement_list": download_statement_list,
                "dry_run": True,
            },
            outputs={
                "output_dir": str(output_dir),
                "bill_count": 0,
                "downloaded_files": [],
                "scene_warnings": scene_warnings,
            },
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="bill download",
            data={
                "start": begin.isoformat(),
                "end": finish.isoformat(),
                "query_start": begin.isoformat(),
                "query_end": query_finish.isoformat(),
                "query_grace_days": DEFAULT_LAST_MONTH_QUERY_GRACE_DAYS if query_finish != finish else 0,
                "output_dir": str(output_dir),
                "bill_list_scene": TMCS_BILL_LIST_SCENE,
                "statement_export_scene": TMCS_BILL_EXPORT_SCENE,
                "download_query_scene": TMCS_BILL_QUERY_SCENE,
                "download_statement_list": download_statement_list,
                "bill_count": 0,
                "downloaded_files": [],
                "scene_warnings": scene_warnings,
                "context_path": str(context_path),
                "dry_run": True,
            },
        )

    retried_for_auth = False
    auth_refresh_applied = False
    last_error: Exception | None = None
    while True:
        template = _load_template()
        output_dir = Path(str((template.get("defaults") or {}).get("output_dir") or get_config().tmcs_bill_download_dir)).expanduser()
        scene_warnings: list[str] = []
        for scene_name in _required_download_scenes(download_statement_list=download_statement_list):
            try:
                check_scene_or_fail(TMCS_SITE, scene_name, next_command="ops tmcs bill learn")
            except RuntimeError as exc:
                if recovery_must_fail_fast():
                    raise
                scene_warnings.append(str(exc))
        if auth_refresh_applied:
            scene_warnings.append("检测到账单下载鉴权失败，已自动强制刷新 SessionHub scenes 并重试一次。")

        bill_list_scene = template.get("bill_list") or {}
        export_scene = template.get("statement_export") or {}
        query_scene = template.get("download_query") or {}
        headers = _request_headers(bill_list_scene, TMCS_BILL_LIST_URL)

        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            items = _list_bill_items(headers=headers, start=begin, end=query_finish, filter_start=begin, filter_end=finish)
            downloaded_files: list[str] = []
            failed: list[dict[str, str]] = []

            statement_list_path = None
            if download_statement_list:
                statement_list_path = _download_statement_list(
                    headers=_request_headers(export_scene, str(export_scene.get("url") or "")),
                    export_scene=export_scene,
                    query_scene=query_scene,
                    start=begin,
                    end=query_finish,
                    output_dir=output_dir,
                )

            for row in items:
                bill_code = str(row.get("billCode"))
                try:
                    path = _download_bill_file(
                        headers=headers,
                        query_scene=query_scene,
                        bill_code=bill_code,
                        output_dir=output_dir,
                    )
                    downloaded_files.append(str(path))
                except Exception as exc:
                    failed.append({"bill_code": bill_code, "error": str(exc)})
        except Exception as exc:
            last_error = exc
            if not retried_for_auth and is_probable_auth_error(exc):
                require_interactive_recovery(TMCS_BILL_LIST_SCENE)
                learn_bill_download(force=True)
                mark_scene_refreshed(TMCS_BILL_LIST_SCENE)
                retried_for_auth = True
                auth_refresh_applied = True
                continue
            raise

        if failed and not retried_for_auth and is_probable_auth_error(failed[0]["error"]):
            require_interactive_recovery(TMCS_BILL_LIST_SCENE)
            learn_bill_download(force=True)
            mark_scene_refreshed(TMCS_BILL_LIST_SCENE)
            retried_for_auth = True
            auth_refresh_applied = True
            continue
        break

    context_path = write_runtime_context(
        task_name="tmcs_bill_download_run",
        status="success" if not failed else "failed",
        inputs={
            "start": begin.isoformat(),
            "end": finish.isoformat(),
            "query_start": begin.isoformat(),
            "query_end": query_finish.isoformat(),
            "query_grace_days": DEFAULT_LAST_MONTH_QUERY_GRACE_DAYS if query_finish != finish else 0,
            "last_month": last_month,
            "download_statement_list": download_statement_list,
            "dry_run": False,
        },
        outputs={
            "output_dir": str(output_dir),
            "bill_count": len(items),
            "downloaded_files": downloaded_files,
            "statement_list_path": str(statement_list_path) if statement_list_path else None,
            "failed": failed,
            "scene_warnings": scene_warnings,
        },
        artifacts=downloaded_files + ([str(statement_list_path)] if statement_list_path else []),
        errors=[item["error"] for item in failed],
    )

    if failed:
        raise RuntimeError(f"账单下载失败 {len(failed)} 个：{failed[0]['bill_code']} | {failed[0]['error']}")

    return CommandResponse(
        success=True,
        platform="tmcs",
        command="bill download",
        data={
            "start": begin.isoformat(),
            "end": finish.isoformat(),
            "query_start": begin.isoformat(),
            "query_end": query_finish.isoformat(),
            "query_grace_days": DEFAULT_LAST_MONTH_QUERY_GRACE_DAYS if query_finish != finish else 0,
            "output_dir": str(output_dir),
            "bill_list_scene": TMCS_BILL_LIST_SCENE,
            "statement_export_scene": TMCS_BILL_EXPORT_SCENE,
            "download_query_scene": TMCS_BILL_QUERY_SCENE,
            "download_statement_list": download_statement_list,
            "bill_count": len(items),
            "downloaded_files": downloaded_files,
            "statement_list_path": str(statement_list_path) if statement_list_path else None,
            "scene_warnings": scene_warnings,
            "context_path": str(context_path),
        },
    )
