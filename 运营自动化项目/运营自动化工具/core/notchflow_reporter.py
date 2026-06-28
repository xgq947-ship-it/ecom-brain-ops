"""可选 NotchFlow workflow 状态上报（文件 inbox transport）。

设计要点（与早期 `open notchflow://` 方案的区别）：

- **App 在跑才上报**：NotchFlow App 运行期独占持有
  `~/Library/Application Support/NotchFlow/runtime.lock` 的 flock 锁。这里用非阻塞 flock
  试锁判断 App 活性——锁不上 = App 在跑 → 把事件写进 `inbox/`；锁得上或路径不存在 =
  App 没跑 → 直接静默 no-op（不创建任何目录、不堆积文件）。
- **不调用 `open`、不碰 App 进程**：纯原子文件落盘，App 侧轮询消费后删除；不激活 App、不抢焦点。
- **不是硬依赖**：所有异常一律吞掉；`dry_run` 下完全不上报（遵守 dry-run 安全规范）。
- **零环境变量**：开关就是"App 是否在运行"，不依赖也不污染 launchctl / 环境变量。
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

_BASE_DIR = Path.home() / "Library" / "Application Support" / "NotchFlow"
_MAX_INBOX_FILES = 300

# action → NotchFlow 展示状态（与 App 侧 WorkflowDisplayStatus 一致）。
_STATUS_BY_ACTION = {
    "start": "running",
    "update": "running",
    "success": "success",
    "failed": "failed",
    "waiting": "waiting",
}


class NotchFlowReporter:
    def __init__(self, base_dir: Path = _BASE_DIR) -> None:
        self._base_dir = base_dir
        self._inbox_dir = base_dir / "inbox"
        self._lock_file = base_dir / "runtime.lock"
        self._seq = 0

    def start(self, workflow_id: str, task_name: str, message: str = "开始执行", *, dry_run: bool = False) -> None:
        self._emit("start", workflow_id, task_name, message, dry_run=dry_run)

    def step(
        self,
        workflow_id: str,
        task_name: str,
        message: str,
        progress: float | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self._emit("update", workflow_id, task_name, message, progress=progress, dry_run=dry_run)

    def success(self, workflow_id: str, task_name: str, message: str = "执行完成", *, dry_run: bool = False) -> None:
        self._emit("success", workflow_id, task_name, message, progress=1, dry_run=dry_run)

    def failed(self, workflow_id: str, task_name: str, error: str = "执行失败", *, dry_run: bool = False) -> None:
        self._emit("failed", workflow_id, task_name, error, dry_run=dry_run)

    def waiting(self, workflow_id: str, task_name: str, message: str = "等待人工处理", *, dry_run: bool = False) -> None:
        self._emit("waiting", workflow_id, task_name, message, dry_run=dry_run)

    def _app_running(self) -> bool:
        """非阻塞试锁判断 App 是否运行：锁不上=在跑；锁得上=没跑；锁文件不存在=没装/没跑。"""
        try:
            fd = os.open(str(self._lock_file), os.O_RDWR)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True  # 被 App 持有 → 正在运行
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def _emit(
        self,
        action: str,
        workflow_id: str,
        task_name: str,
        message: str,
        *,
        progress: float | int | None = None,
        dry_run: bool = False,
    ) -> None:
        if dry_run:
            return
        try:
            if not self._app_running():
                return
            event = {
                "workflowId": str(workflow_id),
                "taskName": str(task_name or workflow_id),
                "message": str(message),
                "status": _STATUS_BY_ACTION.get(action, "running"),
                # UTC + Z，避免与 App 侧 ISO8601 解析的细微差异；不带小数秒。
                "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if progress is not None:
                event["progress"] = max(0.0, min(1.0, float(progress)))
            self._inbox_dir.mkdir(parents=True, exist_ok=True)
            self._write_atomic(event)
            self._prune()
        except Exception:
            # NotchFlow 不能影响 workflow，任何异常都吞掉。
            return

    def _write_atomic(self, event: dict) -> None:
        self._seq += 1
        # 文件名 = 毫秒时间戳-进程号-序号，字典序即时间序；先写 .tmp 再原子 replace。
        name = f"{int(time.time() * 1000):013d}-{os.getpid()}-{self._seq:04d}.json"
        target = self._inbox_dir / name
        fd, tmp_path = tempfile.mkstemp(dir=str(self._inbox_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(event, handle, ensure_ascii=False)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _prune(self, keep: int = _MAX_INBOX_FILES) -> None:
        files = sorted(self._inbox_dir.glob("*.json"))
        excess = len(files) - keep
        for stale in files[: max(0, excess)]:
            try:
                stale.unlink()
            except OSError:
                pass


notchflow = NotchFlowReporter()
