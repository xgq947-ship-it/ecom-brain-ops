# jst_order_invoice_workorder

聚水潭发票工单 workflow。封装 `ops jst order invoice` CLI，提供状态追踪、dry-run 保护和产物记录。

## 步骤

| Step | 说明 | dry-run 行为 |
|------|------|------------|
| check_inputs | 解析并校验所有参数 | 正常运行（纯校验，无副作用） |
| resolve_order | 调用 ops（不含 --execute）解析订单、构建工单预览 | 尝试只读查询；平台不可达时 skip |
| submit_workorder | 真实提交发票工单（需加 --execute） | 直接跳过，绝不调用 --execute |
| collect_outputs | 汇总所有结果 | 正常运行 |

## 参数

```
--order-id TEXT          JST 内部或平台订单号（与 --outer-order-id 二选一）
--outer-order-id TEXT    外部平台订单号（淘宝等）
--shop-name TEXT         店铺名称（必填）
--invoice-entity TEXT    开票主体公司（必填）
--title TEXT             发票抬头（必填）
--tax-no TEXT            税号（必填）
--address TEXT           公司地址（必填）
--phone TEXT             公司电话（必填）
--bank TEXT              开户行（必填）
--bank-account TEXT      银行账号（必填）
--amount TEXT            发票金额，如 128.50（必填）
--quantity INT           商品数量，默认 1
--invoice-type TEXT      发票类型，默认 专用发票
--execute                真实提交工单（不加则只预览）
--dry-run                干跑，跳过所有写操作
```

## 运行示例

```bash
# 预览（解析订单，不提交）
python3 run.py workflow jst_order_invoice_workorder \
  --order-id 5111330689403040244 \
  --shop-name "奥克斯索隆专卖店" \
  --invoice-entity "福安市索隆电子有限公司" \
  --title "XX有限公司" --tax-no "91330000XXXXXXXX" \
  --address "浙江省杭州市XX区" --phone "0571-12345678" \
  --bank "中国银行" --bank-account "12345678901" \
  --amount "128.50"

# 真实提交
python3 run.py workflow jst_order_invoice_workorder \
  --order-id 5111330689403040244 \
  --shop-name "奥克斯索隆专卖店" \
  --invoice-entity "福安市索隆电子有限公司" \
  --title "XX有限公司" --tax-no "91330000XXXXXXXX" \
  --address "浙江省杭州市XX区" --phone "0571-12345678" \
  --bank "中国银行" --bank-account "12345678901" \
  --amount "128.50" --execute

# dry-run
python3 run.py workflow jst_order_invoice_workorder --dry-run \
  --order-id 5111330689403040244 \
  --shop-name "奥克斯索隆专卖店" \
  --invoice-entity "福安市索隆电子有限公司" \
  --title "XX有限公司" --tax-no "91330000XXXXXXXX" \
  --address "浙江省杭州市XX区" --phone "0571-12345678" \
  --bank "中国银行" --bank-account "12345678901" \
  --amount "128.50"
```

## 边界说明

- 平台逻辑（订单查询、工单 API）完全由 Ops-Cli 持有，本 workflow 不直接请求平台
- 不含 --execute 时只查询订单，不产生任何写操作
- dry-run 下 submit_workorder 步骤强制跳过，无论是否传 --execute
