#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 自动签到 · 青龙面板脚本（基于 tg-signer 封装）
参照项目: https://github.com/amchii/tg-signer

cron: 26 8 * * *
new Env('TG签到(tg-signer)');

────────────────────────── 使用说明 ──────────────────────────
0. 要求: 青龙容器 Python >= 3.10；大陆服务器需配置 TG_PROXY 代理

1. 安装依赖（二选一）:
   - 青龙「依赖管理」->「Python3」添加: tg-signer（可选再加 TgCrypto 提速）
   - 或什么都不做，本脚本首次运行会自动 pip 安装

2. 登录（推荐 A，免进容器）:
   A. 网页扫码: 青龙新建一个定时任务，命令:
        task 自用/tg.py --qrlogin      （路径按实际位置；定时随意如 0 0 1 1 *）
      手动运行该任务 -> 点开运行日志 -> 手机 Telegram「设置->设备->连接
      桌面设备」扫日志里**最下方**的二维码（旧码30秒过期，日志不动就点刷新）。
      开了两步验证(2FA)的账号，先加环境变量 TG_2FA_PASSWORD=云密码。
      扫码成功即完成——签到任务会自动使用所有扫码过的账号，无需复制变量。
      多账号: 换个账号再运行一次该任务。
      （若你的青龙版本任务命令不支持传参，就给主任务加环境变量
        TG_QR_LOGIN=true 后运行主任务，效果相同，用完删掉该变量）
   B. 容器终端扫码: python3 <本脚本> --qrlogin
   C. 手机号+验证码: python3 <本脚本> --login [账号名]

   账号来源优先级: TG_SESSION_STRING 环境变量 > 扫码保存的 qr_*.session_string
   文件 > TG_ACCOUNT 文件会话。设置了 TG_SESSION_STRING 就以它为准。

3. 环境变量:
   TG_SIGN_CHATS      签到目标，格式: 聊天|发送文本|点击按钮|N秒后删除
                      后三段均可省略；多个目标用 & 或换行分隔；
                      点击按钮支持逗号分隔多个（按顺序点击）
                      聊天可填 @username 或数字ID（@username 会自动解析）
                      默认: @githubghs_bot|/checkin
                      示例: @githubghs_bot|/checkin|签到|30&@xx_bot|/sign
   TG_SESSION_STRING  会话字符串（可选；多账号用 & 或换行分隔）
   TG_2FA_PASSWORD    网页扫码登录时的两步验证云密码（可选，登录完可删除）
   TG_QR_LOGIN        设为 true 让主任务进入扫码模式（可选，命令传参的备用方案）
   TG_PROXY           代理（可选），如 socks5://127.0.0.1:7890
   TG_API_ID/TG_API_HASH  自有 API 凭据（可选，tg-signer 有内置默认值）
   TG_ACCOUNT         文件会话账号名（可选，默认 my_account，多个用 & 分隔）
   TG_SIGN_TASK       任务名（可选，默认 ql_sign）
   TG_SIGNER_DIR      数据目录（可选，默认 脚本目录/tg_signer_data）
   TG_SIGN_TIMEOUT    单账号超时秒数（可选，默认 600）
   TG_SIGN_DIALOGS    登录时拉取的最近对话数（可选，默认 20）
   TG_SIGN_NOTIFY     设为 false 关闭青龙通知（可选）

   注意: 目标聊天需要在最近对话里（先手动给机器人发过一次 /start）。
   高级玩法（骰子/AI 识图/算术题等）可在容器里直接编辑
   <数据目录>/.signer/signs/<任务名>/config.json
──────────────────────────────────────────────────────────────
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("TG_SIGNER_DIR") or (SCRIPT_DIR / "tg_signer_data"))
SESSION_DIR = DATA_DIR / "sessions"   # <账号>.session / <账号>.session_string
WORKDIR = DATA_DIR / ".signer"        # tg-signer 工作目录（配置、签到记录）
LOG_DIR = DATA_DIR / "logs"

TASK_NAME = os.environ.get("TG_SIGN_TASK", "ql_sign").strip() or "ql_sign"
DEFAULT_CHATS = "@githubghs_bot|/checkin"
TIMEOUT = int(os.environ.get("TG_SIGN_TIMEOUT", "600") or 600)
NUM_DIALOGS = os.environ.get("TG_SIGN_DIALOGS", "20").strip() or "20"
NOTIFY_TITLE = "TG签到 (tg-signer)"

# tg-signer 日志前缀 / 「账户-任务」前缀，摘要时去掉
LOG_PREFIX = re.compile(r"^\[\w+\]\s*\[tg-signer\]\s*[\d-]+\s[\d:,]+\s\S+\s\d+\s")
TASK_PREFIX = re.compile(r"^账户「[^」]*」- 任务「[^」]*」:\s*")
FAIL_MARK = re.compile(r"签到失败|Traceback|Unauthorized|EOFError|AUTH_KEY|SESSION_REVOKED", re.I)
AUTH_DEAD = re.compile(r"AUTH_KEY_UNREGISTERED|AUTH_KEY_INVALID|SESSION_REVOKED|SESSION_EXPIRED|USER_DEACTIVATED", re.I)
RE_EXC_LINE = re.compile(r"^[A-Za-z_][\w.]*: .+")  # Traceback 末尾的异常行

RE_DONE = re.compile(r"处理完成: action=<SupportAction\.(\w+): \d+>(?:\s+(?:text|dice)='(.*)')?")
RE_REPLY = re.compile(r"收到来自「(.+?)」的消息:\s*(.*)")
ACTION_LABELS = {
    "SEND_TEXT": "📨 已发送",
    "SEND_DICE": "🎲 已发骰子",
    "CLICK_KEYBOARD_BY_TEXT": "👆 已点击按钮",
    "CHOOSE_OPTION_BY_IMAGE": "🖼 已完成图片选项",
    "REPLY_BY_CALCULATION_PROBLEM": "🧮 已回复算术题",
}


def ensure_dirs():
    for d in (DATA_DIR, SESSION_DIR, WORKDIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def ensure_tg_signer() -> bool:
    if sys.version_info < (3, 10):
        print(f"当前 Python {sys.version.split()[0]}，tg-signer 需要 >= 3.10，请升级青龙镜像")
        return False
    try:
        import tg_signer  # noqa: F401
        return True
    except ImportError:
        pass
    print("未检测到 tg-signer，尝试自动安装（也可在青龙依赖管理中添加）...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "tg-signer"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    print(r.stdout[-2000:])
    try:
        import tg_signer  # noqa: F401
        return True
    except ImportError:
        print("tg-signer 安装失败，请手动安装后重试")
        return False


def base_cmd() -> list:
    """通过入口函数调用 CLI，避免 tg-signer 可执行文件不在 PATH 的问题"""
    return [
        sys.executable, "-c", "from tg_signer.__main__ import signer; signer()",
        "--log-dir", str(LOG_DIR),
        "--log-file", str(LOG_DIR / "tg-signer.log"),
        "--session_dir", str(SESSION_DIR),
        "-w", str(WORKDIR),
    ]


def sub_env(session_string):
    env = os.environ.copy()
    if session_string:
        env["TG_SESSION_STRING"] = session_string
    else:
        env.pop("TG_SESSION_STRING", None)
    return env


def parse_chats(raw: str) -> list:
    """解析 TG_SIGN_CHATS -> SignConfigV3 的 chats 列表（@username 稍后解析为数字ID）"""
    chats = []
    for item in re.split(r"[&\n]", raw):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]
        chat = parts[0]
        send_text = parts[1] if len(parts) > 1 and parts[1] else None
        click_text = parts[2] if len(parts) > 2 and parts[2] else None
        delete_after = None
        if len(parts) > 3 and parts[3]:
            delete_after = int(parts[3])

        # chat 支持 @username / username / 数字id
        if not chat.startswith("@"):
            if re.fullmatch(r"-?\d+", chat):
                chat = int(chat)
            elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", chat):
                chat = "@" + chat
            else:
                raise ValueError(f"无法识别的 chat: {chat!r}（username 请以 @ 开头）")

        actions = []
        if send_text:
            actions.append({"action": 1, "text": send_text})       # 发送普通文本
        if click_text:
            for btn in click_text.split(","):
                if btn.strip():
                    actions.append({"action": 3, "text": btn.strip()})  # 按文本点击键盘
        if not actions:
            raise ValueError(f"目标 {chat} 未配置任何动作（至少要有发送文本或点击按钮）")

        chats.append({
            "chat_id": chat,
            "message_thread_id": None,
            "name": None,
            "delete_after": delete_after,
            "actions": actions,
            "action_interval": 2,
        })
    if not chats:
        raise ValueError("TG_SIGN_CHATS 解析结果为空")
    return chats


def supports_str_chat_id() -> bool:
    """探测当前安装的 tg-signer 是否支持 @username 形式的 chat_id（0.8.6 及以前只支持数字）"""
    try:
        from tg_signer.config import SignChatV3
        SignChatV3(chat_id="@probe_test", actions=[{"action": 1, "text": "t"}])
        return True
    except Exception:
        return False


def load_username_map() -> dict:
    """从 tg-signer 登录时缓存的最近对话里建立 username -> 数字ID 映射"""
    m = {}
    users_dir = WORKDIR / "users"
    if users_dir.is_dir():
        for f in users_dir.glob("*/latest_chats.json"):
            try:
                for c in json.loads(f.read_text(encoding="utf-8")):
                    u = (c.get("username") or "").lower()
                    if u and c.get("id") is not None:
                        m[u] = c["id"]
            except Exception:
                pass
    return m


def pick_login_account(accounts):
    """挑一个已有会话的账号用于刷新对话缓存"""
    for name, ss in accounts:
        if ss or (SESSION_DIR / f"{name}.session").exists():
            return name, ss
    return None


def refresh_dialogs(account: str, session_string):
    """非交互刷新最近对话缓存（已登录会话不会要求输入）"""
    print(f"用账号「{account}」刷新最近对话缓存...")
    cmd = base_cmd() + ["-a", account, "login", "-n", "100"]
    try:
        r = subprocess.run(
            cmd, cwd=str(DATA_DIR), env=sub_env(session_string), timeout=180, text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if r.returncode != 0:
            print((r.stdout or "")[-800:])
    except subprocess.TimeoutExpired:
        print("刷新最近对话超时（检查网络/代理）")


def resolve_usernames(chats: list, accounts: list):
    """把 @username 解析成数字ID（旧版 tg-signer 只认数字）。返回 (chats, 未解析列表)"""
    need = [c for c in chats if isinstance(c["chat_id"], str)]
    if not need:
        return chats, []
    if supports_str_chat_id():
        return chats, []  # 新版直接支持 @username，无需解析

    m = load_username_map()
    if any(c["chat_id"].lstrip("@").lower() not in m for c in need):
        acct = pick_login_account(accounts)
        if acct:
            refresh_dialogs(*acct)
            m = load_username_map()

    unresolved = []
    for c in chats:
        cid = c["chat_id"]
        if isinstance(cid, str):
            rid = m.get(cid.lstrip("@").lower())
            if rid is None:
                unresolved.append(cid)
            else:
                print(f"解析 {cid} -> {rid}")
                c["chat_id"] = rid
    return chats, unresolved


def write_config(chats: list):
    """写任务配置；写之前用当前安装版本的模型校验，返回错误信息或 None"""
    cfg = {
        "chats": chats,
        "sign_at": "08:00:00",  # run-once 强制执行，此字段仅为格式需要
        "random_seconds": 0,
        "sign_interval": 2,
    }
    try:
        from tg_signer.config import SignConfigV3
        if SignConfigV3.load(cfg) is None:
            return "生成的配置未通过当前 tg-signer 版本的校验（版本字段差异），请附日志反馈"
    except ImportError:
        pass
    cfg_file = WORKDIR / "signs" / TASK_NAME / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return None


def get_accounts() -> list:
    """返回 [(账号名, session_string或None), ...]
    优先级: TG_SESSION_STRING 环境变量 > 扫码保存的 qr_* 文件 > TG_ACCOUNT 文件会话"""
    ss_raw = os.environ.get("TG_SESSION_STRING", "").strip()
    if ss_raw:
        sessions = [s.strip() for s in re.split(r"[&\n]", ss_raw) if s.strip()]
        return [(f"session{i}", s) for i, s in enumerate(sessions, 1)]
    qr = []
    for p in sorted(SESSION_DIR.glob("qr_*.session_string")):
        s = p.read_text(encoding="utf-8").strip()
        if s:
            qr.append((p.stem, s))
    if qr:
        return qr
    acc_raw = os.environ.get("TG_ACCOUNT", "my_account").strip() or "my_account"
    return [(a.strip(), None) for a in re.split(r"[&\n]", acc_raw) if a.strip()]


LOGIN_GUIDE = (
    "未登录。请进入青龙容器终端，在脚本目录执行:\n"
    "  python3 {script} --qrlogin   （扫码，推荐）\n"
    "或 python3 {script} --login {account}   （手机号+验证码）\n"
    "或将 session string 填入环境变量 TG_SESSION_STRING"
)


def decode_session_user(ss: str):
    """从 pyrogram/kurigram session string 反解出 user_id（兼容新旧格式）"""
    import base64
    import struct
    try:
        data = base64.urlsafe_b64decode(ss.strip() + "=" * (-len(ss.strip()) % 4))
    except Exception:
        return None
    for fmt, idx in ((">BI?256sQ?", 4), (">B?256sQ?", 3), (">B?256sI?", 3)):
        if struct.calcsize(fmt) == len(data):
            try:
                return struct.unpack(fmt, data)[idx]
            except Exception:
                return None
    return None


def tg_display_name(uid) -> str:
    """优先 qr_accounts.json，其次 tg-signer 运行时写的 me.json"""
    uid = str(uid)
    names = load_qr_names()
    if uid in names:
        return names[uid]
    me_file = WORKDIR / "users" / uid / "me.json"
    if me_file.exists():
        try:
            d = json.loads(me_file.read_text(encoding="utf-8"))
            name = " ".join(filter(None, [d.get("first_name") or "", d.get("last_name") or ""])).strip()
            if d.get("username"):
                name = f"{name} @{d['username']}".strip()
            if name:
                return name
        except Exception:
            pass
    return ""


def account_label(account: str, session_string) -> str:
    """通知里显示的账号名：优先真实 TG 用户名"""
    uid = None
    if session_string:
        uid = decode_session_user(session_string)
    else:
        ss_file = SESSION_DIR / f"{account}.session_string"
        if ss_file.exists():
            uid = decode_session_user(ss_file.read_text(encoding="utf-8"))
    if uid:
        name = tg_display_name(uid)
        if name:
            return name
    return account


def summarize_output(out: str) -> list:
    """把 tg-signer 原始日志浓缩成人类友好的事件行"""
    events = []
    reply = None  # (来源, [内容片段])

    def flush():
        nonlocal reply
        if reply:
            frm, pieces = reply
            content = re.sub(r"\s+", " ", " ".join(p for p in pieces if p)).strip()
            content = content.rstrip(" |").strip() or "(非文本消息)"
            if len(content) > 200:
                content = content[:200] + "…"
            line = f"🤖 {frm}: {content}"
            if line not in events:  # 消息被编辑时会重复上报，去重
                events.append(line)
            reply = None

    for ln in out.splitlines():
        is_log = bool(LOG_PREFIX.match(ln))
        if not is_log:
            if reply is not None:  # readable_message 的多行内容
                c = ln.strip()
                if not c or c == "Message:":
                    continue
                if c.startswith("text:"):
                    reply[1].append(c[5:].strip())
                elif c.startswith("图片:"):
                    reply[1].append("[图片]" + c[3:].strip())
                elif c.startswith("InlineKeyboard"):
                    reply[1].append("[按钮]")
                else:
                    reply[1].append(c)
            continue

        c = TASK_PREFIX.sub("", LOG_PREFIX.sub("", ln)).strip()

        m = RE_REPLY.match(c)
        if m:
            flush()
            reply = (m.group(1), [m.group(2).strip()])
            continue
        flush()

        m = RE_DONE.match(c)
        if m:
            label = ACTION_LABELS.get(m.group(1), "✔ " + m.group(1))
            events.append(f"{label}: {m.group(2)}" if m.group(2) else label)
            continue
        if c.startswith("签到失败"):
            events.append("❌ " + c)
        elif c.startswith("等待超时"):
            events.append("⚠️ 等待回复超时（未匹配到按钮/新消息）")
    flush()
    return events


def run_account(account: str, session_string) -> tuple:
    """执行一次签到，返回 (是否成功, 摘要文本)"""
    if session_string is None and not (SESSION_DIR / f"{account}.session").exists():
        guide = LOGIN_GUIDE.format(script=Path(__file__).name, account=account)
        print(guide)
        return False, guide

    cmd = base_cmd() + ["-a", account, "run-once", TASK_NAME, "-n", NUM_DIALOGS]
    try:
        p = subprocess.run(
            cmd, cwd=str(DATA_DIR), env=sub_env(session_string), timeout=TIMEOUT, text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        out = p.stdout or ""
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        print(out)
        return False, f"执行超时（>{TIMEOUT}s），请检查网络/代理(TG_PROXY)"

    print(out)
    if "TgCrypto is missing" in out:
        print("提示: 依赖管理添加 TgCrypto 可提速（可选，不影响功能）")

    ok = rc == 0 and not FAIL_MARK.search(out)
    events = summarize_output(out)

    if AUTH_DEAD.search(out):  # 会话已在服务端被注销（如手机上清了设备会话）
        quarantined = quarantine_session(account, session_string)
        tip = "🔑 该账号会话已被注销/失效，请重新运行扫码登录任务"
        if quarantined:
            tip += f"（已隔离失效会话文件 {quarantined}）"
        elif session_string is not None and not account.startswith("qr_"):
            tip += "，并更新/删除环境变量 TG_SESSION_STRING 里对应的串"
        events.append(tip)
    elif "EOFError" in out:
        events.append("🔑 会话缺失或已失效，请重新扫码（--qrlogin）或更新 TG_SESSION_STRING")

    if not ok and not any(e.startswith(("❌", "🔑")) for e in events):
        exc = [ln.strip() for ln in out.splitlines()
               if RE_EXC_LINE.match(ln.strip()) and not ln.strip().startswith("File ")]
        if exc:
            events.append("❌ " + exc[-1][:160])
        else:
            tail = [ln for ln in out.strip().splitlines() if ln.strip()][-3:]
            events.append("❌ 失败详情: " + " / ".join(ln.strip()[:80] for ln in tail))
    if not events:
        events.append(f"（无关键输出，退出码 {rc}）")
    return ok, "\n".join(events)


def quarantine_session(account: str, session_string) -> str:
    """把已被服务端注销的会话文件改名隔离，避免每天反复撞 401。返回被隔离的文件名"""
    if session_string is None:
        f = SESSION_DIR / f"{account}.session"
    elif account.startswith("qr_"):
        f = SESSION_DIR / f"{account}.session_string"
    else:
        return ""  # 环境变量里的串，无文件可隔离
    if f.exists():
        dead = Path(str(f) + ".bad")
        f.rename(dead)
        return dead.name
    return ""


def send_notify(title: str, content: str):
    if os.environ.get("TG_SIGN_NOTIFY", "true").lower() in ("false", "0", "no"):
        return
    for p in ("/ql/data/scripts", "/ql/scripts", str(SCRIPT_DIR)):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    try:
        from notify import send  # 青龙内置通知
        send(title, content)
    except Exception as e:
        print(f"[通知] 未发送（notify 模块不可用: {e}）")


def fail_exit(msg: str):
    print(msg)
    send_notify(NOTIFY_TITLE, msg)
    sys.exit(1)


def do_login():
    """容器终端交互式登录: python3 <脚本> --login [账号名]"""
    args = [a for a in sys.argv[1:] if a != "--login"]
    account = args[0] if args else get_accounts()[0][0]
    print(f"开始登录账号「{account}」，请按提示输入手机号(带区号，如 +86...)、验证码")
    cmd = base_cmd() + ["-a", account, "login", "-n", NUM_DIALOGS]
    rc = subprocess.run(cmd, cwd=str(DATA_DIR), env=sub_env(None)).returncode
    ss_file = SESSION_DIR / f"{account}.session_string"
    if rc == 0 and ss_file.exists():
        print(f"\n登录成功。会话文件: {SESSION_DIR / (account + '.session')}")
        print(f"如需改用环境变量方式，可将此文件内容填入 TG_SESSION_STRING（注意保密）:\n  {ss_file}")
    sys.exit(rc)


# ──────────────────────────── 二维码登录 ────────────────────────────

def pip_install(pkg: str) -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", pkg],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if r.returncode != 0:
        print(r.stdout[-1500:])
    return r.returncode == 0


def parse_proxy():
    """与 tg-signer 相同的 TG_PROXY 解析，供扫码登录直接连接使用"""
    raw = os.environ.get("TG_PROXY", "").strip()
    if not raw:
        return None
    from urllib.parse import urlparse
    r = urlparse(raw)
    return {"scheme": r.scheme, "hostname": r.hostname, "port": r.port,
            "username": r.username, "password": r.password}


def print_qr(url: str, log_mode: bool = False):
    import qrcode
    q = qrcode.QRCode(border=2 if log_mode else 1)
    q.add_data(url)
    q.make(fit=True)
    if log_mode:
        # 网页日志里用实心块画码，佩戴白边，方便浏览器里直接扫
        for row in q.get_matrix():
            print("".join("██" if c else "  " for c in row), flush=True)
    else:
        q.print_ascii(invert=True)


def load_qr_names() -> dict:
    f = SESSION_DIR / "qr_accounts.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def qr_collected_summary():
    """汇总已收集的 qr_*.session_string，返回 (账号描述列表, & 合并值)"""
    names = load_qr_names()
    descs, strings = [], []
    for p in sorted(SESSION_DIR.glob("qr_*.session_string")):
        s = p.read_text(encoding="utf-8").strip()
        if not s:
            continue
        uid = p.stem[3:]
        descs.append(f"{names[uid]}({uid})" if uid in names else uid)
        strings.append(s)
    return descs, "&".join(strings)


def do_qr_login():
    try:
        import qrcode  # noqa: F401
    except ImportError:
        print("安装二维码库 qrcode ...")
        if not pip_install("qrcode"):
            print("qrcode 安装失败，请手动执行: pip3 install qrcode")
            sys.exit(1)
    import asyncio
    try:
        asyncio.run(_qr_login())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)


async def _qr_login():
    import asyncio
    import base64
    import time
    from pyrogram import Client, raw, types
    from pyrogram.errors import BadRequest, FloodWait, SessionPasswordNeeded

    api_id = int(os.environ.get("TG_API_ID", "").strip() or 611335)
    api_hash = os.environ.get("TG_API_HASH", "").strip() or "d524b414d21f4d37f08684c1df41ac9c"
    interactive = sys.stdin.isatty()
    log_mode = not interactive  # 青龙任务里运行：二维码画进任务日志，网页上扫

    client = Client("qr_login_tmp", api_id=api_id, api_hash=api_hash,
                    proxy=parse_proxy(), in_memory=True, workdir=str(SESSION_DIR))

    async def finalize(authorization) -> "types.User":
        u = authorization.user
        await client.storage.user_id(u.id)
        await client.storage.is_bot(False)
        return types.User._parse(client, u)

    async def ask_2fa() -> "types.User":
        if not interactive:
            pwd = os.environ.get("TG_2FA_PASSWORD", "").strip()
            if not pwd:
                print("该账号开启了两步验证(2FA)。请在青龙「环境变量」添加 TG_2FA_PASSWORD=你的云密码，"
                      "然后重新运行本任务（登录完成后可删除该变量）", flush=True)
                raise SystemExit(1)
            try:
                return await client.check_password(pwd)
            except BadRequest as e:
                print(f"TG_2FA_PASSWORD 密码不正确（{e}），请修改后重新运行", flush=True)
                raise SystemExit(1)
        import getpass
        print("该账号开启了两步验证(2FA)")
        while True:
            try:
                pwd = getpass.getpass("请输入云密码(输入不回显): ")
            except Exception:
                pwd = input("请输入云密码: ")
            if not pwd.strip():
                continue
            try:
                return await client.check_password(pwd.strip())
            except BadRequest as e:
                print(f"密码不对，再试一次（{e}）")

    async def migrate(dc_id: int):
        # 与 kurigram 登录内部的 DC 迁移处理保持一致
        dc_option = await client.get_dc_option(dc_id, ipv6=client.ipv6)
        await client.session.stop()
        client.session = await client.get_session(
            dc_id=dc_id,
            server_address=dc_option.ip_address,
            port=dc_option.port,
            export_authorization=False,
            temporary=True,
        )
        await client.storage.dc_id(dc_id)
        await client.storage.server_address(dc_option.ip_address)
        await client.storage.port(dc_option.port)
        await client.storage.auth_key(client.session.auth_key)

    print("连接 Telegram 中...", flush=True)
    await client.connect()
    if log_mode:
        print("【网页扫码】手机 Telegram -> 设置 -> 设备 -> 连接桌面设备", flush=True)
        print("扫描日志里**最下方**的二维码（约30秒换一张新码，日志不动就点右上角刷新）", flush=True)

    user, shown = None, None
    deadline = time.monotonic() + 300
    while user is None and time.monotonic() < deadline:
        try:
            r = await client.invoke(raw.functions.auth.ExportLoginToken(
                api_id=api_id, api_hash=api_hash, except_ids=[]))
        except SessionPasswordNeeded:
            user = await ask_2fa()
            break
        except FloodWait as e:
            print(f"触发限频，等待 {e.value}s ...", flush=True)
            await asyncio.sleep(int(e.value))
            continue

        if isinstance(r, raw.types.auth.LoginTokenSuccess):
            user = await finalize(r.authorization)
        elif isinstance(r, raw.types.auth.LoginTokenMigrateTo):
            await migrate(r.dc_id)
            try:
                r2 = await client.invoke(raw.functions.auth.ImportLoginToken(token=r.token))
            except SessionPasswordNeeded:
                user = await ask_2fa()
                break
            if isinstance(r2, raw.types.auth.LoginTokenSuccess):
                user = await finalize(r2.authorization)
        else:  # LoginToken: 令牌约30秒过期，变化时重绘二维码
            if r.token != shown:
                shown = r.token
                url = "tg://login?token=" + base64.urlsafe_b64encode(r.token).decode().rstrip("=")
                stamp = time.strftime("%H:%M:%S")
                if log_mode:
                    print(f"\n──────── 二维码已更新 {stamp}（扫这张）────────", flush=True)
                else:
                    print("\n手机 Telegram -> 设置 -> 设备 -> 连接桌面设备，扫描下方二维码:")
                print_qr(url, log_mode)
                if not log_mode:
                    print("等待扫码...（二维码约30秒自动刷新；扫不出可放大终端窗口再等下一张）")
            await asyncio.sleep(2)

    if user is None:
        print("超时(5分钟)未完成扫码，请重新运行", flush=True)
        await client.disconnect()
        sys.exit(1)

    ss = await client.export_session_string()
    await client.disconnect()

    uid = str(user.id)
    name = " ".join(filter(None, [user.first_name or "", user.last_name or ""])).strip() \
        or (user.username or uid)
    sf = SESSION_DIR / f"qr_{uid}.session_string"
    sf.write_text(ss, encoding="utf-8")
    os.chmod(sf, 0o600)
    names = load_qr_names()
    names[uid] = name
    (SESSION_DIR / "qr_accounts.json").write_text(
        json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")

    descs, combined = qr_collected_summary()
    bar = "─" * 50
    print(f"\n✅ 登录成功: {name} (id {uid})", flush=True)
    print(f"已收集 {len(descs)} 个账号: {'、'.join(descs)}", flush=True)
    env_ss = os.environ.get("TG_SESSION_STRING", "").strip()
    if env_ss:
        if interactive:
            print(f"\n⚠️ 检测到已设置环境变量 TG_SESSION_STRING（它的优先级更高）。\n"
                  f"要让签到跑扫码收集的全部账号，请删除该变量，或把它的值更新为:\n{combined}", flush=True)
        else:
            print("\n⚠️ 检测到已设置环境变量 TG_SESSION_STRING（它的优先级更高）。\n"
                  "建议删除该变量，签到会自动使用扫码收集的全部账号。", flush=True)
    else:
        print("\n签到任务会自动使用以上账号，无需再配置任何变量。", flush=True)
    if interactive:
        print(f"{bar}\n备用: 本账号 session string（迁移或手动配置时才用得到，注意保密）:\n{ss}")
    else:
        print("（网页模式出于安全不在日志显示 session string；如确实需要该字符串，"
              "请在容器终端运行 --qrlogin）", flush=True)
    print("\n再加一个账号：换个账号扫码，重新运行一次本任务/命令即可", flush=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # 任务日志实时刷新（扫码依赖这个）
    print(f"数据目录: {DATA_DIR}\n任务名: {TASK_NAME}")
    ensure_dirs()
    if not ensure_tg_signer():
        sys.exit(1)

    if "--qrlogin" in sys.argv or \
            os.environ.get("TG_QR_LOGIN", "").strip().lower() in ("1", "true", "yes"):
        do_qr_login()
        return

    if "--login" in sys.argv:
        do_login()
        return

    accounts = get_accounts()
    env_ss = os.environ.get("TG_SESSION_STRING", "").strip()
    if env_ss:
        _, qr_combined = qr_collected_summary()
        if qr_combined:
            env_set = set(s.strip() for s in re.split(r"[&\n]", env_ss) if s.strip())
            if env_set != set(qr_combined.split("&")):
                print("提示: TG_SESSION_STRING 与扫码收集的账号不一致，当前以环境变量为准；"
                      "删除该变量即可改用全部扫码账号")
    raw = os.environ.get("TG_SIGN_CHATS", "").strip()
    cfg_file = WORKDIR / "signs" / TASK_NAME / "config.json"

    if raw or not cfg_file.exists():
        try:
            chats = parse_chats(raw or DEFAULT_CHATS)
        except (ValueError, TypeError) as e:
            fail_exit(f"TG_SIGN_CHATS 配置有误: {e}")
        chats, unresolved = resolve_usernames(chats, accounts)
        if unresolved:
            fail_exit(
                f"无法把 {'、'.join(unresolved)} 解析成数字ID（当前 tg-signer 发行版仅支持数字 chat_id）。\n"
                "请确认已在 Telegram 中给该机器人/群发过消息（如 /start）后重跑；\n"
                "或直接在 TG_SIGN_CHATS 里填数字ID（运行日志的最近对话列表中可查到）"
            )
        err = write_config(chats)
        if err:
            fail_exit(err)
        print(f"已生成配置: {cfg_file}")
    else:
        print(f"使用已有任务配置: {cfg_file}")

    results, all_ok = [], True
    for account, ss in accounts:
        print(f"\n========== 账号「{account}」 ==========")
        ok, summary = run_account(account, ss)
        all_ok = all_ok and ok
        label = account_label(account, ss)  # 运行后 me.json 已生成，可取到真实用户名
        body = "\n".join("  " + ln for ln in summary.splitlines())
        results.append(f"👤 {label} — {'✅ 成功' if ok else '❌ 失败'}\n{body}")

    content = "\n\n".join(results)
    print("\n" + "=" * 34 + "\n" + content)
    send_notify(NOTIFY_TITLE, content)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
