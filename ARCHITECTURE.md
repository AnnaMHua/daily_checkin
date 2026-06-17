# 项目架构

这个项目是一个本机自动化工具，用 macOS LaunchAgent 定时驱动已经登录的一亩三分地 Chrome 会话，完成每日答题和每日签到。它不保存账号密码，不绕过真人验证，也不调用 LLM；遇到登录失效、验证码或人机验证时会停止并记录错误。

## 总体流程

```text
macOS LaunchAgent
  -> ~/Library/Application Support/daily_checkin/run_daily_launchagent.sh
    -> ~/Library/Application Support/daily_checkin/app/scripts/chrome_daily.py
      -> Chrome executable with --profile-directory
      -> AppleScript / osascript
        -> matching Google Chrome tab
          -> daily-question / daily-checkin 页面
```

定时任务由 LaunchAgent 负责触发。安装脚本会把运行所需的 Python 脚本、公开题库和可选配置复制到 `~/Library/Application Support/daily_checkin/app`，然后让 LaunchAgent 从这个英文路径下启动 wrapper。这样做是为了避开 macOS 后台任务读取 `Documents` 目录时可能遇到的 TCC 权限限制。

每次运行 `scripts/install_launchagent.sh` 都会重新部署运行副本，所以修改 `scripts/chrome_daily.py`、`data/question_bank.json` 或 `.env` 后，需要重新安装一次 LaunchAgent 才会影响后台定时任务。

## 主要组件

### `scripts/install_launchagent.sh`

安装或更新 LaunchAgent。

- 默认每天 `00:05` 运行。
- 支持传入小时和分钟，例如 `./scripts/install_launchagent.sh 20 30`。
- 生成 `~/Library/LaunchAgents/com.annahua.daily-checkin.plist`。
- 生成 `~/Library/Application Support/daily_checkin/run_daily_launchagent.sh`。
- 部署运行副本到 `~/Library/Application Support/daily_checkin/app`。
- 载入 LaunchAgent，让 macOS 按 `StartCalendarInterval` 触发。
- 安装时会卸载并删除 rename 前的旧 plist `com.annahua.1point3acres.checkin.plist`，避免新旧任务重复运行。

### `scripts/uninstall_launchagent.sh`

卸载 LaunchAgent。

- unload 并删除 plist。
- 删除 LaunchAgent wrapper。
- 不删除历史日志和运行副本，方便排查问题。

### `scripts/run_daily.sh`

本地手动运行入口，主要用于开发和调试。

- 无参数：真实提交答题和签到。
- `chrome`：真实提交，等价于无参数。
- `chrome-dry-run`：只解析题目、选择答案、检查提交按钮，不真实提交。
- `sync-bank`：同步公开题库。

LaunchAgent 当前不直接调用这个脚本，而是调用部署到 `Application Support` 的 `chrome_daily.py` 副本。这是为了降低后台权限问题。

### `scripts/chrome_daily.py`

核心自动化逻辑。

- 通过 `--profile-directory` 打开指定 Chrome profile，再用 AppleScript 控制匹配的一亩三分地标签页。
- 打开每日答题页并解析题目和选项。
- 优先等待 Chrome 答题助手扩展给出的页面标记。
- 再查本地题库和公开题库。
- 命中答案后点击选项；真实运行时等待提交按钮可用并点击提交。
- 打开签到页，填写随机签到文本并点击提交。
- 分别捕获答题和签到错误，避免答题失败时直接跳过签到。
- 检测“今日已答题”和“今日已签到”，避免重复提交。

### `scripts/sync_question_bank.py`

公开题库同步脚本。

- 从 GitHub 题库和 cnblogs 题库抓取内容。
- 解析后合并去重。
- 写入 `data/question_bank.json`。

## 数据文件

### `data/question_bank.json`

公开题库，来自同步脚本。这个文件可以提交到版本库。

### `data/local_question_bank.json`

本机题库，用来记录本机遇到过的题目、选项、答案来源和最后状态。这个文件包含个人运行痕迹，已在 `.gitignore` 中忽略。

### `.env`

可选配置文件，已在 `.gitignore` 中忽略。当前支持：

- `CHROME_PROFILE_DIRECTORY`：要使用的 Chrome profile 目录名，例如 `Default`。
- `CHROME_EXECUTABLE`：可选，Chrome 可执行文件路径。
- `DAILY_CHECKIN_MESSAGES`：多个签到文本用 `|` 分隔。

## 日志

手动运行时日志直接输出到当前终端。

LaunchAgent 运行时日志写到：

```text
~/Library/Application Support/daily_checkin/launchagent.out.log
~/Library/Application Support/daily_checkin/launchagent.err.log
```

`launchagent.out.log` 记录正常运行结果，例如已答题、已签到、成功完成。`launchagent.err.log` 记录 shell 或系统层错误。

如果 `launchagent.err.log` 里残留历史错误，需要结合时间戳判断。当前成功链路主要看 `launchctl print` 的 `last exit code = 0` 和 `launchagent.out.log` 中最近一次运行记录。

## 权限模型

项目依赖用户已经登录的普通 Google Chrome，不读取浏览器 cookie 文件，也不保存账号密码。

需要的本机权限：

- Chrome 启用 `Allow JavaScript from Apple Events`。
- macOS 允许 `bash` / `osascript` 控制 Google Chrome。
- `.env` 中的 `CHROME_PROFILE_DIRECTORY` 指向已经登录一亩三分地的 Chrome profile。

这个授权通常只需要确认一次。之后 LaunchAgent 到点运行时不应该反复弹窗。

## 错误处理

| 情况 | 行为 |
| --- | --- |
| 未登录 / 真人验证 / 验证码 | 停止当前任务并记录错误 |
| 今日已答题 | 记录 `already_done`，不重复提交 |
| 今日已签到 | 记录 `already_done`，不重复提交 |
| 题库未命中 | 记录未答题，停止答题提交 |
| 答题失败 | 记录失败，继续尝试签到 |
| 签到失败 | 记录失败，不影响答题结果 |
| Chrome 或 AppleScript 异常 | 记录错误，进程以失败状态退出 |

只有答题和签到都处于 `success` 或 `already_done` 时，日志才会写入总成功信息。

## 为什么使用 LaunchAgent

LaunchAgent 适合 macOS 桌面自动化：

- 电脑休眠错过时间后，唤醒时可以补跑。
- 运行在用户图形会话中，可以操作用户的 Chrome。
- 不需要前台激活 Chrome，避免打断当前操作。

脚本会先通过 Chrome 可执行文件和 `--profile-directory` 打开目标页面，再用 AppleScript 查找匹配的一亩三分地标签页执行 JavaScript，避免误操作最前面的非登录 profile 窗口。

## 已验证链路

已经验证过的端到端链路：

1. 安装 LaunchAgent 到两分钟后的测试时间。
2. 等待 macOS 自然触发，不手动 kickstart。
3. LaunchAgent 启动 wrapper。
4. wrapper 启动部署副本中的 `chrome_daily.py`。
5. 脚本操作 Chrome 打开答题页和签到页。
6. 页面识别今天已经完成。
7. 进程退出码为 `0`，日志写入总成功信息。

测试时的成功日志形态：

```text
Question already appears complete.
Check-in already appears complete.
Done: question=already_done, checkin=already_done, submit=True
Success: All daily tasks (Question and Check-in) are completed!
```

## 常用操作

安装默认定时任务：

```bash
./scripts/install_launchagent.sh
```

临时改到指定时间测试：

```bash
./scripts/install_launchagent.sh 21 39
```

查看 LaunchAgent 状态：

```bash
launchctl print gui/$(id -u)/com.annahua.daily-checkin
```

查看运行日志：

```bash
tail -n 80 "$HOME/Library/Application Support/daily_checkin/launchagent.out.log"
tail -n 80 "$HOME/Library/Application Support/daily_checkin/launchagent.err.log"
```

卸载：

```bash
./scripts/uninstall_launchagent.sh
```
