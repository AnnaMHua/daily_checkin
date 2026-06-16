# 一亩三分地每日签到助理

这个项目用你已经登录的 Google Chrome 自动完成一亩三分地每日任务：

- 每日答题：`https://www.1point3acres.com/next/daily-question`
- 每日签到：`https://www.1point3acres.com/next/daily-checkin`

它不会保存账号密码，不读取 Chrome cookie 文件，不绕过真人验证，也不调用 LLM。遇到登录失效、验证码或人机验证时，脚本会停止并记录错误。

## 当前方案

定时任务使用 macOS LaunchAgent。

LaunchAgent 的优势：

- 电脑休眠错过执行时间后，唤醒时可以补跑。
- 运行在用户图形会话里，可以操作已经登录的 Chrome。
- 不需要强行激活 Chrome 前台窗口。

默认执行时间是每天 `00:05`。

## 首次使用

请先确认：

- 你平时使用的 Google Chrome 已登录一亩三分地。
- Chrome 已启用 `View -> Developer -> Allow JavaScript from Apple Events`。
- macOS 已允许 `bash` / `osascript` 控制 Google Chrome。
- 可选：已安装 Chrome 扩展“一亩三分地每日答题助手”。

同步公开题库：

```bash
./scripts/run_daily.sh sync-bank
```

本地 dry-run 一次，只解析题目、选择答案、检查提交按钮，不真实提交：

```bash
./scripts/run_daily.sh chrome-dry-run
```

真实手动运行一次：

```bash
./scripts/run_daily.sh
```

## 安装定时任务

安装默认计划，每天 `00:05` 运行：

```bash
./scripts/install_launchagent.sh
```

指定时间，例如每天 `20:30`：

```bash
./scripts/install_launchagent.sh 20 30
```

安装脚本会：

- 生成 `~/Library/LaunchAgents/com.annahua.daily-checkin.plist`。
- 把运行副本部署到 `~/Library/Application Support/daily_checkin/app`。
- 生成后台入口 `~/Library/Application Support/daily_checkin/run_daily_launchagent.sh`。
- 载入 LaunchAgent。

卸载：

```bash
./scripts/uninstall_launchagent.sh
```

## 两分钟后测试

如果要验证 LaunchAgent 是否会自然触发，可以先看当前时间：

```bash
date '+%H %M %Y-%m-%d %H:%M:%S %Z'
```

然后把计划改到两分钟后。例如当前是 `21:37`，则运行：

```bash
./scripts/install_launchagent.sh 21 39
```

等待到点后查看状态：

```bash
launchctl print gui/$(id -u)/com.annahua.daily-checkin
```

成功时会看到 `runs = 1` 和 `last exit code = 0`。

测试结束后恢复默认时间：

```bash
./scripts/install_launchagent.sh
```

## 配置签到文本

可选：复制 `.env.example` 为 `.env`，修改 `DAILY_CHECKIN_MESSAGES`。

```bash
cp .env.example .env
```

多个签到文本用 `|` 分隔，脚本每天随机选一句。

## 答题来源

脚本按顺序尝试：

1. Chrome 答题助手页面标记：如果扩展把选项标成正确答案。
2. `data/local_question_bank.json`：本机遇到过的题目和答案。
3. `data/question_bank.json`：从公开来源同步的题库。

命中答案后会记录到本地题库。题库未命中时不会提交答案。

## 日志

手动运行时，日志会直接输出到当前终端。

LaunchAgent 运行日志：

```text
~/Library/Application Support/daily_checkin/launchagent.out.log
~/Library/Application Support/daily_checkin/launchagent.err.log
```

成功日志通常类似：

```text
Question already appears complete.
Check-in already appears complete.
Done: question=already_done, checkin=already_done, submit=True
Success: All daily tasks (Question and Check-in) are completed!
```

## 常见问题

### 弹出 macOS 权限框

如果 macOS 提示 `bash` 或 `osascript` 想控制 Google Chrome，这是正常的 Automation 权限。允许后通常不会每天重复弹出。

### Operation not permitted

如果后台任务直接读取 `Documents` 目录里的脚本，macOS 可能因为 TCC 权限拦截。当前 LaunchAgent 安装脚本已经改为从 `~/Library/Application Support/daily_checkin` 下的运行副本启动，用来规避这个问题。

### 真人验证或验证码

脚本不会绕过真人验证。出现这类页面时会停止并写日志，需要手动处理。

### Chrome 没有登录

脚本依赖普通 Chrome 的已登录状态。请先在 Chrome 里打开一亩三分地并确认登录有效。

## 更多文档

- [ARCHITECTURE.md](ARCHITECTURE.md)：项目架构、组件职责、数据流、权限模型和错误处理。
