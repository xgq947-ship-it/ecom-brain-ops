# jst_order_logistics — 聚水潭订单物流查询

把"查询物流轨迹"这一平台读取动作 step 化。业务层不直连聚水潭，所有平台读取
都委托给 Ops-Cli 的 `ops jst order logistics`（封装好的接口），workflow 只负责
参数解析、结果透出与产物记录。

## 步骤

| step | 说明 |
|---|---|
| `check_inputs` | 解析 `--order-id / --outer-order-id / --input / --limit / --output`，参数缺失直接失败 |
| `fetch_logistics` | 经 `clients/ops_cli_client.run_ops_json` 调用 `ops jst order logistics`；dry-run 跳过 |
| `write_output` | 指定 `--output` 时把结果写成 JSON 文件并记 Artifact |
| `collect_outputs` | 透出 ops-cli 同构结果 `{success, platform, command, data}` |

## 参数

- `--order-id <聚水潭订单号>`：可重复
- `--outer-order-id <外部平台订单号>`：可重复
- `--input <文件>`：订单号输入文件，支持 JSON/TXT/CSV
- `--limit <N>`：只查询前 N 个订单
- `--output <path.json>`：把结果写到 JSON 文件；相对路径落在 `runtime/logistics/` 下；省略则只在运行结果里返回
- `--dry-run`：不发起真实查询

`--order-id / --outer-order-id / --input` 至少传一个。

## 输出对齐 ops-cli

`collect_outputs` 透出与 `ops --json jst order logistics` 完全一致的
`success / platform / command / data` 结构：

- 单订单查询：`data` 为单条 item（`logistics_no / logistics_company / logistics_status /
  signed / trace_events` 等）
- 批量查询：`data` 含 `summary` 与 `items[]`

## dry-run 行为

物流查询是平台**读取**动作，但需要有效聚水潭登录态，且可能触发短信验证。
因此 dry-run 一律**不发起真实查询**：`fetch_logistics` / `write_output` /
`collect_outputs` 都返回 `skipped`，不消耗 session、不触发验证、不写文件。

## 边界

- 不出现聚水潭 URL / Cookie / Token / Selector / Playwright / CDP
- 不直连平台，不管理 session，登录态恢复由 Ops-Cli 负责
- 短信验证等鉴权问题由 Ops-Cli 返回 `error_code`，业务层只读取契约字段

## 验证

```bash
python3 run.py workflow jst_order_logistics --dry-run --order-id TEST001
python3 run.py 物流查询 --dry-run --order-id TEST001
python3 -m pytest -q tests/test_jst_order_logistics_workflow.py
```
