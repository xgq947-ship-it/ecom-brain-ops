from __future__ import annotations

import copy
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ops_cli.capabilities import current_capability_execution
from ops_cli.capabilities import mark_scene_refreshed
from ops_cli.capabilities import recovery_must_fail_fast
from ops_cli.capabilities import require_interactive_recovery
from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.runtime_context import write_runtime_context

from ops_cli.platforms.tmcs.shared import TMCS_BILL_QUERY_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.platforms.tmcs.shared import check_scene_or_fail
from ops_cli.platforms.tmcs.shared import ensure_scene_assets
from ops_cli.platforms.tmcs.shared import extract_export_task_id
from ops_cli.platforms.tmcs.shared import filter_cookies_for_url
from ops_cli.platforms.tmcs.shared import find_download_url
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
from ops_cli.platforms.tmcs.shared import scene_store_path
from ops_cli.platforms.tmcs.shared import tmcs_download
from ops_cli.platforms.tmcs.shared import tmcs_request
from ops_cli.platforms.tmcs.shared import unique_path
from ops_cli.platforms.tmcs.shared import write_json


TEMPLATE_PATH = Path("data/tmcs/promotion_bill_template.json")
SOURCE_ALL = "all"
SOURCE_CONFIGS = {
    "zdx": {
        "label": "智多星",
        "scene": "tmcs_promotion_zdx_bill_export",
        "filename": "智多星推广账单",
        "default_extension": ".xlsx",
        "download_keywords": ("智多星", "zdx", "promotion"),
        "probe_keywords": ("智多星", "zhiduoxing", "zdx", "推广"),
    },
    "wxt": {
        "label": "万象台",
        "scene": "tmcs_promotion_wxt_bill_export",
        "filename": "万象台推广账单",
        "default_extension": ".csv",
        "download_keywords": ("万象台", "wxt", "adbrain", "exportAccountJournal"),
        "probe_keywords": ("万象台", "wanxiangtai", "wxt", "adbrain", "alimama", "exportAccountJournal"),
    },
}


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


def _normalize_source(source: str) -> list[str]:
    value = str(source or SOURCE_ALL).strip().lower()
    if value == SOURCE_ALL:
        return list(SOURCE_CONFIGS)
    if value not in SOURCE_CONFIGS:
        raise RuntimeError("--source 仅支持 all、zdx、wxt。")
    return [value]


def _normalize_dates(*, start: str | None, end: str | None, last_month: bool) -> tuple[date, date]:
    if last_month or (start is None and end is None):
        begin, finish = previous_month_range()
        return datetime.strptime(begin, "%Y-%m-%d").date(), datetime.strptime(finish, "%Y-%m-%d").date()
    if not start or not end:
        raise RuntimeError("请同时传入 --start 和 --end，或使用 --last-month。")
    try:
        begin = datetime.strptime(start, "%Y-%m-%d").date()
        finish = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise RuntimeError("日期格式必须是 YYYY-MM-DD。") from exc
    if begin > finish:
        raise RuntimeError("--start 不能晚于 --end。")
    return begin, finish


def _apply_date_placeholders(value: Any, start: str, end: str) -> Any:
    if isinstance(value, dict):
        return {key: _apply_date_placeholders(item, start, end) for key, item in value.items()}
    if isinstance(value, list):
        return [_apply_date_placeholders(item, start, end) for item in value]
    if isinstance(value, str):
        return value.replace("__START_DATE__", start).replace("__END_DATE__", end)
    return value


def _parameterize_dates(value: Any) -> Any:
    # Captured requests usually contain exactly a start/end date pair. Keep the
    # heuristic deliberately conservative so unknown fields remain untouched.
    date_strings: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                collect(nested)
        elif isinstance(item, list):
            for nested in item:
                collect(nested)
        elif isinstance(item, str):
            for token in item.replace("T", " ").split():
                if len(token) >= 10 and token[:10].count("-") == 2:
                    date_strings.append(token[:10])

    collect(value)
    unique_dates = []
    for item in date_strings:
        if item not in unique_dates:
            unique_dates.append(item)
    if len(unique_dates) < 2:
        return value

    start_date, end_date = unique_dates[0], unique_dates[-1]

    def replace(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: replace(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [replace(nested) for nested in item]
        if isinstance(item, str):
            return item.replace(start_date, "__START_DATE__").replace(end_date, "__END_DATE__")
        return item

    return replace(value)


def _is_probably_csv(content: bytes) -> bool:
    if not content:
        return False
    sample = content[:4096]
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:]
    stripped = sample.lstrip()
    if stripped.startswith((b"{", b"[")):
        return False
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = sample.decode("gb18030")
        except UnicodeDecodeError:
            return False
    lines = text.splitlines()
    if not lines:
        return False
    first_line = lines[0]
    delimiter_count = first_line.count(",") + first_line.count("\t")
    if delimiter_count < 2:
        return False
    return len(lines) > 1 or sample.endswith((b"\n", b"\r\n"))


def _spreadsheet_suffix(content: bytes, source: str) -> str:
    if is_probably_excel(content):
        return ".xlsx"
    if _is_probably_csv(content):
        return ".csv"
    return str(SOURCE_CONFIGS[source].get("default_extension") or ".xlsx")


def _is_probably_spreadsheet(content: bytes) -> bool:
    return is_probably_excel(content) or _is_probably_csv(content)


def _raise_if_auth_payload(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("success") is not False:
        return
    serialized = json.dumps(payload, ensure_ascii=False)
    if not is_probable_auth_error(serialized):
        return
    message = (
        payload.get("errorMessage")
        or payload.get("message")
        or payload.get("msg")
        or payload.get("errorCode")
        or "平台登录态失效"
    )
    raise RuntimeError(f"平台登录态失效：{message}")


def _source_file_path(output_dir: Path, source: str, start: date, suffix: str | None = None) -> Path:
    extension = suffix or str(SOURCE_CONFIGS[source].get("default_extension") or ".xlsx")
    filename = f"{SOURCE_CONFIGS[source]['filename']}_{start.strftime('%Y-%m')}{extension}"
    return unique_path(output_dir / filename)


def _existing_source_file(output_dir: Path, source: str, start: date) -> Path | None:
    month_tag = start.strftime("%Y-%m")
    names = [SOURCE_CONFIGS[source]["filename"]]
    if source == "wxt":
        names.append("万相台推广账单")
    suffixes = (".xlsx", ".xls", ".csv")
    candidates: list[Path] = []
    for name in names:
        for suffix in suffixes:
            candidates.extend(output_dir.glob(f"{name}_{month_tag}*{suffix}"))
    aliases = SOURCE_CONFIGS[source]["download_keywords"]
    directory_entries = output_dir.iterdir() if output_dir.exists() else []
    for path in directory_entries:
        if not path.is_file() or path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in suffixes:
            continue
        if month_tag not in path.name:
            continue
        if any(keyword and keyword in path.name for keyword in aliases):
            candidates.append(path)
    unique = {path.resolve(): None for path in candidates if path.exists() and path.is_file()}
    if not unique:
        return None
    return sorted(unique, key=lambda item: item.stat().st_mtime, reverse=True)[0]


def _scene_to_template(scene: dict[str, Any]) -> dict[str, Any]:
    method = str(scene.get("method") or "POST").upper()
    raw_form = scene.get("post_data_form")
    raw_json = scene.get("post_data_json")
    post_data = scene.get("post_data")
    payload: dict[str, Any] = {
        "url": scene.get("url"),
        "method": method,
        "headers": _sanitize_tmcs_headers(
            scene.get("headers") or {},
            scene.get("cookies") or [],
            target_url=str(scene.get("url") or ""),
        ),
        "cookies": list(scene.get("cookies") or []),
    }
    if raw_json:
        payload["post_data_json"] = _parameterize_dates(raw_json)
    elif raw_form:
        payload["post_data_form"] = _parameterize_dates(raw_form)
    elif method == "POST" and post_data:
        try:
            payload["post_data_json"] = _parameterize_dates(parse_json_post_data(post_data, str(scene.get("scene") or "promotion_bill")))
        except RuntimeError:
            payload["post_data_form"] = _parameterize_dates(parse_form_post_data(post_data, str(scene.get("scene") or "promotion_bill")))
    return payload


def _write_template(*, sources: dict[str, dict[str, Any]], query_scene: dict[str, Any] | None = None) -> Path:
    template = {
        "site": TMCS_SITE,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "defaults": {"output_dir": get_config().tmcs_bill_download_dir},
        "source_scenes": {source: SOURCE_CONFIGS[source]["scene"] for source in SOURCE_CONFIGS},
        "sources": sources,
        "download_query": _scene_to_template(query_scene) if query_scene else {},
    }
    path = _template_path()
    write_json(path, template)
    return path


def _load_template(*, allow_refresh: bool = True) -> dict[str, Any]:
    path = _template_path()
    if not path.exists():
        raise RuntimeError(f"未找到猫超推广账单模板：{path}。请先运行 `ops tmcs promotion-bill learn --source all`。")
    template = read_json(path)
    sources = template.get("sources") or {}
    query_scene = template.get("download_query") or {}
    if any("cookies" not in source for source in sources.values() if isinstance(source, dict)) or (
        query_scene and "cookies" not in query_scene
    ):
        execution = current_capability_execution()
        if allow_refresh and (execution is None or not execution.dry_run):
            learn_promotion_bill(source=SOURCE_ALL, force=False)
        else:
            return template
        template = read_json(path)
    return template


def _capture_primary_source(source: str, timeout: int) -> dict[str, Any] | None:
    cdp_url = get_config().primary_chrome_cdp_url.strip()
    if not cdp_url:
        return None

    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    captured: dict[str, Any] | None = None
    keywords = tuple(str(item).lower() for item in SOURCE_CONFIGS[source]["probe_keywords"])

    def on_request(request: Any) -> None:
        nonlocal captured
        if captured is not None or request.method.upper() not in {"GET", "POST"}:
            return
        target = f"{request.url} {request.post_data or ''}".lower()
        if not any(keyword.lower() in target for keyword in keywords):
            return
        captured = {
            "scene": SOURCE_CONFIGS[source]["scene"],
            "status": "captured",
            "source": "primary_chrome",
            "url": request.url,
            "method": request.method.upper(),
            "headers": dict(request.headers),
            "post_data": request.post_data,
            "post_data_json": request.post_data_json if request.post_data else None,
            "cookies": [],
            "meta": {"captured_at": datetime.now().isoformat(timespec="seconds")},
        }

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError:
            return None
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.on("request", on_request)
        page = context.pages[0] if context.pages else context.new_page()
        deadline = time.time() + timeout
        while captured is None and time.time() < deadline:
            page.wait_for_timeout(500)
        if captured:
            try:
                captured["cookies"] = context.cookies([captured["url"]])
            except Exception:
                captured["cookies"] = context.cookies()
    return captured


def learn_promotion_bill(*, source: str = SOURCE_ALL, force: bool = False, timeout: int = 90) -> CommandResponse:
    selected = _normalize_source(source)
    learned_sources: dict[str, dict[str, Any]] = {}
    scene_paths: dict[str, str] = {}
    warnings: list[str] = []

    for item in selected:
        scene_name = SOURCE_CONFIGS[item]["scene"]
        scene_data = _capture_primary_source(item, timeout)
        if scene_data is None:
            warnings.append(f"{item}: 主浏览器未捕获到导出请求，改用 9222 SessionHub scene。")
            try:
                scene_data, _check, scene_path = ensure_scene_assets(
                    site=TMCS_SITE,
                    scene=scene_name,
                    force=force,
                    next_command="ops tmcs auth capture",
                )
                scene_paths[item] = str(scene_path)
            except RuntimeError:
                if force:
                    raise
                scene_data = load_scene_or_fail(TMCS_SITE, scene_name, next_command="ops tmcs auth capture")
                scene_paths[item] = str(scene_store_path(TMCS_SITE, scene_name))
        learned_sources[item] = _scene_to_template(scene_data)

    query_scene = None
    try:
        query_scene, _query_check, query_path = ensure_scene_assets(
            site=TMCS_SITE,
            scene=TMCS_BILL_QUERY_SCENE,
            force=False,
            next_command="ops tmcs auth capture",
        )
        scene_paths[TMCS_BILL_QUERY_SCENE] = str(query_path)
    except RuntimeError as exc:
        warnings.append(f"{TMCS_BILL_QUERY_SCENE}: {exc}")
        try:
            query_scene = load_scene_or_fail(TMCS_SITE, TMCS_BILL_QUERY_SCENE, next_command="ops tmcs auth capture")
            scene_paths[TMCS_BILL_QUERY_SCENE] = str(scene_store_path(TMCS_SITE, TMCS_BILL_QUERY_SCENE))
        except RuntimeError:
            query_scene = None

    existing_sources: dict[str, dict[str, Any]] = {}
    if _template_path().exists():
        try:
            existing_template = read_json(_template_path())
            existing_sources = {
                str(key): value
                for key, value in (existing_template.get("sources") or {}).items()
                if isinstance(value, dict)
            }
        except Exception:
            existing_sources = {}
    existing_sources.update(learned_sources)

    template_path = _write_template(sources=existing_sources, query_scene=query_scene)
    context_path = write_runtime_context(
        task_name="tmcs_promotion_bill_learn",
        status="success",
        inputs={"source": source, "selected": selected, "force": force, "timeout": timeout},
        outputs={"template_path": str(template_path), "scene_paths": scene_paths, "warnings": warnings},
        artifacts=[str(template_path), *scene_paths.values()],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="promotion-bill learn",
        data={
            "source": source,
            "sources": selected,
            "template_path": str(template_path),
            "scene_paths": scene_paths,
            "warnings": warnings,
            "context_path": str(context_path),
            "next_command": "ops --json tmcs promotion-bill download --last-month",
        },
    )


def _build_request_payload(scene: dict[str, Any], start: date, end: date) -> tuple[dict[str, Any] | None, bytes | None]:
    start_text = start.isoformat()
    end_text = end.isoformat()
    if "post_data_json" in scene:
        return _apply_date_placeholders(copy.deepcopy(scene.get("post_data_json") or {}), start_text, end_text), None
    if "post_data_form" in scene:
        form = _apply_date_placeholders(copy.deepcopy(scene.get("post_data_form") or {}), start_text, end_text)
        return None, form_encode(form)
    return None, None


def _query_download_center_url(query_scene: dict[str, Any], source: str) -> str | None:
    if not query_scene:
        return None
    json_body, data_body = _build_request_payload(query_scene, date.today(), date.today())
    _, payload, _ = tmcs_request(
        str(query_scene.get("method") or "POST"),
        str(query_scene.get("url") or ""),
        headers=_request_headers(query_scene, str(query_scene.get("url") or "")),
        json_body=json_body,
        data_body=data_body,
    )
    _raise_if_auth_payload(payload)
    found = find_success_file_url_by_keywords(payload, SOURCE_CONFIGS[source]["download_keywords"])
    if found:
        return found
    return _find_download_url_by_keywords(payload, SOURCE_CONFIGS[source]["download_keywords"])


def _find_download_url_by_keywords(value: Any, keywords: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        text_blob = json.dumps(value, ensure_ascii=False)
        if any(keyword in text_blob for keyword in keywords):
            found = find_download_url(value)
            if found:
                return found
        for nested in value.values():
            found = _find_download_url_by_keywords(nested, keywords)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_download_url_by_keywords(item, keywords)
            if found:
                return found
    return None


def _download_source(
    *,
    source: str,
    scene: dict[str, Any],
    query_scene: dict[str, Any],
    start: date,
    end: date,
    output_dir: Path,
) -> Path:
    json_body, data_body = _build_request_payload(scene, start, end)
    method = str(scene.get("method") or "POST").upper()
    url = str(scene.get("url") or "")
    headers = _request_headers(scene, url)
    _, payload, content = tmcs_request(method, url, headers=headers, json_body=json_body, data_body=data_body)
    _raise_if_auth_payload(payload)

    resolved_content: bytes | None = None
    if content and _is_probably_spreadsheet(content):
        resolved_content = content
    else:
        file_url = find_download_url(payload)
        if file_url:
            _, nested_payload, nested_content = tmcs_download(file_url, headers=None)
            _raise_if_auth_payload(nested_payload)
            resolved_content, _ = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=None)
        else:
            task_id = extract_export_task_id(payload)
            if task_id:
                gei_url = gei_task_download_url(url, task_id)
                for _ in range(12):
                    _, nested_payload, nested_content = tmcs_download(gei_url, headers=_request_headers(scene, gei_url))
                    _raise_if_auth_payload(nested_payload)
                    if nested_content and _is_probably_spreadsheet(nested_content):
                        resolved_content = nested_content
                        break
                    try:
                        resolved_content, _ = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=headers)
                        if resolved_content and _is_probably_spreadsheet(resolved_content):
                            break
                    except RuntimeError:
                        resolved_content = None
                    time.sleep(2)
            if not resolved_content:
                for _ in range(24):
                    file_url = _query_download_center_url(query_scene, source)
                    if file_url:
                        _, final_payload, final_content = tmcs_download(file_url, headers=None)
                        _raise_if_auth_payload(final_payload)
                        resolved_content, _ = resolve_download_content(content=final_content, parsed_payload=final_payload, headers=None)
                        break
                    time.sleep(5)

    if not resolved_content or not _is_probably_spreadsheet(resolved_content):
        raise RuntimeError(f"{SOURCE_CONFIGS[source]['label']}推广账单下载内容不是合法表格文件")

    output_path = _source_file_path(output_dir, source, start, _spreadsheet_suffix(resolved_content, source))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(resolved_content)
    return output_path


def run_promotion_bill_download(
    *,
    source: str = SOURCE_ALL,
    start: str | None = None,
    end: str | None = None,
    last_month: bool = False,
    dry_run: bool = False,
) -> CommandResponse:
    selected = _normalize_source(source)
    begin, finish = _normalize_dates(start=start, end=end, last_month=last_month)
    if dry_run:
        template_warning = None
        try:
            template = _load_template(allow_refresh=False)
        except RuntimeError as exc:
            template_warning = str(exc)
            template = {"defaults": {"output_dir": get_config().tmcs_bill_download_dir}, "sources": {}, "download_query": {}}
        output_dir = Path(str((template.get("defaults") or {}).get("output_dir") or get_config().tmcs_bill_download_dir)).expanduser()
        existing_files = {item: _existing_source_file(output_dir, item, begin) for item in selected}
        scene_status: list[dict[str, Any]] = []
        for item in selected:
            scene_name = SOURCE_CONFIGS[item]["scene"]
            if existing_files.get(item) is not None:
                scene_status.append({"source": item, "scene": scene_name, "status": "existing_file", "path": str(existing_files[item])})
                continue
            try:
                check = check_scene_or_fail(TMCS_SITE, scene_name, next_command="ops tmcs promotion-bill learn --source all")
                scene_status.append({"source": item, "scene": scene_name, "status": check.get("status", "valid")})
            except Exception as exc:
                if recovery_must_fail_fast():
                    raise
                scene_status.append({"source": item, "scene": scene_name, "status": "warning", "warning": str(exc)})
        if template_warning:
            scene_status.append({"source": source, "scene": "promotion_bill_template", "status": "missing", "warning": template_warning})
        sources_payload = [
            {
                "source": item,
                "label": SOURCE_CONFIGS[item]["label"],
                "scene": SOURCE_CONFIGS[item]["scene"],
                "output_file": str(_source_file_path(output_dir, item, begin)),
            }
            for item in selected
        ]
        context_path = write_runtime_context(
            task_name="tmcs_promotion_bill_download_run",
            status="success",
            inputs={"source": source, "start": begin.isoformat(), "end": finish.isoformat(), "dry_run": True},
            outputs={"sources": sources_payload, "scene_status": scene_status, "downloaded_files": [], "failed": []},
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="promotion-bill download",
            data={
                "start": begin.isoformat(),
                "end": finish.isoformat(),
                "output_dir": str(output_dir),
                "sources": sources_payload,
                "scene_status": scene_status,
                "downloaded_files": [],
                "failed": [],
                "context_path": str(context_path),
                "dry_run": True,
            },
        )

    retried_for_auth = False
    auth_refresh_applied = False
    while True:
        template = _load_template(allow_refresh=False)
        output_dir = Path(str((template.get("defaults") or {}).get("output_dir") or get_config().tmcs_bill_download_dir)).expanduser()
        existing_files = {item: _existing_source_file(output_dir, item, begin) for item in selected}
        template_sources = template.get("sources") or {}
        missing = [item for item in selected if item not in template_sources and existing_files.get(item) is None]
        if missing:
            learn_promotion_bill(source=SOURCE_ALL if len(missing) > 1 else missing[0], force=False)
            template = _load_template()
            template_sources = template.get("sources") or {}
            existing_files = {item: _existing_source_file(output_dir, item, begin) for item in selected}
            missing = [item for item in selected if item not in template_sources and existing_files.get(item) is None]
            if missing:
                raise RuntimeError(f"推广账单模板缺少来源：{','.join(missing)}。请先运行 `ops tmcs promotion-bill learn --source all`。")

        scene_status: list[dict[str, Any]] = []
        for item in selected:
            scene_name = SOURCE_CONFIGS[item]["scene"]
            if existing_files.get(item) is not None:
                scene_status.append({"source": item, "scene": scene_name, "status": "existing_file", "path": str(existing_files[item])})
                continue
            try:
                check = check_scene_or_fail(TMCS_SITE, scene_name, next_command="ops tmcs promotion-bill learn --source all")
                scene_status.append({"source": item, "scene": scene_name, "status": check.get("status", "valid")})
            except Exception as exc:
                if recovery_must_fail_fast():
                    raise
                scene_status.append({"source": item, "scene": scene_name, "status": "warning", "warning": str(exc)})
        if auth_refresh_applied:
            scene_status.append({"source": source, "scene": "promotion_bill_template", "status": "refreshed", "warning": "检测到推广账单鉴权失败，已自动强制刷新 SessionHub scenes 并重试一次。"})

        sources_payload = [
            {
                "source": item,
                "label": SOURCE_CONFIGS[item]["label"],
                "scene": SOURCE_CONFIGS[item]["scene"],
                "output_file": str(_source_file_path(output_dir, item, begin)),
            }
            for item in selected
        ]

        output_dir.mkdir(parents=True, exist_ok=True)
        query_scene = template.get("download_query") or {}
        downloaded_files: list[str] = []
        failed: list[dict[str, str]] = []
        for item in selected:
            existing_path = existing_files.get(item)
            if existing_path is not None:
                downloaded_files.append(str(existing_path))
                continue
            try:
                path = _download_source(
                    source=item,
                    scene=template_sources[item],
                    query_scene=query_scene,
                    start=begin,
                    end=finish,
                    output_dir=output_dir,
                )
                downloaded_files.append(str(path))
            except Exception as exc:
                failed.append({"source": item, "label": SOURCE_CONFIGS[item]["label"], "error": str(exc)})

        if not downloaded_files and failed and not retried_for_auth and is_probable_auth_error(failed[0]["error"]):
            require_interactive_recovery(SOURCE_CONFIGS[selected[0]]["scene"])
            learn_promotion_bill(source=source, force=True)
            mark_scene_refreshed(SOURCE_CONFIGS[selected[0]]["scene"])
            retried_for_auth = True
            auth_refresh_applied = True
            continue
        break

    context_path = write_runtime_context(
        task_name="tmcs_promotion_bill_download_run",
        status="success" if downloaded_files else "failed",
        inputs={"source": source, "start": begin.isoformat(), "end": finish.isoformat(), "dry_run": False},
        outputs={"sources": sources_payload, "scene_status": scene_status, "downloaded_files": downloaded_files, "failed": failed},
        artifacts=downloaded_files,
        errors=[item["error"] for item in failed],
    )

    if not downloaded_files:
        raise RuntimeError(f"推广账单下载全部失败：{failed[0]['label']} | {failed[0]['error']}" if failed else "推广账单下载全部失败")

    return CommandResponse(
        success=True,
        platform="tmcs",
        command="promotion-bill download",
        data={
            "start": begin.isoformat(),
            "end": finish.isoformat(),
            "output_dir": str(output_dir),
            "sources": sources_payload,
            "scene_status": scene_status,
            "downloaded_files": downloaded_files,
            "failed": failed,
            "context_path": str(context_path),
        },
    )
