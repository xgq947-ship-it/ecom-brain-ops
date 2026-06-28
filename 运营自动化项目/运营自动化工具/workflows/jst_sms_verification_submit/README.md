# jst_sms_verification_submit — 聚水潭短信验证码提交（通用恢复）

把用户主动提供的 **4-6 位**短信验证码填入当前 9222 专用 Chrome 里的聚水潭短信验证码弹窗并提交。
适用于**所有 JST workflow** 触发的短信验证：弹窗过期后用 challenge 记录的**原 workflow**重新触发，
不再写死 `jst_pickup_watch`。Hermes 收到用户飞书回复的验证码后调用本 workflow 即可。

> 这是**辅助人工登录态恢复**的能力：只填写用户本人收到、主动提供的验证码。
> 不读取短信、不破解、不绕过验证码。

## 中文入口

```bash
python3 run.py 聚水潭短信验证码提交 --code 1234 --execute
```

## workflow 命令

```bash
# 只检测当前是否存在验证码弹窗（不填写、不提交）
python3 run.py workflow jst_sms_verification_submit --code 1234 --dry-run

# 推荐：用 challenge-file 提交（弹窗过期则用 challenge 里的【原 workflow】重新触发）
python3 run.py workflow jst_sms_verification_submit \
  --code 1234 --challenge-file /tmp/jst_sms_pending.json --execute

# 兼容旧机制：固定用聚水潭揽收监控触发
python3 run.py workflow jst_sms_verification_submit --code 1234 \
  --trigger-with-pickup-watch --max-trigger-attempts 3 --execute
```

## 参数

| 参数 | 说明 |
|---|---|
| `--code` | 必填，**4-6 位**数字验证码（用户主动提供） |
| `--execute` | 真实填写并提交必须显式传入；缺省（非 dry-run）报 EXECUTE_REQUIRED |
| `--dry-run` | 只检测弹窗，不填写、不提交、不触发 |
| `--challenge-file` | 推荐。读 challenge 取**原 workflow_id + args**，弹窗过期时用原 workflow 重新触发；验证通过后把 challenge 标记 verified |
| `--trigger-with-pickup-watch` | 兼容旧机制：未检测到弹窗时固定用 `jst_pickup_watch` 触发 |
| `--max-trigger-attempts` | 默认 3，硬上限 5 |
| `--trigger-cooldown-seconds` | 默认 20，每次触发之间等待 |
| `--timeout-seconds` | 默认 120 |
| `--screenshot-dir` | 可选，检测截图目录（文件名不含验证码） |

触发器解析优先级：`--challenge-file`（原 workflow） > `--trigger-with-pickup-watch`（揽收监控） > 不触发。

## 步骤

1. `check_inputs` — 校验 4-6 位 code / execute / attempts / timing，生成 masked_code。
2. `find_trigger_workflow` — 优先读 `--challenge-file` 里的原 workflow_id + args；否则回退 `--trigger-with-pickup-watch`。解析到的 workflow 不存在 → TRIGGER_WORKFLOW_NOT_FOUND。
3. `detect_sms_dialog` — 调 `ops jst auth sms detect`，记录 sms_required。
4. `trigger_with_pickup_watch_if_needed` — 未出现弹窗且有触发器时，最多 max_trigger_attempts 次（每次 cooldown）用**解析出的 workflow** 触发，出现即停；超限 TRIGGER_ATTEMPTS_EXCEEDED。
5. `submit_sms_code` — 仅 `--execute` 时调 `ops jst auth sms submit`；走 challenge-file 且验证通过则把 challenge 标记 verified。
6. `verify_session_restored` — 复检弹窗是否消失，输出 verified。
7. `collect_outputs` — 输出 submitted / verified / sms_required_before / trigger_workflow_id / trigger_attempts / challenge_id / resume_command / masked_code。

## 第一版限制（重要）

- 本 workflow 只负责**填码 + 验证**。验证通过后由 **Hermes 执行 `challenge.resume_command` 重跑原 workflow**（第一版是「重跑」，不是 step resume）。
- 原 workflow 多为只读监控时安全。**有副作用的 workflow（写单 / 导入 / 改备注）后续必须升级为 step resume / 幂等恢复**，否则重跑可能重复副作用。

## dry-run 行为

- 只调用只读检测能力，**不触发**任何 workflow、**不填写**、**不提交**。
- 浏览器未连上（9222 未启动）在 dry-run 下视为软结果，不算 workflow 失败。

## 边界

- 连 9222 / CDP / Selector / Playwright / 填码 / 提交全在 Ops-Cli（`jst auth sms detect|submit`），本层不出现平台细节。
- challenge 读写走 `clients/jst_sms_challenge.py`（唯一 schema 实现）；本层不直接发飞书。
- 验证码明文绝不进 outputs / artifact / 日志：outputs 只含 `masked_code`；落盘的 `--code` 由 runtime 统一脱敏成 `****`（`core/runtime/secrets.py`）。

## 错误码

`INVALID_CODE` · `EXECUTE_REQUIRED` · `SMS_DIALOG_NOT_FOUND` · `SMS_INPUT_NOT_FOUND` ·
`SMS_SUBMIT_BUTTON_NOT_FOUND` · `SMS_VERIFY_FAILED` · `SMS_SUBMIT_FAILED` ·
`BROWSER_NOT_RUNNING` · `TRIGGER_WORKFLOW_NOT_FOUND` · `TRIGGER_ATTEMPTS_EXCEEDED`

## 关联

- 通用恢复链路与 challenge schema：见 `clients/jst_sms_challenge.py` 与 `run.py`（`run_workflow` 自动登记 + `sms-challenge` CLI）。
- Hermes 侧：`~/.hermes/skills/ecommerce/jst-sms-verification/SKILL.md`。
