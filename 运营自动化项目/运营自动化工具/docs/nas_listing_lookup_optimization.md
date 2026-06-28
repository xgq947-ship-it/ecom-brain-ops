# company_nas_listing 查找路径慢 —— 优化评估

> 针对「公司网盘下载产品」流程中 `search_nas_index` 步骤（定位源目录 + 选材计数）耗时偏高的问题。
>
> **实施状态**：① WebDAV→SMB 因公司网络 445 不可用，**未采用**（保留备查）；
> ② 索引选材 + 回退、③ 并发遍历 **已落地**（见 `tasks/company_nas_listing.py` 与
> `workflows/company_nas_listing/`，测试在 `tests/test_company_nas_listing*.py`）。

## 1. 现状与根因

`search_nas_index`（[workflows/company_nas_listing/steps.py:79](../workflows/company_nas_listing/steps.py)）耗时分布：

| 子动作 | 是否网络 | 说明 |
|---|---|---|
| `mount_nas()` | 一次 | 已挂载则跳过 |
| `brand_source_dir().is_dir()` | 1 次 stat | 网络往返 |
| `indexed_model_source()` 打分 | **否** | 纯内存读本地索引 JSON，快 |
| `indexed_model_source()` 候选 `src.is_dir()` | N 次 stat | 网络往返 |
| `selected_files()` | **大量** | `os.walk` + 多次 `iterdir`，**主要耗时** |

**根因有两点，都与输入的品牌/类目/型号无关：**

1. **挂载协议是 WebDAV**：`NAS_URL = "https://suolong.synology.me:5006"`（[tasks/company_nas_listing.py:30](../tasks/company_nas_listing.py)）。
   WebDAV 列目录每次是一个 HTTP PROPFIND 往返，群晖 WebDAV 偏慢；一个产品目录下
   `主图/sku/详情切片/场景图/白底` 嵌套多层，累积几十次**串行**往返。
2. **定位后重复遍历**：`indexed_model_source` 用索引定位到目录（快），但 `selected_files`
   随即对该目录做完整 `os.walk` 按 800/790 规则选材计数——这些文件**索引扫描时已走过一遍**，
   现在又在 WebDAV 上重走。

> 结论：定位（打分）不慢，慢在「列目录 / 数文件」的网络往返。

---

## 2. 方案对比总览

| 方案 | 收益 | 改动范围 | 风险 | 外部前提 | 时效性问题 |
|---|---|---|---|---|---|
| ① SMB 挂载 | ★★★★★ | 极小 | 中 | NAS 开 SMB / 445 放行 / 钥匙串 | 无 |
| ② 索引选材 + 回退 | ★★★★☆（重复查接近瞬时） | 中 | 中 | 需定期/触发刷新索引 | **有** |
| ③ 并发遍历 | ★★★☆☆ | 中 | 低 | 无 | 无 |

可组合：① + ③ 兜底是最彻底；SMB 受限时走 ③，再视需要叠 ②。

---

## 3. 方案① —— WebDAV 换 SMB（根因修复）

**思路**：把 NAS 从 WebDAV 改为 SMB 挂载。SMB 的目录枚举/stat 比 WebDAV PROPFIND
快 5–20 倍，业务选材逻辑一行不改。

**改动点（全在 Ops-Cli / tasks 平台侧，不碰 workflow 业务层）：**
- `mount_nas()`（[tasks/company_nas_listing.py:309](../tasks/company_nas_listing.py)）：
  挂载命令从 `mount volume "https://...:5006"` 改为 `smb://suolong.synology.me/<共享名>`。
- `NAS_URL` / `NAS_MOUNT_NAME` / `active_nas_mount()` 的挂载点匹配（`/Volumes/<共享名>`）。
- `nas_product_root()` 的根路径需对应 SMB 挂载点下的实际目录结构。
- 卸载 `unmount_nas()` 逻辑基本通用，确认 `umount`/`diskutil unmount` 对 SMB 同样适用。

**前提验证（动手前必做）：**
1. NAS 控制面板 → 文件服务 → 已启用 SMB。
2. 公司网络放行 TCP 445（部分企业网封 445，这是最常见的拦路点）。
3. 钥匙串/凭据可用：`mount_smbfs //user@host/share /Volumes/xxx` 能成功。
4. SMB 下 `产品资料（运营）/1.产品资料` 路径与 WebDAV 一致或可映射。

**风险：**
- 445 被企业网封 → 方案直接不可用（先验证再投入）。
- 挂载点命名/路径与 WebDAV 不同 → 需同步改 `nas_product_root` 与 `TARGET_BRAND_DIRS` 无关（目标在本地）。
- 现有索引 JSON 里记录的是 WebDAV 路径字符串 → **换协议后索引里的绝对路径全部失效，需重建索引**。

**测试：**
- `python3 run.py 更新公司网盘索引 --dry-run`（确认 SMB 下能扫描）。
- `python3 run.py workflow company_nas_listing --brand 奥克斯 --category 足疗机 --models XXX --dry-run`（对比耗时）。
- `python3 -m pytest -q` 全绿（挂载相关测试若 mock 了 `mount_nas`，需同步更新 mock）。

---

## 4. 方案② —— 选材直接读全量索引 + 失败回退

**思路**：让索引带上文件层级，`selected_files` 优先从本地 JSON 按规则筛，命中即返回，
定位+选材全程不碰网络；未命中或索引过期再回退到现有实时 `os.walk`。

**改动点：**
- `company_nas_index` 默认带 `--include-files` 建全量索引（现默认只到型号目录层，
  见 [tasks/company_nas_index.py:48](../tasks/company_nas_index.py)）。注意全量索引 JSON 会变大，
  且建索引本身更慢（一次性成本，可后台/定时跑）。
- 新增 `selected_files_from_index(brand, category, model, include_buyer_show)`：
  对索引 `records` 里 `type==file` 且 path 落在目标产品目录下的记录，套用与
  `selected_files` **完全相同**的 800/790/白底/买家秀规则筛选（规则必须共用一份实现，
  避免两套逻辑漂移——CLAUDE.md 已注明 selected_files 是选材唯一真源）。
- `search_nas_index` 改为：先查索引选材；命中 0 或索引 `updated_at` 早于阈值 →
  回退实时 `selected_files` 并在输出里提示「索引可能过期，建议先更新索引」。

**风险（核心是时效性）：**
- **索引过期**：新加/改名/删除的图，索引未刷新就会漏选或选到已删文件。必须有：
  - 回退机制（命中失败走实时 walk）；
  - 过期提示（`updated_at` 超过 N 天告警）；
  - 复制阶段对索引给出的文件路径做存在性校验（`copy_product` 已有 `FileNotFoundError` 兜底）。
- **规则双实现漂移**：索引筛选与实时筛选必须复用同一套判定函数，否则两条路径结果不一致。

**测试：**
- 构造含 800/790/白底/买家秀的假索引 JSON，断言索引选材与实时选材结果一致。
- 索引缺该型号 → 断言回退到实时 walk。
- 索引 `updated_at` 过期 → 断言输出带过期提示。
- dry-run 不写文件、不复制（沿用现有断言）。

---

## 5. 方案③ —— 目录遍历并发化（最稳，无时效问题）

**思路**：WebDAV 慢是延迟瓶颈（串行等往返），不是带宽。把选材里的串行目录枚举
改成线程池并发，不改协议、不改选材结果、无 staleness。

**改动点（集中在 [tasks/company_nas_listing.py:590](../tasks/company_nas_listing.py) 选材链路）：**
- `safe_child_dirs` / `iter_matching_child_dirs` / `material_roots` 的多次 `iterdir`：
  对一批目录用 `ThreadPoolExecutor` 并发 `iterdir`。
- `collect_under` 的 `os.walk`：对多个 root 的 walk 并发执行（每个 root 一个任务），
  或自实现并发广度遍历替代串行 `os.walk`。
- 并发度建议 8–16，做成可配置；注意群晖 WebDAV 的最大并发连接数（过高会被限流/报错）。
- 结果需保持稳定排序（现有 `selected_files` 末尾已有去重，需保证顺序确定）。

**风险：**
- WebDAV 并发连接上限 → 并发太高反而报错/变慢，需压测取最优并发度。
- 线程安全：`ChildDirCache` 现为普通 dict，并发写需加锁或改用每任务局部缓存后合并。
- 异常聚合：单个目录失败不应中断整体（现有 `OSError` 已局部吞掉，需在并发下保留语义）。

**测试：**
- 选材结果与串行版**逐一致**（同目录跑串行/并发对比文件集合相同）。
- mock 慢 `iterdir` 验证并发确实缩短总时长。
- 并发下缓存无竞态（可用本地目录树模拟）。
- `python3 -m pytest -q` 全绿。

---

## 6. 推荐路径

1. **先验证 SMB 能否连通**（开 SMB + 445 放行 + 凭据）。能通 → 方案① 一劳永逸，
   叠方案③ 兜底，基本解决；换协议后**务必重建索引**（旧索引路径失效）。
2. **SMB 不可用**（445 被封等）→ 直接上方案③（并发），收益稳、风险低、无前提；
   若仍想要「秒级 + 离线」体验，再叠方案②（索引选材 + 回退 + 过期提示）。

> 三个方案都不改变 workflow 业务分层：协议/挂载/遍历属平台侧（tasks/Ops-Cli），
> 选材规则仍以 `selected_files` 为唯一真源，workflow 层只消费结果。
