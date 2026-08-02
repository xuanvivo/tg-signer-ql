# tg-signer-ql · Telegram 自动签到（青龙面板版）

基于 [amchii/tg-signer](https://github.com/amchii/tg-signer) 封装的青龙面板签到脚本：用你自己的 Telegram 账号，每天定时给机器人/群组发送签到命令、点击按钮，并把结果推送到青龙通知。

## 特性

- 🧾 **一个脚本文件**，依赖自动安装，配置全靠环境变量
- 📱 **网页扫码登录**：在青龙面板的任务日志里直接扫二维码，不用进容器终端
- 👥 **多账号**：扫一次码收集一个账号，自动合并管理，逐个签到
- 🎯 **多目标**：一条变量配置多个机器人/群组，支持发文本、按文本点按钮、发送后定时删除
- 🔁 `@username` 自动解析为数字 ID（兼容 tg-signer 稳定版）
- 📨 **通知美化**：显示真实 TG 用户名、机器人回复原文，去掉日志杂讯
- 🔒 会话文件权限 600，签到记录、配置均存在脚本目录下，卸载即删

## 快速开始

### 1. 放入脚本

把 `tg_signer_ql.py` 放到青龙 `/ql/data/scripts/`（或其子目录），也可以用订阅：

```
青龙 -> 订阅管理 -> 新建订阅
链接: https://github.com/xuanvivo/tg-signer-ql.git
白名单: tg_signer_ql.py
```

### 2. 创建两个任务

| 任务 | 命令 | 定时 |
|---|---|---|
| TG签到 | `task tg_signer_ql.py` | `26 8 * * *` |
| TG扫码登录 | `task tg_signer_ql.py --qrlogin` | `0 0 1 1 *`（只手动跑） |

依赖 `tg-signer` 首次运行会自动 pip 安装；也可以在「依赖管理 → Python3」提前添加 `tg-signer`（可选再加 `TgCrypto` 提速）。要求容器 Python ≥ 3.10。

### 3. 扫码登录

手动运行「TG扫码登录」任务 → 打开运行日志 → 手机 Telegram「设置 → 设备 → 连接桌面设备」→ 扫日志**最下方**的二维码（旧码 30 秒过期，日志不动就点刷新）。

- 开了两步验证的账号：先加环境变量 `TG_2FA_PASSWORD=云密码`，登录完可删除
- 多账号：换个账号再运行一次
- 扫码成功即完成，签到任务会**自动使用**所有扫码过的账号，无需复制任何变量
- 也支持容器终端扫码（`python3 tg_signer_ql.py --qrlogin`）或手机号验证码登录（`--login`）

### 4. 配置签到目标

环境变量 `TG_SIGN_CHATS`，格式：`聊天|发送文本|点击按钮|N秒后删除`（后三段可省略），多个目标用 `&` 或换行分隔，点击按钮支持逗号分隔多个（按顺序点）：

```
@some_bot|/checkin
@some_bot|/checkin|签到
@some_bot|/checkin|签到|30&@another_bot|/sign&-1001234567890|打卡
```

> 注意：目标需要在你的最近对话里（先手动给机器人发过一次 `/start`）。

## 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `TG_SIGN_CHATS` | 签到目标（见上） | — |
| `TG_PROXY` | 代理，如 `socks5://127.0.0.1:7890`（大陆服务器必配） | 无 |
| `TG_SESSION_STRING` | 会话字符串，多账号用 `&` 分隔（设置后优先于扫码文件） | 无 |
| `TG_2FA_PASSWORD` | 网页扫码时的两步验证密码 | 无 |
| `TG_QR_LOGIN` | 设 `true` 让主任务进入扫码模式（命令传参的备用方案） | 无 |
| `TG_API_ID` / `TG_API_HASH` | 自有 API 凭据 | tg-signer 内置 |
| `TG_ACCOUNT` | 文件会话账号名，多个用 `&` 分隔 | `my_account` |
| `TG_SIGN_TASK` | tg-signer 任务名 | `ql_sign` |
| `TG_SIGNER_DIR` | 数据目录 | `脚本目录/tg_signer_data` |
| `TG_SIGN_TIMEOUT` | 单账号超时（秒） | `600` |
| `TG_SIGN_DIALOGS` | 登录拉取的最近对话数 | `20` |
| `TG_SIGN_NOTIFY` | 设 `false` 关闭通知 | `true` |

## 通知效果

```
👤 张三 @zhangsan — ✅ 成功
  📨 已发送: /checkin
  🤖 some_bot: ✅ 签到成功！获得 5 积分 [按钮] 我的积分 | 邀请好友

👤 李四 @lisi — ✅ 成功
  📨 已发送: /checkin
  🤖 some_bot: 今天已经签到过啦
```

## 常见问题

- **收不到消息/超时**：检查 `TG_PROXY`；确认目标在最近对话里
- **`@username` 解析失败**：先给该机器人发一条 `/start`，或在 `TG_SIGN_CHATS` 里直接填数字 ID（任务日志的对话列表里能查到）
- **会话失效**：重新扫码即可，同账号自动覆盖
- **高级玩法**（骰子 / AI 识图 / 算术题）：直接编辑 `数据目录/.signer/signs/<任务名>/config.json`，字段格式见 tg-signer 文档；不设置 `TG_SIGN_CHATS` 时脚本不会覆盖手工配置

## 安全提醒

session 文件 / 会话字符串等同于账号完全控制权，**不要泄露给任何人**，不要提交到仓库。扫码后手机上会出现一个"桌面设备"会话，属正常现象，可随时在手机上注销。

## 致谢

- [amchii/tg-signer](https://github.com/amchii/tg-signer) — 核心签到能力全部来自该项目

仅供学习交流，请遵守 Telegram 服务条款，勿用于骚扰、滥用行为。
