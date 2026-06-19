# 一亩三分地每日签到助理

这个项目用一个专属的可见 Google Chrome profile 自动完成一亩三分地每日任务：

- 每日答题：`https://www.1point3acres.com/next/daily-question`
- 每日签到：`https://www.1point3acres.com/next/daily-checkin`

它不会保存账号密码，不读取 Chrome cookie 文件，不绕过真人验证，也不调用 LLM。遇到登录失效、验证码或人机验证时，脚本会记录错误并保留 Chrome 窗口给你手动处理；答题步骤出错时仍会继续尝试签到。

## 当前方案

脚本通过 Chrome DevTools Protocol，也就是 CDP，精确控制一个专属 Chrome profile。默认 profile 数据目录是：

```text
~/Library/Application Support/daily_checkin/chrome-profile
```

定时任务使用 macOS LaunchAgent，默认每天 `00:05` 触发。后台任务会先随机等待 `0-600` 秒，所以实际打开网页的时间通常落在 `00:05-00:15`。安装脚本会把运行副本部署到：

```text
~/Library/Application Support/daily_checkin/app
```

这样可以避开后台任务直接读取 `Documents` 目录时可能遇到的 macOS 权限问题。

## 首次使用

请先确认已安装 Google Chrome。

初始化配置：

```bash
cp .env.example .env
```

同步公开题库：

```bash
./scripts/run_daily.sh sync-bank
```

打开专属 Chrome profile：

```bash
./scripts/run_daily.sh chrome-cdp-setup
```

第一次打开后，在这个 Chrome 窗口里登录一亩三分地，并手动完成可能出现的人机验证。

登录后先 dry-run，不真实提交：

```bash
./scripts/run_daily.sh chrome-cdp-dry-run
```

确认 dry-run 正常后，真实手动运行一次：

```bash
./scripts/run_daily.sh chrome-cdp
```

## 配置 Chrome Profile

`.env.example` 里默认使用专属 Chrome 数据目录：

```env
CHROME_CONTROL_MODE=cdp
CHROME_CDP_ADDRESS=127.0.0.1
CHROME_CDP_PORT=9223
CHROME_USER_DATA_DIR="$HOME/Library/Application Support/daily_checkin/chrome-profile"
CHROME_PROFILE_DIRECTORY=Default
```

`CHROME_USER_DATA_DIR` 是整个专属 Chrome 数据目录。`CHROME_PROFILE_DIRECTORY` 是这个数据目录里的内部 profile 目录名。

Chrome UI 里显示的 profile 名称不一定等于目录名。例如 UI 里叫 `13checkin` 的 profile，磁盘目录可能是 `Profile 1`。查看映射：

```bash
jq '.profile.info_cache | to_entries[] | {dir: .key, name: .value.name}' "$HOME/Library/Application Support/daily_checkin/chrome-profile/Local State"
```

如果输出里显示：

```json
{"dir":"Profile 1","name":"13checkin"}
```

就把 `.env` 改成：

```env
CHROME_PROFILE_DIRECTORY="Profile 1"
```

修改 `.env` 后，需要重新运行 `./scripts/install_launchagent.sh`，后台定时任务才会拿到新配置。

## 随机等待

后台任务有两类随机等待，默认都可以通过 `.env` 调整：

```env
CLICK_WAIT_MIN_SECONDS=0.8
CLICK_WAIT_MAX_SECONDS=2.4
LAUNCH_RANDOM_DELAY_MIN_SECONDS=0
LAUNCH_RANDOM_DELAY_MAX_SECONDS=600
```

`CLICK_WAIT_*` 控制每次真实点击前的等待时间，包括答题选项、答题提交和签到提交。`LAUNCH_RANDOM_DELAY_*` 控制 LaunchAgent 触发后多久真正开始运行；默认配合 `00:05` 计划，让实际运行时间分布在 `00:05-00:15`。

这些等待用于给页面加载和按钮状态变化留余量，不用于绕过登录、人机验证或验证码。

## 答题来源

脚本按顺序尝试：

1. `data/local_question_bank.json`：本机遇到过的题目和答案。
2. `data/question_bank.json`：从公开来源同步的题库。

命中答案后会记录到本地题库。题库未命中时不会提交答案。

## 安装定时任务

安装默认计划，每天 `00:05` 触发，实际运行时间随机落在 `00:05-00:15`：

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

如果保留默认随机启动窗口，到点后可能还会再等最多 10 分钟才真正打开网页。成功时会看到 `runs = 1` 和 `last exit code = 0`。

如果想让测试时间一到就立刻运行，可以先把 `.env` 里的 `LAUNCH_RANDOM_DELAY_MAX_SECONDS` 临时改成 `0`，安装测试计划后再改回 `600` 并重新安装。

测试结束后恢复默认时间：

```bash
./scripts/install_launchagent.sh
```

## 日志

手动运行时，日志会直接输出到当前终端。

LaunchAgent 运行日志：

```text
~/Library/Application Support/daily_checkin/launchagent.out.log
~/Library/Application Support/daily_checkin/launchagent.err.log
```

成功日志通常类似：

```text
[2026-06-20 00:05:03 PDT] LaunchAgent starting
Waiting 214.37s before starting LaunchAgent daily run.
Question already appears complete.
Check-in already appears complete.
Done: question=already_done, checkin=already_done, submit=True
Success: All daily tasks (Question and Check-in) are completed!
```

## 常见问题

### 真人验证或验证码

脚本不会绕过真人验证。出现这类页面时会停止并写日志，需要你在专属 Chrome 窗口里手动处理。

### Chrome 没有登录

运行：

```bash
./scripts/run_daily.sh chrome-cdp-setup
```

在打开的专属 Chrome 窗口里登录一亩三分地。确认 `.env` 里的 `CHROME_PROFILE_DIRECTORY` 指向你登录过的内部 profile 目录名。

### CDP 端口被占用

默认端口是 `127.0.0.1:9223`。如果本机其他程序占用了这个端口，可以在 `.env` 中修改：

```env
CHROME_CDP_PORT=19223
```

修改后重新运行 setup，并重新安装 LaunchAgent。

### Operation not permitted

如果后台任务直接读取 `Documents` 目录里的脚本，macOS 可能因为 TCC 权限拦截。当前安装脚本会从 `~/Library/Application Support/daily_checkin` 下的运行副本启动，用来规避这个问题。

## 更多文档

- [ARCHITECTURE.md](ARCHITECTURE.md)：项目架构、组件职责、数据流、权限模型和错误处理。
