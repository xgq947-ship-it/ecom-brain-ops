# NotchFlow workflow 状态显示（可选）

把 workflow 的运行状态（开始 / 完成 / 失败 / 等待人工）实时显示在 macOS 刘海区，由独立的
NotchFlow App（`~/Desktop/NotchFlow`）渲染。**这是可选功能，对 workflow 没有任何硬依赖。**

## 工作机制（文件 inbox + 活性锁，不用 URL Scheme）

```
~/Library/Application Support/NotchFlow/
  runtime.lock      ← App 运行期独占持有 flock 锁
  inbox/            ← workflow 投递的事件文件（一个事件一个原子 JSON）
```

- **App 在运行才上报**：App 启动时用 flock 独占持有 `runtime.lock`。`core/notchflow_reporter.py`
  上报前用非阻塞 flock 试锁——锁不上 = App 在跑 → 把事件写进 `inbox/`；锁得上或锁文件不存在 =
  App 没跑 → **直接静默 no-op**（不创建目录、不堆积文件）。
- **App 监听消费**：App 运行期轮询 `inbox/`，读到事件文件就更新刘海显示，然后删除该文件。
- **App 一关，监听和上报同时停**：监听器随 App 进程消失；锁释放后 workflow 试锁即知"没在跑"，不再写。
- **不调用 `open`、不碰 App 进程、不抢焦点**：纯原子文件写入，因此不会像 URL Scheme 那样把窗口顶到前台。
- **零环境变量**：开关就是"App 是否在运行"，不依赖也不污染 launchctl / 任何环境变量。

## 边界

- `dry_run` 下完全不上报（遵守 dry-run 安全规范）。
- reporter 的任何异常都被吞掉，绝不影响 workflow 主流程。
- App 关闭期间跑的 workflow 状态会被丢弃，不做事后补显示（刻意取舍：没开就不报、不堆积）。

事件 JSON 契约（与 App 侧 `WorkflowEvent` 一致）：
`workflowId / taskName / message / status(running|success|failed|waiting) / updatedAt(ISO8601 UTC Z) / progress(可选 0–1)`。
