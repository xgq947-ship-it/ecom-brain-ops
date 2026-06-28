# ai_file_iterate — AI 文件迭代优化 workflow

两个 AI（Claude / Codex）**轮流打磨同一份文件**：第 1 轮 Claude 改、第 2 轮 Codex 改、交替……直到收敛或到最大轮数，产出 `<原名>.final.<ext>`。属**工具类 workflow**：不碰平台、不调 Ops-Cli。

## 收敛判定（为什么不会"2 轮就停"）

一轮判 `converged` 提前停，必须满足：**文件确实稳定下来**——本轮相似度 ≥ `convergence_ratio`（默认 0.985，即改动 <1.5%）。agent 自己在输出里打印的 `OPTIMIZE_CONVERGED` / `已收敛` 标记**只在本轮改动已很小（相似度 ≥ `marker_min_ratio`，默认 0.95）时才采信**；只要文件还在实质打磨（相似度 < 0.95），即使 agent 喊"已收敛"也不算数，继续下一轮。这避免了第二个 AI 一边大改一边自报收敛导致刚跑一个来回就停。要更激进/保守可在 `config/ai_iterate.json` 调 `convergence_ratio` / `marker_min_ratio` / `max_rounds`。

## 健壮性（失败轮不污染产物）

迭代是在同一份 `work` 上就地反复改的。为避免「某一轮把文件改坏，坏内容传给下一个 AI、甚至变成最终成品」：

- **失败/超时轮自动回滚**：一轮判 `failed`（超时被杀、限流且无改动、非零退出且无改动等）时，`work` 会被还原成**上一份成功轮产物**（`last_good`），这一轮的任何改动（含半成品/截断）一律丢弃，并在该轮记录里标 `rolled_back=true`。因此最终 `.final` **恒为某一份成功态，绝不会是半成品**。
- **震荡检测**：跑满一个完整 ping-pong 后，若当前稳定态与「上上个成功态」相似度 ≥ `convergence_ratio`，说明两个 AI 在来回拉锯、已无实质进展，提前停（`stop_reason=oscillation_or_stable`），不空跑到 `max_rounds`。
- **单 agent 全程失败告警**：只有「连续」失败到 `min_consecutive_fail_abort` 才会中止；若是「claude 一直 401、codex 一直正常」这种交替，不会触发连续中止，于是 `status.agent_stats` 汇总每个 agent 的 edited/converged/failed，全程失败的 agent 会进 `status.warnings`（并透传到 step 输出），不再被默默吞掉。
- **未知 agent 兜底**：`order` 里写了 `agents` 中不存在的名字时，该轮判失败并给出清晰 hint，不会 `TypeError` 崩掉整个迭代。

## 步骤

1. `check_inputs` — 解析 `--request/--target/--goal/--max-rounds`，定位目标文件与优化标准。
2. `iterate` — 两个 AI 轮流跑（dry-run 下**只体检不真跑**）；产出成品并记 Artifact。
3. `collect_outputs` — 汇总成品路径、轮数、停止原因。

## 中文入口（Hermes 用）

```bash
cd 运营自动化工具
python3 run.py 文件优化 --request "给我优化一下这个文件 ~/Desktop/某文案.md"
python3 run.py 文件优化 --target /path/to/file.md --goal "改成更口语化、加emoji"
python3 run.py 文件优化 --check-agents          # 体检两个 AI 能否被调用
```

也可走 workflow 入口：`python3 run.py workflow ai_file_iterate --request "..."`。

## dry-run 行为

`--dry-run` **不真实调用付费 AI、不写文件**，改为体检 agent 可用性并返回 `skipped`（CLAUDE.md 第 7 节）。

## NotchFlow 进度显示

迭代很慢（每轮上限 40 分钟），所以除了 run.py 在首尾推的 `running / success`，本 workflow 还会**逐轮**上报 NotchFlow：每轮开始推「第 X/N 轮 · agent 打磨中…」，每轮结束推「第 X/N 轮 · agent 已修改/已收敛/本轮失败」，进度条按 `轮次/上限` 推进。`iterate` step 把 `_make_progress_reporter(ctx)` 作为 `on_progress` 回调传给 `engine.run_iteration`。NotchFlow App 没开时上报自身静默 no-op；dry-run / check-agents 不进迭代，无逐轮上报。

## 产物

成品 `<原名>.final.<ext>`（原文件不动，除非配置 `in_place`）。Artifact：`type=<ext>, role=output`，metadata 含每轮 outcome / 相似度 / stop_reason。

## 运行态 / 配置 / 密钥

- 运行态（running/archive/每轮快照/日志）→ `运营自动化工具/runtime/ai_iterate/`（被 .gitignore 的 `runtime/` 覆盖，不入库）。
- 非敏感配置（可选）→ `config/ai_iterate.json`（命令、轮数、收敛阈值、模型等）。
- **密钥**（Claude 无头长期 token）→ `config/ai_iterate.secret.env`（**gitignore，绝不入库**），格式：
  ```
  CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
  ```
  生成方式：`claude setup-token`。详见仓库根 `docs/` 或本文件「鉴权」一节。

## 鉴权（Claude 无头 401 的坑）

桌面应用的订阅 token 会在磁盘上过期、独立 CLI 刷不动 → 无头 `claude -p` 报 401。
修法：`claude setup-token` 生成长期 token，写进 `config/ai_iterate.secret.env`，引擎会注入子进程。
Codex 用 `~/.codex` 文件登录，无头无此问题（需 `--sandbox workspace-write --skip-git-repo-check` 才能改文件，已默认带上）。

## 边界

- 不碰平台 / 不调 Ops-Cli / 不写 Cookie/Token 到任何提交物。
- 引擎调子 agent 前会剥离宿主 Claude 会话变量（避免“在 Claude 里套娃”导致 401）。
- 危险动作（真实调用付费 AI、写文件）在 dry-run 下全部跳过。
