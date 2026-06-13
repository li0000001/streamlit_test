#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 导入所有需要的库
import os
import json
import re
import time
import shutil
import socket
import subprocess
import platform
import uuid
from pathlib import Path
import urllib.request
import urllib.parse
import tarfile
import streamlit as st

# --- 全局常量定义 ---
# 工作目录，所有运行时文件都将存放在这里
INSTALL_DIR = Path.home() / ".agsb"
# 各种运行时文件的具体路径
SB_PID_FILE = INSTALL_DIR / "sbpid.log"
LIST_FILE = INSTALL_DIR / "list.txt"
SB_LOG_FILE = INSTALL_DIR / "sb.log"
ALL_NODES_FILE = INSTALL_DIR / "allnodes.txt"
REALITY_KEY_FILE = INSTALL_DIR / "reality_key.json"

# Reality 默认配置
DEFAULT_REALITY_SNI = "www.microsoft.com"  # 借用的大站（必须支持 TLS 1.3 + H2）
DEFAULT_REALITY_PORT = 443  # sing-box 监听端口
# 备选借用站点（用户可自选，TLS 1.3 + H2 即可）
SNI_CHOICES = [
    "www.microsoft.com",
    "www.apple.com",
    "www.cloudflare.com",
    "gateway.icloud.com",
    "www.samsung.com",
    "www.mozilla.org",
    "www.google.com",
]

# --- 辅助函数 ---

def download_file(url, target_path, silent=False):
    """下载文件，可选择是否在界面上显示错误信息。"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as response, open(target_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        return True
    except Exception as e:
        if not silent:
            st.error(f"下载失败: {url}, 错误: {e}")
        return False


def get_external_ip(timeout=8):
    """获取容器的外网 IP，用于客户端连接。"""
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://ipinfo.io/ip",
        "https://checkip.amazonaws.com",
    ]
    for url in services:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.88'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ip = resp.read().decode('utf-8').strip()
            # 简单校验 IPv4
            if ip and len(ip.split('.')) == 4:
                return ip
        except Exception:
            continue
    return None


def load_or_generate_reality_keys(singbox_path):
    """加载已持久化的 Reality 密钥对和短 ID；如果没有则生成新的。"""
    if REALITY_KEY_FILE.exists():
        try:
            data = json.loads(REALITY_KEY_FILE.read_text())
            if data.get("private_key") and data.get("public_key") and data.get("short_id"):
                return data
        except Exception:
            pass

    # 生成新密钥对
    # sing-box 1.10.x 输出格式: "PrivateKey: xxx"（无空格）
    # sing-box 1.9.x  输出格式: "Private key: xxx"（带空格）
    # 下面用正则同时兼容两种格式
    try:
        result = subprocess.run(
            [str(singbox_path), 'generate', 'reality-keypair'],
            capture_output=True, text=True, check=True
        )
        private_key, public_key = None, None
        # 正则匹配 "Private[ ]?Key" 和 "Public[ ]?Key"，不区分大小写
        priv_match = re.search(r'private[\s_]?key\s*:\s*(\S+)', result.stdout, re.IGNORECASE)
        pub_match = re.search(r'public[\s_]?key\s*:\s*(\S+)', result.stdout, re.IGNORECASE)
        if priv_match:
            private_key = priv_match.group(1).strip()
        if pub_match:
            public_key = pub_match.group(1).strip()
        if not private_key or not public_key:
            raise RuntimeError(f"无法解析密钥对输出: {result.stdout}")

        # 生成短 ID（输出为单行十六进制）
        sid_result = subprocess.run(
            [str(singbox_path), 'generate', 'reality-shortid'],
            capture_output=True, text=True, check=True
        )
        # 取第一个看起来像十六进制的 token
        sid_match = re.search(r'\b([0-9a-fA-F]{8,32})\b', sid_result.stdout)
        short_id = sid_match.group(1) if sid_match else sid_result.stdout.strip().split('\n')[0].strip()
        if not short_id or len(short_id) < 4:
            raise RuntimeError(f"无法生成短 ID: {sid_result.stdout}")
    except Exception as e:
        raise RuntimeError(f"Reality 密钥生成失败: {e}")

    keys = {
        "private_key": private_key,
        "public_key": public_key,
        "short_id": short_id,
    }
    REALITY_KEY_FILE.write_text(json.dumps(keys, indent=2))
    return keys


def generate_vless_reality_link(config):
    """根据配置字典生成 VLESS Reality 链接字符串。"""
    # 标准的 vless 链接格式: vless://uuid@host:port?params#name
    params = {
        "type": "tcp",
        "security": "reality",
        "flow": "xtls-rprx-vision",
        "sni": config["sni"],
        "fp": "chrome",          # 客户端 UA 指纹
        "pbk": config["public_key"],
        "sid": config["short_id"],
    }
    param_str = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items()
    )
    name = urllib.parse.quote(config["ps"], safe='')
    return f"vless://{config['uuid']}@{config['add']}:{config['port']}?{param_str}#{name}"


def stop_services():
    """停止所有由本脚本启动的后台服务进程。"""
    if SB_PID_FILE.exists():
        try:
            pid = int(SB_PID_FILE.read_text().strip())
            os.kill(pid, 9)  # 强制终止进程
        except (ValueError, ProcessLookupError, FileNotFoundError, PermissionError):
            pass
        finally:
            SB_PID_FILE.unlink(missing_ok=True)
    # 保险措施：按名字查找并杀死残留进程
    subprocess.run("pkill -9 -f 'sing-box run'", shell=True, capture_output=True)


def is_service_running():
    """通过检查 PID 文件和进程是否存在，来判断核心服务是否在运行。"""
    if not SB_PID_FILE.exists():
        return False
    try:
        sb_pid = int(SB_PID_FILE.read_text().strip())
        # os.kill(pid, 0) 不会杀死进程，而是检查进程是否存在
        os.kill(sb_pid, 0)
        return True
    except (ValueError, ProcessLookupError, FileNotFoundError, PermissionError):
        return False


# --- 核心逻辑 ---

def generate_all_configs(uuid_str, port, reality_keys, sni, external_ip):
    """生成所有节点链接和配置文件，并返回用于 UI 显示的文本。"""
    hostname = socket.gethostname()[:10]

    # 优选 IP 段（不同端口的 Cloudflare CDN 节点）
    # 客户端可以挑延迟最低的用
    cf_endpoints = {
        "104.16.0.0": 443,
        "104.17.0.0": 8443,
        "104.18.0.0": 2053,
        "104.19.0.0": 2083,
        "104.20.0.0": 2087,
        "162.159.0.0": 443,
        "172.64.0.0": 443,
        "188.114.96.0": 443,
    }

    base_config = {
        "uuid": uuid_str,
        "public_key": reality_keys["public_key"],
        "short_id": reality_keys["short_id"],
        "sni": sni,
    }

    all_links = []
    for ip, p in cf_endpoints.items():
        all_links.append(generate_vless_reality_link({
            **base_config,
            "ps": f"VL-REALITY-{hostname}-{ip.split('.')[2]}-{p}",
            "add": ip,
            "port": p,
        }))

    # 真实直连节点（用容器外网 IP 和默认端口）
    if external_ip:
        all_links.append(generate_vless_reality_link({
            **base_config,
            "ps": f"VL-REALITY-Direct-{hostname}",
            "add": external_ip,
            "port": port,
        }))

    # 写入纯链接文件
    ALL_NODES_FILE.write_text("\n".join(all_links) + "\n")

    # 准备 UI 显示文本
    list_output_text = f"""✅ **VLESS + Reality 服务已启动**
---
- **容器外网 IP:** `{external_ip or '未能获取'}`
- **UUID:** `{uuid_str}`
- **sing-box 监听端口:** `{port}`
- **Reality SNI (借用站):** `{sni}`
- **公钥 (pbk):** `{reality_keys['public_key']}`
- **短 ID (sid):** `{reality_keys['short_id']}`
- **流控 (flow):** `xtls-rprx-vision`
---
**使用提示**：
- 推荐优先使用 "Direct" 节点（延迟最低）
- 如果 Direct 不通，依次尝试优选 IP 节点
- 客户端需开启 TLS 指纹模拟（`fp=chrome`），大多数现代客户端默认支持
- ⚠️ Streamlit Cloud 容器重启后 IP 可能变化，请重新打开本页面刷新
---
**VLESS Reality 链接 (可复制):**

""" + "\n".join(all_links)

    LIST_FILE.write_text(list_output_text)
    return list_output_text


def start_services(uuid_str, port, sni, silent=False):
    """核心函数：安装并启动 sing-box (VLESS + Reality)，可选择静默模式。"""

    if not silent:
        st.info("🔄 正在启动/重启服务...")

    stop_services()

    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        uuid_str = uuid_str or str(uuid.uuid4())
        port = int(port) if port else DEFAULT_REALITY_PORT
        if port < 1 or port > 65535:
            port = DEFAULT_REALITY_PORT
        sni = sni or DEFAULT_REALITY_SNI

        # 定义依赖路径
        arch = "amd64" if "x86_64" in platform.machine().lower() else "arm64"
        singbox_path = INSTALL_DIR / "sing-box"

        def install_dependencies():
            if not singbox_path.exists():
                # 使用 sing-box 1.8+ 稳定版（Reality 协议 1.8 起 GA）
                sb_version = "1.10.2"
                sb_name_actual = f"sing-box-{sb_version}-linux-{arch}"
                tar_path = INSTALL_DIR / "sing-box.tar.gz"
                url = f"https://github.com/SagerNet/sing-box/releases/download/v{sb_version}/{sb_name_actual}.tar.gz"
                if not download_file(url, tar_path, silent):
                    return False, f"sing-box 下载失败: {url}"
                try:
                    with tarfile.open(tar_path, "r:gz") as tar:
                        tar.extractall(path=INSTALL_DIR)
                    shutil.move(INSTALL_DIR / sb_name_actual / "sing-box", singbox_path)
                    shutil.rmtree(INSTALL_DIR / sb_name_actual)
                    tar_path.unlink()
                    os.chmod(singbox_path, 0o755)
                except Exception as e:
                    return False, f"sing-box 解压失败: {e}"
            return True, ""

        if silent:
            success, msg = install_dependencies()
            if not success:
                return False, msg
        else:
            with st.spinner("正在检查并安装依赖 (sing-box)..."):
                success, msg = install_dependencies()
                if not success:
                    return False, msg

        # 加载或生成 Reality 密钥
        if not silent:
            with st.spinner("正在加载 Reality 密钥..."):
                reality_keys = load_or_generate_reality_keys(singbox_path)
        else:
            reality_keys = load_or_generate_reality_keys(singbox_path)

        # 生成 sing-box 配置（VLESS + Reality）
        sb_config = {
            "log": {"level": "info", "output": "sb.log"},
            "inbounds": [
                {
                    "type": "vless",
                    "tag": "vless-in",
                    "listen": "0.0.0.0",
                    "listen_port": port,
                    "users": [
                        {
                            "uuid": uuid_str,
                            "flow": "xtls-rprx-vision",
                        }
                    ],
                    "tls": {
                        "enabled": true,
                        "server_name": sni,
                        "reality": {
                            "enabled": true,
                            "private_key": reality_keys["private_key"],
                            "short_id": [reality_keys["short_id"]],
                        },
                    },
                }
            ],
            "outbounds": [
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"},
            ],
        }
        (INSTALL_DIR / "sb.json").write_text(json.dumps(sb_config, indent=2))

        # 尝试启动 sing-box，带端口降级
        # Streamlit Cloud 容器通常以非 root 运行，bind 443 可能失败
        candidate_ports = [port, 2053, 2083, 2087, 8443, 2096]
        # 去重并保持顺序
        seen = set()
        candidate_ports = [p for p in candidate_ports if not (p in seen or seen.add(p))]

        sb_process = None
        actual_port = None
        for try_port in candidate_ports:
            sb_config["inbounds"][0]["listen_port"] = try_port
            (INSTALL_DIR / "sb.json").write_text(json.dumps(sb_config, indent=2))
            with open(SB_LOG_FILE, "w") as sb_log:
                proc = subprocess.Popen(
                    [str(singbox_path), "run", "-c", "sb.json"],
                    cwd=INSTALL_DIR,
                    stdout=sb_log,
                    stderr=subprocess.STDOUT,
                )
            time.sleep(1.5)
            # 检查进程是否还活着（bind 失败会立即退出，pid 文件会被 unlinked）
            if proc.poll() is None:
                sb_process = proc
                actual_port = try_port
                break
            else:
                # 进程已退出，读取日志看原因
                if not silent:
                    err_log = SB_LOG_FILE.read_text() if SB_LOG_FILE.exists() else ""
                    st.warning(f"端口 {try_port} 启动失败，尝试下一个... 错误: {err_log[:200]}")

        if sb_process is None:
            return False, f"所有候选端口均启动失败，请检查日志: {SB_LOG_FILE}"

        port = actual_port
        SB_PID_FILE.write_text(str(sb_process.pid))

        # 获取外网 IP
        if not silent:
            with st.spinner("正在获取容器外网 IP..."):
                external_ip = get_external_ip()
        else:
            external_ip = get_external_ip()

        # 写 UUID / 端口 / SNI 到 secrets 文件，方便后续读取
        meta = {
            "uuid": uuid_str,
            "port": port,
            "sni": sni,
            "external_ip": external_ip,
        }
        (INSTALL_DIR / "meta.json").write_text(json.dumps(meta, indent=2))

        links_output = generate_all_configs(uuid_str, port, reality_keys, sni, external_ip)
        return True, links_output

    except Exception as e:
        return False, f"处理过程中发生意外错误: {e}"


def uninstall_services():
    """卸载服务，清理所有运行时文件和进程。"""
    stop_services()
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    st.success("✅ 卸载完成。所有运行时文件和进程已清除。")
    st.session_state.clear()


# --- UI 渲染函数 ---

def render_main_ui(config):
    """渲染主控制面板。"""
    st.set_page_config(page_title="部署工具", layout="wide")
    st.header("⚙️ 服务管理面板 (VLESS + Reality)")

    st.subheader("控制操作")
    c1, c2, c3 = st.columns(3)

    if c1.button("🚀 强制重启服务", type="primary", use_container_width=True):
        success, message = start_services(
            config["uuid_str"],
            config["port"],
            config["reality_sni"],
            silent=False,
        )
        if success:
            st.session_state.output = message
        else:
            st.error(f"操作失败: {message}")
            st.session_state.output = message
        st.rerun()

    if c2.button("❌ 永久卸载服务", use_container_width=True):
        with st.spinner("正在执行卸载..."):
            uninstall_services()
        st.rerun()

    if c3.button("📄 显示/刷新节点信息", use_container_width=True):
        if LIST_FILE.exists():
            st.session_state.output = LIST_FILE.read_text()
        else:
            st.session_state.output = "节点信息文件不存在，请先启动服务。"
        st.rerun()

    # 优先从会话状态读取输出，否则从文件读取
    output_to_show = st.session_state.get('output', '')
    if not output_to_show and LIST_FILE.exists():
        output_to_show = LIST_FILE.read_text()

    if output_to_show:
        st.subheader("节点信息")
        st.code(output_to_show)


def render_login_ui(secret_key):
    """渲染伪装的天气查询登录界面。"""
    st.set_page_config(page_title="天气查询", layout="centered")
    st.title("🌦️ 实时天气查询")
    city = st.text_input("请输入城市名或秘密口令：", "")
    if st.button("查询天气"):
        if city == secret_key:
            st.session_state.authenticated = True
            st.rerun()
        else:
            with st.spinner(f"正在查询 {city} 的天气..."):
                time.sleep(1)
                st.error("查询失败，请检查城市名是否正确。")


def main():
    """主应用逻辑：先执行后台自愈，再根据登录状态渲染 UI。"""
    st.session_state.setdefault('authenticated', False)
    st.session_state.setdefault('output', "")

    try:
        secret_key = st.secrets["SECRET_KEY"]
        config = {
            "uuid_str": st.secrets.get("UUID_STR", ""),
            "port": int(st.secrets.get("PORT_VM_WS", DEFAULT_REALITY_PORT)),
            "reality_sni": st.secrets.get("REALITY_SNI", DEFAULT_REALITY_SNI),
        }
    except KeyError:
        st.error("严重错误：未在 Secrets 中找到 'SECRET_KEY'。")
        st.info("请确保您已在 Streamlit Cloud 的设置中添加了名为 'SECRET_KEY' 的密钥。")
        return

    # --- 核心自愈逻辑 ---
    # 在渲染任何 UI 之前，先检查服务状态。如果服务未运行，就以静默模式启动。
    if not is_service_running():
        start_services(
            config["uuid_str"],
            config["port"],
            config["reality_sni"],
            silent=True,
        )

    # --- UI 渲染逻辑 ---
    if st.session_state.authenticated:
        render_main_ui(config)
    else:
        render_login_ui(secret_key)


if __name__ == "__main__":
    main()
