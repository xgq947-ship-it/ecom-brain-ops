# jst_order_exchange_resend

聚水潭订单换货 / 补发 workflow。封装 `ops jst order exchange-resend`（learn / preview / submit），
提供参数校验、状态追踪、dry-run 保护与产物记录。平台交互完全由 Ops-Cli 持有，本 workflow
不直接请求平台。

## 步骤

| Step | 说明 | dry-run 行为 |
|------|------|------------|
| check_inputs | 校验 order_no、mode、execute、confirm_order_no、qty | 正常运行（纯校验） |
| inspect_existing_capabilities | 复用订单查询能力（只读 preview）解析订单与资格 | 平台不可达时 skip |
| learn_or_preview_flow | --learn-only 时调用 Ops-Cli 学习换货/补发入口；否则沿用 preview | 探索失败时 skip |
| validate_eligibility | 订单存在、状态允许、商品匹配，否则停止 | 无 preview 数据时 skip |
| submit_if_execute | 仅 --execute 且确认订单号一致时提交 | 直接跳过，绝不调用 --execute |
| collect_outputs | 汇总 submitted / final_payload / 截图 / 探索步骤 | 正常运行 |

## 参数

```
--order-no TEXT          聚水潭订单号 / 线上订单号（必填）
--mode TEXT              resend=补发 / exchange=换货（必填）
--reason TEXT            原因（可选）
--remark TEXT            备注（可选）
--sku-code TEXT          商品编码。exchange 模式表示换入目标商品；resend 模式当前为整单补发，
                         不支持按 SKU 部分补发，显式传 --sku-code 会被拒绝
--qty INT                数量，默认 1。同上，resend 整单补发不支持 --qty != 1
--dry-run                只预览流程，不提交
--execute                真实执行必须显式传入
--confirm-order-no TEXT  真实执行二次确认，必须等于 --order-no
--learn-only             只探索页面流程并截图，不提交
--screenshot-dir TEXT    保存页面探索截图目录
```

## 运行示例

```bash
# 预览补发（解析订单 + 资格判断，不提交）
python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode resend --dry-run

# 预览换货
python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode exchange --dry-run

# 学习页面入口（只记录步骤，绝不提交）
python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode resend --learn-only --dry-run
python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode exchange --learn-only --dry-run

# 真实执行（二次确认）
python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode resend --execute --confirm-order-no 订单号
python3 run.py workflow jst_order_exchange_resend --order-no 订单号 --mode exchange --sku-code 换入商品编码 --execute --confirm-order-no 订单号
```

## 安全边界

1. 默认 dry-run，不真实提交。
2. 没有 `--execute` 绝不点击最终确认 / 提交。
3. `--execute` 时必须提供 `--confirm-order-no` 且等于 `--order-no`。
4. `--learn-only` 只探索页面与截图，不能提交（与 `--execute` 互斥）。
5. 真实提交前必须输出 `final_payload`。
6. 找不到订单、状态不允许、商品不匹配时停止。
7. 不修改订单金额 / 收货地址 / 账号密码，不绕过验证码，不无限重试。

## 真实提交模板

聚水潭换货 / 补发真实提交由 Ops-Cli 平台层独占。workflow 只调用
`ops --json jst order exchange-resend submit`，不会保存或感知平台请求细节。

当前已确认 `resend` 路径是**整单补发**（按原订单全部明细原样补发，聚水潭 `CreateReissueOrderAllItem`
只用内部订单 ID）。`final_payload.sku_code` 仅作信息展示（默认取原订单首个商品编码），
**不参与提交渲染**。因此 resend 模式下显式传 `--sku-code` 或 `--qty != 1` 会被 `submit` 直接拒绝
（不打平台），避免「以为按 SKU 部分补发、实际整单补发」的误操作；preview 会给出同样的 `warnings` 提示。
模板存在且订单状态命中 `eligible_status` 时，`submit` 经平台提交并返回 `submitted=true` 与接口 `result`。
若聚水潭返回「已补发过，是否继续」这类二次确认提示，Ops-Cli 不自动确认，按失败返回。

`exchange` 换货已支持纯接口真实执行：`--sku-code` 表示换入目标商品，退回商品默认取原订单商品；
未传 `--sku-code` 时默认以原订单商品作为换货商品。真实提交不再点击页面「确定」：
Ops-Cli 会通过 `ReloadOrdersV2` 读取最新订单明细，通过商品选择器接口按商品编码查换入目标，
再按页面 JS 的 `CreateData` 结构组装 `items`，调用订单列表 `JTable1 / ChangeBatchItem` 提交并复查订单行商品编码。
learn-only 仍只会打开商品选择器、搜索并选中目标商品，不点击「确定」。
换货默认允许订单状态：`已发货`、`未发货`、`已付款待审核`、`异常`；旧 confirmed template
即使只写了 `已发货`，Ops-Cli 也会合并这组默认换货状态后再判断资格。

如果 confirmed template 不存在，`resend` 仍会在输出 `final_payload` 后停在待确认；
`exchange` 不依赖补发模板，直接使用已固化的 `ChangeBatchItem` 接口流。
