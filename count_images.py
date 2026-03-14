#!/usr/bin/env python3
"""
图片数量统计脚本（只统计，不下载）

遍历指定 Wiki 空间的所有文档，统计图片块数量和预估存储大小。

用法：
  python count_images.py --config config.yaml
"""
import argparse
import sys
from pathlib import Path

import yaml
from tqdm import tqdm

from api import FeishuAPI
from auth import get_user_token
from converter import IMAGE

IMAGE_AVG_SIZE_KB = 500  # 预估单张图片平均大小（KB）


def _build_flat_nodes(api: FeishuAPI, space_id: str) -> list[dict]:
    """递归获取 Wiki 所有节点，返回展平列表。"""
    def fetch(parent_token=None):
        nodes = api.list_wiki_nodes(space_id, parent_token)
        result = []
        for node in nodes:
            result.append(node)
            result.extend(fetch(node["node_token"]))
        return result
    return fetch()


def main():
    parser = argparse.ArgumentParser(description="统计飞书 Wiki 图片数量（不下载）")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"找不到配置文件: {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    token_data = get_user_token(cfg["app_id"], cfg["app_secret"])
    api = FeishuAPI(token_data["access_token"], cfg.get("rate_limit_per_minute", 60))

    grand_total_docs = 0
    grand_total_images = 0
    grand_docs_with_images = 0

    for space_cfg in cfg.get("wiki_spaces", []):
        space_id = space_cfg["space_id"]
        name = space_cfg.get("name", space_id)

        print(f"\n📚 知识库：{name}（{space_id}）")
        print("  获取节点树...")
        nodes = _build_flat_nodes(api, space_id)
        doc_nodes = [n for n in nodes if n.get("obj_type") == "docx"]
        print(f"  共 {len(doc_nodes)} 篇文档，开始统计图片...\n")

        space_images = 0
        space_docs_with_images = 0
        top_docs = []  # (image_count, title)

        with tqdm(total=len(doc_nodes), unit="篇") as bar:
            for node in doc_nodes:
                doc_id = node.get("obj_token", "")
                title = node.get("title", "未命名")
                bar.set_postfix_str(title[:20])
                try:
                    blocks = api.get_doc_blocks(doc_id)
                    count = sum(1 for b in blocks if b.get("block_type") == IMAGE)
                    space_images += count
                    if count > 0:
                        space_docs_with_images += 1
                        top_docs.append((count, title))
                except Exception as e:
                    tqdm.write(f"  ⚠️  跳过 [{title}]: {e}")
                bar.update(1)

        top_docs.sort(reverse=True)
        est_mb = space_images * IMAGE_AVG_SIZE_KB / 1024

        print(f"\n  ── {name} 统计结果 ──")
        print(f"  文档总数:        {len(doc_nodes)} 篇")
        print(f"  含图片文档数:    {space_docs_with_images} 篇")
        print(f"  图片总数:        {space_images} 张")
        print(f"  预估存储大小:    {est_mb:.0f} MB（按均 {IMAGE_AVG_SIZE_KB}KB/张估算）")
        print(f"\n  图片最多的前 10 篇：")
        for count, title in top_docs[:10]:
            print(f"    {count:4d} 张  {title}")

        grand_total_docs += len(doc_nodes)
        grand_total_images += space_images
        grand_docs_with_images += space_docs_with_images

    if len(cfg.get("wiki_spaces", [])) > 1:
        est_mb = grand_total_images * IMAGE_AVG_SIZE_KB / 1024
        print(f"\n{'='*45}")
        print(f"所有知识库合计")
        print(f"  文档总数:     {grand_total_docs} 篇")
        print(f"  含图片文档:   {grand_docs_with_images} 篇")
        print(f"  图片总数:     {grand_total_images} 张")
        print(f"  预估存储:     {est_mb:.0f} MB")


if __name__ == "__main__":
    main()
