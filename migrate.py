#!/usr/bin/env python3
"""
飞书 Wiki → Obsidian 批量迁移主脚本

用法：
  python migrate.py --config config.yaml                    # 全量迁移
  python migrate.py --config config.yaml --incremental      # 增量同步
  python migrate.py --config config.yaml --limit 10         # 小批量测试
  python migrate.py --config config.yaml --doc-id <id>      # 单篇文档
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from tqdm import tqdm

from api import FeishuAPI
from auth import get_user_token
from converter import convert_blocks

# ── 日志 ───────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate")


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    """将文档标题转换为合法的文件/目录名（保留中文、字母、数字、空格、连字符、点）。"""
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "未命名文档"


def _ts_to_iso(ts: int | str | None) -> str:
    """Unix 时间戳（秒）→ ISO-8601 字符串，带 +08:00 时区。"""
    if not ts:
        return ""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(
            timezone(datetime.now().astimezone().utcoffset())
        )
        return dt.isoformat(timespec="seconds")
    except Exception:
        return str(ts)


def _detect_ext(data: bytes) -> str:
    """根据文件头魔数判断图片扩展名。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"  # 默认


# ── 同步状态 ───────────────────────────────────────────────────────────────────

class SyncState:
    def __init__(self, path: Path):
        self._path = path
        self._data: dict = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def is_up_to_date(self, doc_id: str, edit_time: int) -> bool:
        entry = self._data.get(doc_id)
        return bool(entry and entry.get("edit_time", 0) >= edit_time)

    def update(self, doc_id: str, edit_time: int, local_path: str):
        self._data[doc_id] = {"edit_time": edit_time, "local_path": local_path}

    def save(self):
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── Wiki 节点树 ────────────────────────────────────────────────────────────────

def _build_tree(api: FeishuAPI, space_id: str) -> list[dict]:
    """
    递归获取 Wiki 空间完整节点树。
    每个节点附加 _children 字段（子节点列表）。
    """
    def fetch(parent_token=None):
        nodes = api.list_wiki_nodes(space_id, parent_token)
        for node in nodes:
            node["_children"] = fetch(node["node_token"])
        return nodes

    return fetch()


def _collect_all_nodes(tree: list[dict]) -> list[dict]:
    """将树形节点展平为列表（先序遍历）。"""
    result = []
    for node in tree:
        result.append(node)
        result.extend(_collect_all_nodes(node["_children"]))
    return result


def _build_doc_map(nodes: list[dict]) -> dict:
    """obj_token → title，供内链转换用。"""
    return {n["obj_token"]: n["title"] for n in nodes if n.get("obj_token")}


# ── 文档处理 ───────────────────────────────────────────────────────────────────

def _resolve_att_dir(cfg: dict, local_dir: Path, output_root: Path) -> Path:
    """
    根据 attachments_location 配置项，返回附件存放目录。

    对应 Obsidian「Files and links → Default location for new attachments」的四种模式：
      vault_folder      → <output_root>/<assets_dir>/
      same_folder       → <local_dir>/
      subfolder         → <local_dir>/<assets_dir>/          （默认）
      specified_folder  → <output_root>/<assets_dir>/
    """
    mode = cfg.get("attachments_location", "subfolder")
    assets = cfg.get("assets_dir", "assets")

    if mode == "vault_folder":
        return output_root / assets
    elif mode == "same_folder":
        return local_dir
    elif mode == "specified_folder":
        return output_root / assets
    else:  # subfolder（默认）
        return local_dir / assets


def _write_doc(
    api: FeishuAPI,
    node: dict,
    local_dir: Path,
    doc_map: dict,
    cfg: dict,
    state: SyncState,
    errors: list,
    output_root: Path | None = None,
) -> bool:
    """
    下载单篇文档并写入本地。
    返回 True 表示成功（含跳过），False 表示失败。
    """
    doc_id = node.get("obj_token", "")
    title = node.get("title") or "未命名文档"
    edit_time = int(node.get("obj_edit_time", 0) or 0)
    safe_title = _safe_filename(title)
    md_path = local_dir / f"{safe_title}.md"
    rel_path = str(md_path)

    try:
        blocks = api.get_doc_blocks(doc_id)
        md_text, images = convert_blocks(blocks, doc_map=doc_map)

        # 下载图片
        if images:
            att_dir = _resolve_att_dir(cfg, local_dir, output_root or local_dir)
            att_dir.mkdir(parents=True, exist_ok=True)
            new_images = []
            for token, _ in images:
                try:
                    data = api.download_media(token)
                    ext = _detect_ext(data)
                    filename = f"{token}{ext}"
                    (att_dir / filename).write_bytes(data)
                    new_images.append((token, filename))
                except Exception as e:
                    log.warning("图片下载失败 token=%s: %s", token, e)
                    new_images.append((token, f"{token}.png"))  # 保留原占位
            # 修正 md 中的图片文件名（扩展名可能变化）
            for old_token, new_filename in new_images:
                md_text = md_text.replace(f"![[{old_token}.png]]", f"![[{new_filename}]]")

        # 构建 frontmatter
        fm_cfg = cfg.get("frontmatter", {})
        fm_lines = ["---"]
        fm_lines.append(f'title: "{title}"')
        if fm_cfg.get("include_created_time", True):
            created = node.get("obj_create_time") or node.get("create_time", "")
            fm_lines.append(f'created: "{_ts_to_iso(created)}"')
        if fm_cfg.get("include_modified_time", True):
            fm_lines.append(f'modified: "{_ts_to_iso(edit_time)}"')
        if fm_cfg.get("include_feishu_url", True):
            space_id = node.get("space_id", "")
            token = node.get("node_token", "")
            feishu_url = f"https://feishu.cn/wiki/{token}"
            fm_lines.append(f'feishu_url: "{feishu_url}"')
        fm_lines.append("tags: []")
        fm_lines.append("---")
        frontmatter = "\n".join(fm_lines)

        content = f"{frontmatter}\n\n# {title}\n\n{md_text}\n"
        md_path.write_text(content, encoding="utf-8")
        state.update(doc_id, edit_time, rel_path)
        return True

    except Exception as e:
        log.error("文档转换失败 [%s] %s: %s", doc_id, title, e)
        errors.append({"doc_id": doc_id, "title": title, "error": str(e)})
        return False


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def _migrate_wiki(
    api: FeishuAPI,
    space_cfg: dict,
    output_root: Path,
    cfg: dict,
    state: SyncState,
    incremental: bool,
    limit: int | None,
    errors: list,
) -> tuple[int, int]:
    """迁移单个 Wiki 空间，返回 (成功数, 跳过数)。"""
    space_id = space_cfg["space_id"]
    folder_name = space_cfg.get("name") or space_id
    wiki_root = output_root / _safe_filename(folder_name)

    log.info("获取 Wiki 节点树 space_id=%s ...", space_id)
    tree = _build_tree(api, space_id)
    all_nodes = _collect_all_nodes(tree)
    doc_nodes = [n for n in all_nodes if n.get("obj_type") == "docx"]

    log.info("共发现 %d 篇文档", len(doc_nodes))

    doc_map = _build_doc_map(all_nodes)

    if limit:
        doc_nodes = doc_nodes[:limit]

    success = skip = 0
    with tqdm(total=len(doc_nodes), unit="篇", desc=folder_name) as bar:
        for node in doc_nodes:
            doc_id = node.get("obj_token", "")
            title = node.get("title", "未命名")
            edit_time = int(node.get("obj_edit_time", 0) or 0)
            bar.set_postfix_str(title[:20])

            if incremental and state.is_up_to_date(doc_id, edit_time):
                skip += 1
                bar.update(1)
                continue

            # 按节点路径还原目录层级
            local_dir = _node_local_dir(node, all_nodes, wiki_root)
            local_dir.mkdir(parents=True, exist_ok=True)

            ok = _write_doc(api, node, local_dir, doc_map, cfg, state, errors, output_root=output_root)
            if ok:
                success += 1
            bar.update(1)

    return success, skip


def _node_local_dir(node: dict, all_nodes: list[dict], wiki_root: Path) -> Path:
    """根据节点的父链，计算其对应的本地目录路径。"""
    node_map = {n["node_token"]: n for n in all_nodes}

    ancestors = []
    current = node
    while True:
        parent_token = current.get("parent_node_token", "")
        if not parent_token or parent_token not in node_map:
            break
        parent = node_map[parent_token]
        ancestors.insert(0, parent)
        current = parent

    path = wiki_root
    for anc in ancestors:
        path = path / _safe_filename(anc.get("title") or anc["node_token"])
    return path


def _migrate_single_doc(
    api: FeishuAPI,
    doc_id: str,
    output_root: Path,
    cfg: dict,
    state: SyncState,
    errors: list,
):
    """迁移单篇文档（调试用）。"""
    node = {"obj_token": doc_id, "title": doc_id, "obj_edit_time": 0}
    output_root.mkdir(parents=True, exist_ok=True)
    ok = _write_doc(api, node, output_root, doc_map={}, cfg=cfg, state=state, errors=errors, output_root=output_root)
    if ok:
        log.info("单篇文档导出成功 → %s", output_root / f"{doc_id}.md")
    else:
        log.error("单篇文档导出失败，请查看上方错误日志")


# ── 入口 ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="飞书 Wiki → Obsidian 迁移工具")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--incremental", action="store_true", help="增量同步模式")
    parser.add_argument("--limit", type=int, default=None, help="每个 Wiki 最多导出 N 篇（测试用）")
    parser.add_argument("--doc-id", dest="doc_id", default=None, help="仅导出指定文档 ID（测试用）")
    args = parser.parse_args()

    # 读取配置
    config_path = Path(args.config)
    if not config_path.exists():
        log.error("找不到配置文件: %s", config_path)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    output_root = Path(os.path.expanduser(cfg.get("output_dir", "~/obsidian-vault")))
    output_root.mkdir(parents=True, exist_ok=True)

    sync_state_file = output_root / cfg.get("sync_state_file", ".feishu_sync_state.json")
    state = SyncState(sync_state_file)

    # 认证
    log.info("获取 user_access_token ...")
    token_data = get_user_token(cfg["app_id"], cfg["app_secret"])
    api = FeishuAPI(
        access_token=token_data["access_token"],
        rate_limit_per_minute=cfg.get("rate_limit_per_minute", 60),
    )

    errors: list[dict] = []
    total_success = total_skip = 0
    start_time = time.time()

    try:
        if args.doc_id:
            _migrate_single_doc(api, args.doc_id, output_root, cfg, state, errors)
        else:
            wiki_spaces = cfg.get("wiki_spaces", [])
            if not wiki_spaces:
                log.error("config.yaml 中未配置 wiki_spaces")
                sys.exit(1)
            for space_cfg in wiki_spaces:
                s, sk = _migrate_wiki(
                    api, space_cfg, output_root, cfg, state,
                    incremental=args.incremental,
                    limit=args.limit,
                    errors=errors,
                )
                total_success += s
                total_skip += sk
    finally:
        state.save()

    elapsed = time.time() - start_time
    log.info("=" * 50)
    log.info("迁移完成！耗时 %.1f 秒", elapsed)
    log.info("成功: %d  跳过: %d  失败: %d", total_success, total_skip, len(errors))

    if errors:
        report_path = output_root / "migrate_errors.json"
        report_path.write_text(
            json.dumps(errors, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.warning("失败文档列表已写入: %s", report_path)


if __name__ == "__main__":
    main()
