# 项目架构

这个项目是一个本机自动化工具，用 macOS LaunchAgent 定时驱动专属 Google Chrome profile，完成一亩三分地每日答题和每日签到。它不保存账号密码，不读取 Chrome cookie 文件，不绕过真人验证，也不调用 LLM；遇到明确的登录失效、验证码或人机验证时会记录错误并保留 Chrome 窗口给用户手动处理。

## 总体流程

```text
macOS LaunchAgent
  -> ~/Library/Application Support/daily_checkin/run_daily_launchagent.sh
    -> ~/Library/Application Support/daily_checkin/app/scripts/chrome_daily.py
      -> optional random launch delay
      -> Google Chrome with dedicated --user-data-dir
      -> CDP endpoint at 127.0.0.1:<CHROME_CDP_PORT>
        -> daily-question / daily-checkin page target
          -> DOM parsing, answer selection, check-in form fill
```

定时任务由 LaunchAgent 负责触发。安装脚本会把运行所需的 Python 脚本、公开题库和 `.env` 配置复制到 `~/Library/Application Support/daily_checkin/app`，然后让 LaunchAgent 从这个英文路径下启动 wrapper。这样做是为了避开 macOS 后台任务读取 `Documents` 目录时可能遇到的 TCC 权限限制。

每次运行 `scripts/install_launchagent.sh` 都会重新部署运行副本，所以修改 `scripts/chrome_daily.py`、`data/question_bank.json` 或 `.env` 后，需要重新安装一次 LaunchAgent 才会影响后台定时任务。

## Chrome Profile 模型

项目默认使用专属 Chrome 数据目录：

```text
~/Library/Application Support/daily_checkin/chrome-profile
```

这个目录下可以有一个或多个 Chrome profile。脚本通过 `.env` 中的 `CHROME_PROFILE_DIRECTORY` 选择内部 profile 目录名，例如 `Default` 或 `Profile 1`。

Chrome UI 显示名和内部目录名不是一回事。例如 UI 里显示 `13checkin`，对应的内部目录可能是 `Profile 1`。映射关系来自专属数据目录里的 `Local State`：

```bash
jq '.profile.info_cache | to_entries[] | {dir: .key, name: .value.name}' "$HOME/Library/Application Support/daily_checkin/chrome-profile/Local State"
```

## 主要组件

### `scripts/install_launchagent.sh`

安装或更新 LaunchAgent。

- 默认每天 `00:05` 触发；实际任务开始前由 runner 随机等待，默认窗口是 `00:05-00:15`。
- 支持传入小时和分钟，例如 `./scripts/install_launchagent.sh 20 30`。
- 生成 `~/Library/LaunchAgents/com.annahua.daily-checkin.plist`。
- 生成 `~/Library/Application Support/daily_checkin/run_daily_launchagent.sh`。
- 部署运行副本到 `~/Library/Application Support/daily_checkin/app`。
- 复制 `.env` 到运行副本。
- 载入 LaunchAgent，让 macOS 按 `StartCalendarInterval` 触发。
- 安装时会卸载并删除 rename 前的旧 plist `com.annahua.1point3acres.checkin.plist`，避免新旧任务重复运行。

### `scripts/uninstall_launchagent.sh`

卸载 LaunchAgent。

- unload 并删除 plist。
- 删除 LaunchAgent wrapper。
- 不删除历史日志和运行副本，方便排查问题。

### `scripts/run_daily.sh`

本地手动运行入口，主要用于初始化、开发和调试。

- `chrome-cdp-setup`：打开专属 Chrome profile，供用户登录和处理验证。
- `chrome-cdp-dry-run`：解析题目、选择答案、检查提交按钮，不真实提交。
- `chrome-cdp`：真实提交答题和签到。
- `sync-bank`：同步公开题库。

LaunchAgent 当前不直接调用这个脚本，而是调用部署到 `Application Support` 的 `chrome_daily.py` 副本。这是为了降低后台权限问题。

### `scripts/chrome_daily.py`

核心自动化逻辑。

- 启动或连接专属 Chrome 数据目录。
- 通过 CDP 打开并附着到目标页面。
- 打开每日答题页并解析题目和选项。
- 查询本地题库和公开题库。
- 命中答案后点击选项；真实运行时等待提交按钮可用并点击提交。
- 打开签到页，填写随机签到文本并点击提交。
- LaunchAgent 模式下，开始运行前随机等待；每次真实点击前也随机等待。
- 分别捕获答题和签到错误，避免答题失败时直接跳过签到。
- 检测“今日已答题”和“今日已签到”，避免重复提交。
- 检测登录页、密码框、登录表单和明确验证码文案，并在强人工处理信号出现时停止自动操作。
- 将单独残留的 captcha/challenge 相关元素视为弱信号，避免成功提交后的页面残留误阻止浏览器清理。

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

### `.env.example`

可提交的配置模板。默认使用 CDP 专属 Chrome profile。

### `.env`

本机配置文件，已在 `.gitignore` 中忽略。当前支持：

- `CHROME_CONTROL_MODE`：默认 `cdp`。
- `CHROME_CDP_ADDRESS`：默认 `127.0.0.1`。
- `CHROME_CDP_PORT`：默认 `9223`。
- `CHROME_USER_DATA_DIR`：专属 Chrome 数据目录。
- `CHROME_PROFILE_DIRECTORY`：专属数据目录内的内部 profile 目录名。
- `CHROME_EXECUTABLE`：可选，Chrome 可执行文件路径。
- `CLICK_WAIT_MIN_SECONDS` / `CLICK_WAIT_MAX_SECONDS`：真实点击前的随机等待范围，默认 `0.8-2.4` 秒。
- `LAUNCH_RANDOM_DELAY_MIN_SECONDS` / `LAUNCH_RANDOM_DELAY_MAX_SECONDS`：LaunchAgent 触发后的随机启动等待范围，默认 `0-600` 秒。
- `DAILY_CHECKIN_MESSAGES`：多个签到文本用 `|` 分隔。

## 随机等待模型

LaunchAgent 的 plist 只负责固定日程触发，默认是 `00:05`。真正的随机窗口在 `chrome_daily.py` 内实现：wrapper 使用 `--random-start-delay` 启动 runner，runner 在打开 Chrome 前根据 `LAUNCH_RANDOM_DELAY_MIN_SECONDS` 和 `LAUNCH_RANDOM_DELAY_MAX_SECONDS` 随机 sleep。默认 `0-600` 秒意味着实际开始时间落在 `00:05-00:15`。

点击前等待同样在 Python runner 内实现。答题选项点击、答题提交点击、签到提交点击前都会根据 `CLICK_WAIT_MIN_SECONDS` 和 `CLICK_WAIT_MAX_SECONDS` 等待一段随机时间。这些等待只用于降低页面未稳定导致的误判，不改变登录或验证处理策略。

## CDP 控制模型

CDP 端口只绑定本机地址，默认是：

```text
127.0.0.1:9223
```

脚本启动 Chrome 时使用：

```text
--user-data-dir=<CHROME_USER_DATA_DIR>
--profile-directory=<CHROME_PROFILE_DIRECTORY>
--remote-debugging-address=127.0.0.1
--remote-debugging-port=<CHROME_CDP_PORT>
```

然后通过 CDP 获取页面 target，并在目标页面内执行 JavaScript 读取 DOM、点击选项和填写签到文本。

## 权限模型

项目依赖用户已经在专属 Chrome profile 中登录一亩三分地。它不读取浏览器 cookie 文件，也不保存账号密码。

需要的本机条件：

- 已安装 Google Chrome。
- `.env` 指向正确的专属 Chrome 数据目录和内部 profile 目录名。
- 首次运行 `chrome-cdp-setup` 后，用户已经在打开的窗口里登录并处理必要验证。

CDP 端口绑定 `127.0.0.1`，不对局域网或公网开放。

## 错误处理

| 情况 | 行为 |
| --- | --- |
| 未登录 / 真人验证 / 验证码 | 强证据出现时记录当前步骤需要人工处理，退出码为 `2` |
| 今日已答题 | 记录 `already_done`，不重复提交 |
| 今日已签到 | 记录 `already_done`，不重复提交 |
| 提交后无明确失败或强人工处理信号 | 记录 `submitted`，允许后续清理本次运行打开的 Chrome |
| 题库未命中 | 记录未答题，停止答题提交 |
| 答题失败或答题需要人工处理 | 记录状态，继续尝试签到 |
| 签到失败 | 记录失败，不影响答题结果 |
| Chrome 或 CDP 异常 | 记录错误，进程以失败状态退出 |

只有答题和签到都处于 `success` 或 `already_done` 时，日志才会写入总成功信息。
如果提交模式下答题和签到都处于 `success`、`already_done` 或 `submitted`，脚本会清理本次运行打开的 Chrome：如果这次运行新启动了 Chrome，就关闭这次启动的 Chrome 进程；如果只是连到已有浏览器，则只关闭本次打开的标签页。

## 为什么使用 LaunchAgent

LaunchAgent 适合 macOS 桌面自动化：

- 电脑休眠错过时间后，唤醒时可以补跑。
- 运行在用户图形会话中，可以操作可见 Chrome。
- 不需要前台激活 Chrome，避免打断当前操作。

## 已验证链路

已经验证过的链路：

1. 创建专属 Chrome 数据目录。
2. 在专属数据目录中创建并选择 `13checkin` profile，对应内部目录 `Profile 1`。
3. 通过 `.env` 指定 `CHROME_PROFILE_DIRECTORY="Profile 1"`。
4. 通过 CDP 打开一亩三分地页面。
5. 签到页识别今天已经完成。
6. 答题页解析出题目、选项，并从公开题库命中答案。

成功日志形态：

```text
Question already appears complete.
Check-in already appears complete.
Done: question=already_done, checkin=already_done, submit=True
Success: All daily tasks (Question and Check-in) are completed!
Closed the Chrome browser launched by this run.
Closed the Chrome process launched by this run.
Closed 1 Chrome tab(s) opened by this run.
```

## 常用操作

打开专属 Chrome profile：

```bash
./scripts/run_daily.sh chrome-cdp-setup
```

本地 dry-run：

```bash
./scripts/run_daily.sh chrome-cdp-dry-run
```

真实运行：

```bash
./scripts/run_daily.sh chrome-cdp
```

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
