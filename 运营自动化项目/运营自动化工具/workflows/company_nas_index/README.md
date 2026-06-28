# company_nas_index workflow

把「更新公司网盘索引」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/company_nas_index.py`）：

```bash
python3 run.py 更新公司网盘索引 --dry-run
python3 run.py 更新公司网盘索引                # 真实写索引
python3 run.py 更新公司网盘索引 奥克斯          # 搜索关键词
```

新 workflow 入口：

```bash
python3 run.py workflow company_nas_index --dry-run
python3 run.py workflow company_nas_index                  # 真实写索引
python3 run.py workflow company_nas_index 奥克斯 --dry-run  # 搜索（只读现有索引）
```

支持参数（透传给复用逻辑）：位置 `query`（搜索关键词）、`--root`、`--max-depth`、`--include-files`、`--keep-mounted`、`--limit`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数、判定 build/search 模式 | 只解析 |
| `scan_nas` | 挂载 NAS + 只读扫描目录（或 search 模式查现有索引） | 只读扫描；失败安全降级 |
| `build_index` | 汇总品牌/类目/文件统计 | 纯计算 |
| `save_index` | 写 JSON/MD/CSV 索引 | **跳过：不写正式索引文件** |
| `collect_artifacts` | 汇总产物、收尾卸载 NAS | 汇总 + 卸载 |

## dry-run 安全策略

1. **不覆盖正式索引文件**：`save_index` 在 dry-run 跳过，不写 `JSON/MD/CSV`。
2. **不移动 NAS 文件**：扫描为只读目录遍历（`os.walk`），不复制/移动/删除任何文件。
3. **扫描路径用配置**：默认 `nas_product_root()`，可用 `--root` 覆盖。
4. 收尾卸载与 legacy 一致：仅当本次由 workflow 挂载、且非 `--keep-mounted`、且执行前未挂载时才卸载。
5. NAS 不可达 / 扫描失败时 dry-run 安全降级（跳过后续，不报错）。

> 注：扫描过程会写一个临时 checkpoint（`company_nas_scan_checkpoint.jsonl`，扫描断点，非正式索引），此为 legacy 既有行为，dry-run 不写正式 JSON/MD/CSV 索引。

## 边界

- 不涉及电商平台调用；NAS 挂载/卸载逻辑复用 legacy（`tasks/company_nas_listing`）。
- 复用 `tasks/company_nas_index.py` 的 `scan_index / summarize / write_json / write_csv / write_md / search_index`，不重写算法。
