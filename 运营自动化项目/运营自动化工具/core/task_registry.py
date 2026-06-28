from __future__ import annotations

from pathlib import Path

from core.business_text import TEXT_CORRECTIONS, UNKNOWN_TASK_HINT


ROOT = Path(__file__).resolve().parents[1]
_TASKS_DIR = ROOT / "tasks"


def _parse_task_yaml(path: Path) -> dict:
    """Parse a task.yaml file with support for simple lists and inline lists."""
    result: dict = {}
    current_key: str | None = None
    current_list: list | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item: "  - value"
        if stripped.startswith("- ") and current_key is not None:
            if current_list is None:
                current_list = []
            value = stripped[2:].strip().strip("\"'")
            # Support inline list syntax: [a, b]
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                items = tuple(item.strip().strip("\"'") for item in inner.split(",") if item.strip())
                current_list.append(items)
            else:
                current_list.append(value)
            continue

        # Flush previous list
        if current_key is not None and current_list is not None:
            result[current_key] = current_list
            current_key = None
            current_list = None

        # Key-value pair: "key: value"
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            if not value:
                # Start of a list block
                current_key = key
                current_list = None
            elif value.startswith("[") and value.endswith("]"):
                # Inline list on same line: key: [a, b] or key: []
                inner = value[1:-1]
                result[key] = [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
            else:
                result[key] = value

    # Flush final list
    if current_key is not None and current_list is not None:
        result[current_key] = current_list

    return result


def _discover_tasks() -> tuple[dict, dict, list]:
    """Scan tasks/ for task.yaml files, build TASKS, TASK_ALIASES, FUZZY_TASK_RULES."""
    tasks: dict = {}
    fuzzy_rules: list = []

    # Pattern 1: tasks/*.yaml (monolithic task metadata alongside .py files)
    for yaml_path in sorted(_TASKS_DIR.glob("*.yaml")):
        data = _parse_task_yaml(yaml_path)
        if not data.get("name"):
            continue
        name = data["name"]
        entrypoint = data.get("entrypoint", f"{name}.py")
        script = _TASKS_DIR / entrypoint
        stem = Path(entrypoint).stem
        # For tasks/buyer_show.py -> module tasks.buyer_show
        # For tasks/jst_order_label/main.py -> module tasks.jst_order_label.main
        module = f"tasks.{stem}" if "/" not in entrypoint else f"tasks.{entrypoint.replace('/', '.').removesuffix('.py')}"
        tasks[name] = {
            "aliases": data.get("aliases", []),
            "module": module,
            "script": script,
            "description": data.get("description", ""),
            "required_modules": data.get("required_modules", []),
        }
        keywords = data.get("fuzzy_keywords", [])
        if keywords:
            fuzzy_rules.append((name, tuple(keywords)))

    # Pattern 2: tasks/*/task.yaml (package task metadata)
    for yaml_path in sorted(_TASKS_DIR.glob("*/task.yaml")):
        data = _parse_task_yaml(yaml_path)
        if not data.get("name"):
            continue
        name = data["name"]
        package_dir = yaml_path.parent
        entrypoint = data.get("entrypoint", "main.py")
        script = package_dir / entrypoint
        module = f"tasks.{package_dir.name}.{Path(entrypoint).stem}"
        tasks[name] = {
            "aliases": data.get("aliases", []),
            "module": module,
            "script": script,
            "description": data.get("description", ""),
            "required_modules": data.get("required_modules", []),
        }
        keywords = data.get("fuzzy_keywords", [])
        if keywords:
            fuzzy_rules.append((name, tuple(keywords)))

    aliases = {alias: task_name for task_name, config in tasks.items() for alias in config["aliases"]}
    return tasks, aliases, fuzzy_rules


TASKS, TASK_ALIASES, FUZZY_TASK_RULES = _discover_tasks()


def normalize_task_text(text: str) -> str:
    for wrong, right in TEXT_CORRECTIONS.items():
        text = text.replace(wrong, right)
    return text


def task_scripts() -> dict[str, Path]:
    return {name: config["script"] for name, config in TASKS.items()}


def task_required_modules() -> dict[str, tuple[str, ...]]:
    """Return {task_name: (required_module, ...)} from task.yaml declarations."""
    return {name: tuple(config.get("required_modules", ())) for name, config in TASKS.items()}


def resolve_task(task: str) -> str:
    task = normalize_task_text(task)
    if task in TASKS:
        return task
    if task in TASK_ALIASES:
        return TASK_ALIASES[task]

    normalized = task.replace(" ", "").replace("_", "").lower()
    for task_name, patterns in FUZZY_TASK_RULES:
        for keywords in patterns:
            if all(keyword.lower() in normalized for keyword in keywords):
                return task_name

    valid_names = "、".join(sorted([*TASKS, *TASK_ALIASES]))
    raise SystemExit(f"未知任务：{task}\n可用任务：{valid_names}\n{UNKNOWN_TASK_HINT}")
