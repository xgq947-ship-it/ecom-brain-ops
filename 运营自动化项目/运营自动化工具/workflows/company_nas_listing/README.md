# company_nas_listing workflow

把「公司网盘下载产品」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/company_nas_listing.py`）：

```bash
python3 run.py "从公司网盘下载奥克斯足疗机AQA-JT-RFY06" --dry-run
python3 run.py company_nas_listing --brand 奥克斯 --category 足疗机 --models AQA-JT-RFY06 --dry-run
```

新 workflow 入口：

```bash
python3 run.py workflow company_nas_listing --dry-run
python3 run.py workflow company_nas_listing --brand 奥克斯 --category 足疗机 --models AQA-JT-RFY06 --dry-run
python3 run.py workflow company_nas_listing --text "从公司网盘下载奥克斯足疗机AQA-JT-RFY06"   # 真实下载
```

支持参数（透传给复用逻辑）：`--text`（自然语言）、`--brand`、`--category`、`--models`、`--models-file`、`--target-root`、`--jst-workbook`、`--include-buyer-show`、`--keep-mounted`、`--no-replace`、`--skip-excel`、`--dry-run`。

> 真实执行必须提供品牌 + 类目 + 型号（可经 `--text` 自然语言或显式参数）。无这些时 dry-run 安全跳过并提示。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数（含自然语言）、校验品牌/类目/型号 | 缺参数时安全跳过 |
| `parse_listing_request` | 解析型号规格（`load_models`） | 只解析 |
| `search_nas_index` | 挂载 NAS + 索引定位源目录 + 选材计数（索引加速 / 实时回退） | 只读预览 |
| `copy_product_assets` | 复制素材到目标目录（`copy_product`） | **跳过：不复制/移动任何文件** |
| `build_listing_data` | 匹配聚水潭 + 生成上架数据 Excel（「天猫搜索标题」按类目+品牌从标题库随机取） | **跳过：不生成/覆盖 Excel** |
| `collect_artifacts` | 校验产出、收尾卸载 NAS | 卸载 + 汇总 |

## dry-run 安全策略

1. **不复制/移动文件**：`copy_product_assets` 在 dry-run 跳过（且 listing `copy_product` 本身 dry-run 也为 no-op）。
2. **不删除 NAS 文件**：选材为只读遍历，目标目录在 dry-run 不被清理或写入。
3. **不覆盖已有上架资料**：`build_listing_data` 在 dry-run 跳过，不写 `上架数据.xlsx`。
4. **保留自然语言兼容**：`--text` 经 listing `parse_natural_text` 解析品牌/类目/型号。
5. 收尾卸载按既有口径；NAS 不可达 / 源目录缺失时 dry-run 安全降级。

## 标题来源（天猫搜索标题）

上架数据的「天猫搜索标题（30字限制）」不再由规则拼词生成，而是从**按摩器材爆款标题库**
（`config/paths.yaml` 的 `massage_title_library_file`，默认 `主数据/按摩器材爆款标题库.xlsx`）
按「类目 + 品牌」随机取一个真实爆款标题：

- 优先匹配**同类目同品牌**的标题；该品牌无标题时回退到**同类目任意品牌**。
- 标题库每个 sheet 对应一个类目（sheet 名即类目），列含「类目 / 品牌 / 商品标题」。
- 标题库缺失、类目不存在或无标题时，自动回退到原规则生成（`build_search_title`），不阻断流程。
- 「天猫搜索标题（15字）」仍由 30 字标题 `compact_title` 截取。
- 标题长度按库内自然分布（约 26-35 字），`validate_outputs` 不再强卡 28-30 字，仅在标题缺失或异常超长（>40 字）时报错。

## 查找/选材性能（方案 ②③）

`search_nas_index` 慢的根因是 WebDAV 列目录的网络往返。已落地两项优化：

- **③ 并发遍历**：`selected_files` 的目录列举与文件收集用线程池并发（`NAS_SCAN_WORKERS`，默认 12），选材结果与串行完全一致。
- **② 索引选材 + 回退**：`select_files_resolved` 优先从**全量文件索引**在内存中选材（零网络）；索引无该型号文件（新增产品 / 未建全量索引）则自动回退实时遍历。选材规则由 `selected_files` 单一实现，索引与实时共用同一套规则（`_LiveLister` / `_IndexLister` 只替换「列目录/列文件」原语）。

要享受 ② 的秒级体验，需先建**全量文件索引**（含文件层级、不限深度）：

```bash
python3 run.py 更新公司网盘索引 --include-files --max-depth 0
```

索引超过 `NAS_INDEX_STALE_DAYS`（默认 7 天）未更新，`search_nas_index` 输出 `warnings` 提示重建；新增/改名的产品不在索引里时按型号自动回退实时遍历，复制阶段对文件存在性仍有兜底。

> 注：WebDAV→SMB 的根因方案（①）因公司网络不可用未采用；如后续 445 放行可再评估，见 `docs/nas_listing_lookup_optimization.md`。

## 边界

- 不涉及电商平台调用；NAS 挂载/卸载来自 `workflows/company_nas_common/nas.py`，素材选取规则在同包 `listing.py`。
- 业务逻辑在同包 `listing.py`（`resolve_args / load_models / indexed_model_source / selected_files / select_files_resolved / copy_product / match_jst / save_listing / validate_outputs` 等）；`tasks/company_nas_listing.py` 仅为透传薄 wrapper。
