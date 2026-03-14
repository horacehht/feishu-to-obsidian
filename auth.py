"""
飞书 OAuth 2.0 授权流程 + token 缓存管理

个人知识库必须使用 user_access_token，不能用 tenant_access_token。
首次运行会打开浏览器完成授权，token 缓存到本地，过期自动刷新。
"""
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests

BASE_URL = "https://open.feishu.cn/open-apis"
REDIRECT_URI = "http://localhost:9898/callback"
SCOPES = "wiki:wiki:readonly docx:document:readonly drive:drive:readonly"
TOKEN_CACHE_FILE = ".token_cache.json"


class _CallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None
    expected_state: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/callback":
            params = parse_qs(parsed.query)
            returned_state = params.get("state", [None])[0]
            if returned_state != _CallbackHandler.expected_state:
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<h2 style='font-family:sans-serif;padding:2em'>授权失败：state 验证不通过，请重试。</h2>".encode()
                )
                return
            _CallbackHandler.auth_code = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2 style='font-family:sans-serif;padding:2em'>授权成功！可以关闭浏览器了。</h2>".encode()
            )

    def log_message(self, fmt, *args):  # suppress server log
        pass


def get_user_token(app_id: str, app_secret: str) -> dict:
    """返回有效的 user token dict，含 access_token 字段。"""
    cache = _load_cache()
    if cache and cache.get("expires_at", 0) > time.time() + 300:
        return cache
    if cache and cache.get("refresh_token"):
        refreshed = _refresh(app_id, app_secret, cache["refresh_token"])
        if refreshed:
            return refreshed
    return _oauth_flow(app_id, app_secret)


# ── 内部函数 ──────────────────────────────────────────────────────────────────

def _oauth_flow(app_id: str, app_secret: str) -> dict:
    state = secrets.token_hex(8)
    scope_encoded = SCOPES.replace(" ", "%20")
    auth_url = (
        f"https://open.feishu.cn/open-apis/authen/v1/authorize"
        f"?app_id={app_id}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope_encoded}"
        f"&state={state}"
    )

    _CallbackHandler.auth_code = None
    _CallbackHandler.expected_state = state
    server = HTTPServer(("localhost", 9898), _CallbackHandler)
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    print("\n🌐 正在打开浏览器进行飞书授权...")
    print(f"   若浏览器未自动打开，请手动访问：\n   {auth_url}\n")
    webbrowser.open(auth_url)

    deadline = time.time() + 120
    while _CallbackHandler.auth_code is None and time.time() < deadline:
        time.sleep(0.5)
    server.server_close()

    if not _CallbackHandler.auth_code:
        raise TimeoutError("授权超时（120s），请重试")

    app_token = _get_app_access_token(app_id, app_secret)
    resp = requests.post(
        f"{BASE_URL}/authen/v1/oidc/access_token",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {app_token}",
        },
        json={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.auth_code,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"换取 token 失败: {data.get('msg')} (code={data.get('code')})\n完整响应: {data}")

    return _save_cache(data["data"])


def _refresh(app_id: str, app_secret: str, refresh_token: str) -> dict | None:
    try:
        app_token = _get_app_access_token(app_id, app_secret)
        resp = requests.post(
            f"{BASE_URL}/authen/v1/oidc/refresh_access_token",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {app_token}",
            },
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return None
        return _save_cache(data["data"])
    except Exception:
        return None


def _get_app_access_token(app_id: str, app_secret: str) -> str:
    """用 app_id + app_secret 换取 app_access_token（有效期 2h，此处不缓存）。"""
    resp = requests.post(
        f"{BASE_URL}/auth/v3/app_access_token/internal",
        headers={"Content-Type": "application/json"},
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 app_access_token 失败: {data.get('msg')} (code={data.get('code')})")
    return data["app_access_token"]


def _save_cache(token_data: dict) -> dict:
    cache = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": time.time() + token_data.get("expires_in", 7200) - 60,
    }
    with open(TOKEN_CACHE_FILE, "w") as f:
        json.dump(cache, f)
    os.chmod(TOKEN_CACHE_FILE, 0o600)
    return cache


def _load_cache() -> dict | None:
    if not os.path.exists(TOKEN_CACHE_FILE):
        return None
    try:
        with open(TOKEN_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None
