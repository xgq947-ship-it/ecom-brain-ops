import json
import logging
from pathlib import Path
from typing import Any

from rich.logging import RichHandler

from ops_cli.config import get_config


LOGGER_NAME = "ops_cli"


def setup_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    config = get_config()
    log_dir = Path(config.logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    rich_handler = RichHandler(rich_tracebacks=True, show_time=False, show_path=False)
    rich_handler.setFormatter(formatter)
    rich_handler.setLevel(logging.WARNING)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    logger.addHandler(rich_handler)
    logger.addHandler(file_handler)
    return logger


def log_command(payload: dict[str, Any]) -> None:
    logger = setup_logger()
    logger.info(json.dumps(payload, ensure_ascii=False))
