"""
飞书 Open API 封装
- 统一 Authorization header
- 速率限制（令牌桶，默认 60 req/min）
- 自动分页
"""
import time

import requests

BASE_URL = "https://open.feishu.cn/open-apis"


class FeishuAPI:
    def __init__(self, access_token: str, rate_limit_per_minute: int = 60):
        self._token = access_token
        self._interval = 60.0 / max(rate_limit_per_minute, 1)
        self._last_call = 0.0

    # ── 公开接口 ───────────────────────────────────────────────────────────────

    def list_spaces(self) -> list[dict]:
        """列出用户所有知识库。"""
        return list(self._paginate("/wiki/v2/spaces", "items"))

    def list_wiki_nodes(self, space_id: str, parent_node_token: str | None = None) -> list[dict]:
        """列出某知识库某父节点下的直接子节点（不含孙节点）。"""
        params = {}
        if parent_node_token:
            params["parent_node_token"] = parent_node_token
        return list(self._paginate(f"/wiki/v2/spaces/{space_id}/nodes", "items",
                                   extra_params=params, page_size=50))

    def get_doc_blocks(self, document_id: str) -> list[dict]:
        """获取文档全部块（flat list）。"""
        return list(self._paginate(f"/docx/v1/documents/{document_id}/blocks", "items"))

    def download_media(self, file_token: str) -> bytes:
        """下载图片/附件原始字节。"""
        return self._download(f"/drive/v1/medias/{file_token}/download")

    # ── 内部方法 ───────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_call = time.time()

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._throttle()
        resp = requests.get(
            f"{BASE_URL}{path}",
            headers=self._headers(),
            params=params or {},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(
                f"HTTP {resp.status_code} path={path}\n响应体: {resp.text[:500]}"
            )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"API 错误 code={data.get('code')}: {data.get('msg')}  path={path}"
            )
        return data

    def _download(self, path: str) -> bytes:
        self._throttle()
        resp = requests.get(
            f"{BASE_URL}{path}",
            headers=self._headers(),
            timeout=60,
            stream=True,
        )
        resp.raise_for_status()
        return resp.content

    def _paginate(self, path: str, items_key: str, extra_params: dict | None = None, page_size: int = 500):
        """自动翻页的生成器，yield 各页 items。"""
        page_token = None
        while True:
            params = {"page_size": page_size, **(extra_params or {})}
            if page_token:
                params["page_token"] = page_token
            data = self._get(path, params)
            payload = data.get("data", {})
            yield from payload.get(items_key, [])
            if not payload.get("has_more"):
                break
            page_token = payload.get("page_token")
