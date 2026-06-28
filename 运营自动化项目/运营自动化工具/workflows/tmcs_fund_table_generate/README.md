# 猫超资金表生成

`tmcs_fund_table_generate` 新建猫超资金表，不读取、不修改旧资金表。

边界：

- 页面访问、9222、SessionHub、Playwright、截图全部在 `Ops-Cli`。
- workflow 只调用 `ops --json tmcs fund ...`，校验金额，合并手工输入余额，生成 Excel，嵌入截图，记录 Artifact。
- 不使用接口请求或抓包获取资金数据。

产物：

- 只生成一个 `.xlsx`，两张凭证截图直接嵌入单元格：待收货款截图 → `I3`、推广账户余额截图 → `J3`（数据正下方同列）。
- `--reserve-balance` 写入 `M2`（备用金//微信余额），`--bank-card-balance` 写入 `N2`（银行卡余额）；不传默认 `0`。
- 截图先落临时目录，嵌入后清理，**不在桌面创建截图凭证文件夹**。

步骤：

1. `check_inputs`：解析 `--month`、输出目录、备用金余额、银行卡余额；截图目录默认用临时目录。
2. `fetch_receivable_amount`：调用 `tmcs fund receivable-bill sum`。
3. `fetch_promotion_balance`：调用 `tmcs fund promotion-balance sum`。
4. `validate_amounts`：校验金额与截图文件。
5. `generate_fund_table`：新建 `.xlsx`，把两张截图嵌入 `I3`/`J3` 单元格。
6. `verify_generated_excel`：校验 Q2/S2 仍是公式。
7. `collect_outputs`：清理临时截图目录，输出产物路径与金额。

命令：

```bash
python3 run.py workflow tmcs_fund_table_generate --month 2026-05 --dry-run
python3 run.py 猫超资金表生成 --month 2026-05 --dry-run
python3 run.py 猫超资金表生成 --month 2026-05 --reserve-balance 123.45 --bank-card-balance 678.90
```
