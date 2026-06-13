#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
INSTALL_DIR = Path.home() / ".agsb"
SB_PID_FILE = INSTALL_DIR / "sbpid.log"
ARGO_PID_FILE = INSTALL_DIR / "sbargopid.log"
LIST_FILE = INSTALL_DIR / "list.txt"
LOG_FILE = INSTALL_DIR / "argo.log"
SB_LOG_FILE = INSTALL_DIR / "sb.log"
ALL_NODES_FILE = INSTALL_DIR / "allnodes.txt"

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


def get_tunnel_domain(silent=False):
    """从 cloudflared 日志中读取临时隧道域名。"""
    for _ in range(20):  # 最多等 40 秒
        if LOG_FILE.exists():
            try:
                content = LOG_FILE.read_text()
                match = re.search(r'https://([a-zA-Z0-9.-]+\.trycloudflare\.com)', content)
                if match:
                    return match.group(1)
            except Exception:
                pass
        time.sleep(2)
    if not silent:
        st.warning("未能从日志中获取隧道域名，请检查 .agsb/argo.log")
    return None


def stop_services():
    """停止所有由本脚本启动的后台服务进程。"""
    for pid_file in [SB_PID_FILE, ARGO_PID_FILE]:
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 9)
            except (ValueError, ProcessLookupError, FileNotFoundError, PermissionError):
                pass
            finally:
                pid_file.unlink(missing_ok=True)
    subprocess.run("pkill -9 -f 'sing-box run'", shell=True, capture_output=True)
    subprocess.run("pkill -9 -f 'cloudflared tunnel'", shell=True, capture_output=True)


def is_service_running():
    """检查核心服务是否在运行。"""
    if not SB_PID_FILE.exists() or not ARGO_PID_FILE.exists():
        return False
    try:
        sb_pid = int(SB_PID_FILE.read_text().strip())
        argo_pid = int(ARGO_PID_FILE.read_text().strip())
        os.kill(sb_pid, 0)
        os.kill(argo_pid, 0)
        return True
    except (ValueError, ProcessLookupError, FileNotFoundError, PermissionError):
        return False


# --- 核心逻辑 ---

def generate_all_configs(domain, uuid_str, port, is_named_tunnel=False):
    """生成所有节点链接。"""
    hostname = socket.gethostname()[:10]
    all_links = []

    if is_named_tunnel:
        # 命名隧道：域名固定，只生成直连 + 优选 IP 节点
        # Cloudflare 优选 IP（走 CDN）
        cf_endpoints = {
            "104.16.0.0": "443",
            "104.17.0.0": "8443",
            "104.18.0.0": "2053",
            "104.19.0.0": "2083",
            "104.20.0.0": "2087",
            "162.159.0.0": "443",
            "172.64.0.0": "443",
            "188.114.96.0": "443",
        }
        for ip, p in cf_endpoints.items():
            all_links.append(generate_vless_link({
                "ps": f"VL-WS-{hostname}-{ip.split('.')[2]}-{p}",
                "add": ip,
                "port": p,
                "id": uuid_str,
                "host": domain,
                "sni": domain,
            }))

    # 直连节点（直接用隧道域名）
    all_links.append(generate_vless_link({
        "ps": f"VL-WS-Direct-{hostname}",
        "add": domain,
        "port": "443",
        "id": uuid_str,
        "host": domain,
        "sni": domain,
    }))

    ALL_NODES_FILE.write_text("\n".join(all_links) + "\n")

    tunnel_type = "命名隧道 (Named)" if is_named_tunnel else "临时隧道 (Quick)"
    list_output_text = f"""✅ **VLESS + WebSocket + TLS 服务已启动**
---
- **隧道类型:** `{tunnel_type}`
- **隧道域名:** `{domain}`
- **UUID:** `{uuid_str}`
- **本地端口:** `{port}`
- **WebSocket 路径:** `/`
- **TLS:** Cloudflare 提供（无需证书）
---
**使用提示**：
- 推荐优先使用 "Direct" 节点（走 Cloudflare 隧道直连）
{"- 命名隧道无带宽限制，可正常观看 YouTube" if is_named_tunnel else "- ⚠️ 临时隧道有带宽限制，YouTube 未登录可能无法播放"}
---
**VLESS 链接 (可复制):**

""" + "\n".join(all_links)

    LIST_FILE.write_text(list_output_text)
    return list_output_text


def generate_vless_link(config):
    """生成 VLESS + WebSocket + TLS 链接字符串。"""
    # 格式: vless://UUID@HOST:PORT?params#NAME
    params = {
        "type": "ws",
        "security": "tls",
        "path": "/",
        "host": config.get("host"),
        "sni": config.get("sni"),
    }
    param_str = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items()
    )
    name = urllib.parse.quote(config["ps"], safe='')
    return f"vless://{config['id']}@{config['add']}:{config['port']}?{param_str}#{name}"


def start_services(uuid_str, port, argo_token="", custom_domain="", silent=False):
    """核心函数：安装并启动 sing-box + cloudflared。"""
    if not silent:
        st.info("🔄 正在启动/重启服务...")

    stop_services()

    is_named_tunnel = bool(argo_token and custom_domain)

    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        uuid_str = uuid_str or str(uuid.uuid4())
        port = int(port) if port else 10000
        if port < 1 or port > 65535:
            port = 10000

        arch = "amd64" if "x86_64" in platform.machine().lower() else "arm64"
        singbox_path = INSTALL_DIR / "sing-box"
        cloudflared_path = INSTALL_DIR / "cloudflared"

        def install_dependencies():
            if not singbox_path.exists():
                sb_version = "1.10.2"
                sb_name = f"sing-box-{sb_version}-linux-{arch}"
                tar_path = INSTALL_DIR / "sing-box.tar.gz"
                url = f"https://github.com/SagerNet/sing-box/releases/download/v{sb_version}/{sb_name}.tar.gz"
                if not download_file(url, tar_path, silent):
                    return False, f"sing-box 下载失败: {url}"
                try:
                    with tarfile.open(tar_path, "r:gz") as tar:
                        tar.extractall(path=INSTALL_DIR)
                    shutil.move(INSTALL_DIR / sb_name / "sing-box", singbox_path)
                    shutil.rmtree(INSTALL_DIR / sb_name)
                    tar_path.unlink()
                    os.chmod(singbox_path, 0o755)
                except Exception as e:
                    return False, f"sing-box 解压失败: {e}"

            if not cloudflared_path.exists():
                cf_arch = "amd64" if arch == "amd64" else "arm"
                url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{cf_arch}"
                if not download_file(url, cloudflared_path, silent):
                    return False, f"cloudflared 下载失败: {url}"
                os.chmod(cloudflared_path, 0o755)
            return True, ""

        if silent:
            success, msg = install_dependencies()
            if not success:
                return False, msg
        else:
            with st.spinner("正在检查并安装依赖 (sing-box, cloudflared)..."):
                success, msg = install_dependencies()
                if not success:
                    return False, msg

        # 创建 sing-box 配置 (VLESS + WebSocket)
        sb_config = {
            "log": {"level": "info"},
            "inbounds": [
                {
                    "type": "vless",
                    "tag": "vless-in",
                    "listen": "127.0.0.1",
                    "listen_port": port,
                    "users": [
                        {
                            "uuid": uuid_str,
                        }
                    ],
                    "transport": {
                        "type": "ws",
                        "path": "/",
                    },
                }
            ],
            "outbounds": [
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"},
            ],
        }
        (INSTALL_DIR / "sb.json").write_text(json.dumps(sb_config, indent=2))

        # 启动 sing-box
        with open(SB_LOG_FILE, "w") as sb_log:
            sb_proc = subprocess.Popen(
                [str(singbox_path), "run", "-c", "sb.json"],
                cwd=INSTALL_DIR,
                stdout=sb_log,
                stderr=subprocess.STDOUT,
            )
        SB_PID_FILE.write_text(str(sb_proc.pid))

        # 启动 cloudflared
        with open(LOG_FILE, "w") as cf_log:
            if is_named_tunnel:
                # 命名隧道：使用 Token 连接
                cf_cmd = [
                    str(cloudflared_path), "tunnel", "--no-autoupdate",
                    "run", "--token", argo_token,
                ]
            else:
                # 临时隧道
                cf_cmd = [
                    str(cloudflared_path), "tunnel", "--no-autoupdate",
                    "--url", f"http://127.0.0.1:{port}",
                    "--protocol", "http2",
                ]
            cf_proc = subprocess.Popen(
                cf_cmd, cwd=INSTALL_DIR,
                stdout=cf_log, stderr=subprocess.STDOUT,
            )
        ARGO_PID_FILE.write_text(str(cf_proc.pid))

        # 获取隧道域名
        if is_named_tunnel:
            domain = custom_domain
        else:
            if not silent:
                with st.spinner("正在等待 cloudflared 隧道建立..."):
                    domain = get_tunnel_domain(silent=True)
            else:
                domain = get_tunnel_domain(silent=True)

        if not domain:
            return False, "未能获取隧道域名。请检查日志 (.agsb/argo.log)。"

        links_output = generate_all_configs(domain, uuid_str, port, is_named_tunnel)
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
    st.header("⚙️ 服务管理面板 (VLESS + WS + TLS)")

    st.subheader("控制操作")
    c1, c2, c3 = st.columns(3)

    if c1.button("🚀 强制重启服务", type="primary", use_container_width=True):
        success, message = start_services(
            config["uuid_str"], config["port"],
            config["argo_token"], config["custom_domain"],
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
    """主应用逻辑。"""
    st.session_state.setdefault('authenticated', False)
    st.session_state.setdefault('output', "")

    try:
        secret_key = st.secrets["SECRET_KEY"]
        config = {
            "uuid_str": st.secrets.get("UUID_STR", ""),
            "port": int(st.secrets.get("PORT_VM_WS", 10000)),
            "argo_token": st.secrets.get("ARGO_TOKEN", ""),
            "custom_domain": st.secrets.get("CUSTOM_DOMAIN", ""),
        }
    except KeyError:
        st.error("严重错误：未在 Secrets 中找到 'SECRET_KEY'。")
        st.info("请确保您已在 Streamlit Cloud 的设置中添加了名为 'SECRET_KEY' 的密钥。")
        return

    # 核心自愈逻辑
    if not is_service_running():
        start_services(
            config["uuid_str"], config["port"],
            config["argo_token"], config["custom_domain"],
            silent=True,
        )

    # UI 渲染
    if st.session_state.authenticated:
        render_main_ui(config)
    else:
        render_login_ui(secret_key)


if __name__ == "__main__":
    main()
