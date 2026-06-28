# organize_buyer_show workflow

把 Hermes skill `organize-buyer-show`（买家秀文件整理）升级为 step 化流程。

对买家秀数据包执行两步整理：

1. **删低质**：删除图片 ≤N 张的低质量买家秀（默认 N=3，≤3 张保留无意义）。
2. **去层级**：把所有买家秀从 SKU 子目录平铺到根目录，清理空目录。

纯本地文件操作，**不涉及任何平台调用**（无猫超/聚水潭/浏览器/Cookie）。

## 目标目录结构

```
目标文件夹/                 ← --path 指向这里
  SKU文件夹（可嵌套数据包子目录）/
    买家秀1/ 图片...
    买家秀2/ 图片...
```

递归定位「含图片、无子目录」的买家秀叶子目录，支持多层嵌套。

## 入口

新 workflow 入口：

```bash
# 预览（安全，只扫描分类，绝不删除/移动）
python3 run.py workflow organize_buyer_show --path "/绝对路径/买家秀" --dry-run

# 真正执行（删除不可逆，必须显式 --execute）
python3 run.py workflow organize_buyer_show --path "/绝对路径/买家秀" --execute
```

旧中文命令（走 `tasks/organize_buyer_show.py` 薄 wrapper）：

```bash
python3 run.py 买家秀文件整理 --path "/绝对路径/买家秀" --execute
```

## 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--path` | 买家秀目标根目录（真实执行必填） | 无 |
| `--min-images` | 删除阈值：图片数 ≤ 该值的买家秀视为低质 | `3` |
| `--no-flatten` | 只删低质，不去 SKU 层级 | 关 |
| `--execute` | 真正执行删除/平铺（破坏性动作的显式开关） | 关 |
| `--dry-run` | 只预览，不做任何改动 | 关 |

## 步骤

| step | 作用 | dry-run / 无 --execute 行为 |
|------|------|------------------------------|
| `check_inputs` | 解析参数、校验路径存在 | 缺 `--path`/路径不存在时安全跳过 |
| `scan_preview` | 递归扫描买家秀叶子目录、按阈值分类 | 只读，照常执行 |
| `delete_low_quality` | `shutil.rmtree` 删除 ≤阈值 的买家秀 | **跳过**，只报 would_delete |
| `flatten_sku` | 移买家秀到根目录 + `os.rmdir` 清空目录 | **跳过**，不移动任何文件 |
| `verify_collect` | 列出最终买家秀与图片数 | 只读汇总 |

## 安全策略（遵守 CLAUDE.md §4 / §7）

1. **删除不可逆，双重保护**：破坏性动作只在 `非 --dry-run` **且** 带 `--execute` 时执行；
   两个条件缺一即只预览。
2. **dry-run 零改写**：`--dry-run` 下 `delete_low_quality` / `flatten_sku` 全部跳过，
   不删除、不移动、不清目录。
3. **重名冲突中止**：平铺前检查根目录重名，有冲突立即 `failure_result` 中止，未移动任何文件。
4. **空目录才清**：第2步用 `os.rmdir`，非空目录自动跳过，杜绝误删未识别内容。

## 边界

- 不涉及平台调用（纯本地图片目录操作）。
- 与既有 `buyer_show` workflow（自动分组/压缩/登记表回写）是**两个独立功能**，互不影响。
