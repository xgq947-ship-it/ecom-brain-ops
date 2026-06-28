# retry_queue workflow

把「查看失败任务 / 重放重试队列」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/retry_queue.py`）：

```bash
python3 run.py 查看失败任务
python3 run.py 查看失败任务 <retry_id>
python3 run.py 查看失败任务 --all
python3 run.py 查看失败任务 <retry_id> --done
python3 run.py 查看失败任务 <retry_id> --execute
```

新 workflow 入口：

```bash
python3 run.py workflow retry_queue --dry-run            # 仅查看队列
python3 run.py workflow retry_queue <retry_id> --dry-run  # dry-run 重放单个
python3 run.py workflow retry_queue --all --dry-run       # dry-run 重放全部
python3 run.py workflow retry_queue <retry_id> --execute  # 真实重放
```

支持参数（透传给复用逻辑）：位置 `retry_id`、`--all`、`--done`、`--execute`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数、判定模式（view/replay_one/replay_all/done） | 只解析；强制 `execute=False` |
| `load_retry_items` | 列出待重试项（`list_retries`） | 只读 |
| `preview_retry` | 给出本次计划描述 | 只读 |
| `execute_retry` | 重放或标记完成 | view 跳过；replay 走 dry-run；done 跳过 |
| `collect_outputs` | 汇总结果 | 只汇总 |

## 安全策略

1. **默认只查看**：不带 `retry_id` / `--all` / `--done` 时只列出队列，不重放。
2. **真实重试必须 `--execute`**：`--dry-run` 或缺省时 `execute=False`，重放会以 `--dry-run` 跑被重放任务，不触发真实平台写入。
3. **dry-run 不改队列**：`--done` 标记在 dry-run 下跳过，不修改队列状态。
4. 重放仍由 `core/retry_queue` 经 `run.py` 子进程驱动对应任务，平台动作在各任务内部经 `clients/ops_cli_client.py` → Ops-Cli；本 workflow 不直接请求平台。

## 边界

- 复用 `core/retry_queue.py` 的 `list_retries / replay_retry / replay_all / mark_done`，不重写队列或重放逻辑。
- 不引入新的持久化（沿用 `runtime/retry/*.json`）。
