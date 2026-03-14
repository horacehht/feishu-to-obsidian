#!/usr/bin/env python3
"""
链接后处理脚本

功能：
  1. 扫描 output_dir 下所有 .md 文件
  2. 将飞书风格的超链接（指向飞书域名的外链）转换为 Obsidian [[wikilink]]
  3. 生成迁移报告（总数、成功、失败列表）

用法：
  python post_process.py --config config.yaml
  python post_process.py --config config.yaml --dry-run   # 仅预览，不写文件
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

# 匹配飞书文档链接：https://xxx.feishu.cn/wiki/TOKEN 或 /docx/TOKEN
_FEISHU_LINK_RE = re.compile(
    r'\[([^\]]+)\]\(https?://[a-z0-9\-]+\.(?:feishu\.cn|larkoffice\.com)'
    r'/(?:wiki|docx)/([A-Za-z0-9_\-]+)[^)]*\)'
)

# 匹配 markdown 图片链接（非 wikilink 格式），例如 ![alt](https://...)
_REMOTE_IMG_RE = re.compile(r'!\[([^\]]*)\]\((https?://[^)]+)\)')


def _load_title_map(output_root: Path) -> dict[str, str]:
    """
    构建 token → 本地文件名（不含扩展名）的映射。
    通过扫描所有 .md 文件的 frontmatter feishu_url 字段提取 token。
    """
    token_to_title: dict[str, str] = {}
    for md_file in output_root.rglob("*.md"):
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        # 提取 feishu_url 中的 token
        url_match = re.search(r'feishu_url:\s*"[^"]+/wiki/([A-Za-z0-9_\-]+)"', text)
        if url_match:
            token = url_match.group(1)
            # 用文件名（不含扩展名）作为 wikilink 目标
            token_to_title[token] = md_file.stem
    return token_to_title


def _process_file(md_path: Path, token_map: dict[str, str], dry_run: bool) -> dict:
    """
    处理单个 .md 文件，返回修改情况统计。
    """
    original = md_path.read_text(encoding="utf-8", errors="ignore")
    converted = original
    changes = []

    def replace_link(m: re.Match) -> str:
        display = m.group(1)
        token = m.group(2)
        if token in token_map:
            target = token_map[token]
            changes.append(f"  [{display}](.../{token}...) → [[{target}]]")
            return f"[[{target}]]"
        else:
            # 找不到对应本地文件，保留原链接
            return m.group(0)

    converted = _FEISHU_LINK_RE.sub(replace_link, converted)

    if converted != original and not dry_run:
        md_path.write_text(converted, encoding="utf-8")

    return {
        "file": str(md_path),
        "changes": len(changes),
        "details": changes,
        "modified": converted != original,
    }


def main():
    parser = argparse.ArgumentParser(description="飞书迁移 Obsidian 链接后处理")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不修改文件")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[错误] 找不到配置文件: {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    output_root = Path(os.path.expanduser(cfg.get("output_dir", "~/obsidian-vault")))
    if not output_root.exists():
        print(f"[错误] output_dir 不存在: {output_root}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("[ 预览模式：不修改任何文件 ]")

    print("扫描本地文档，构建 token → 文件名映射 ...")
    token_map = _load_title_map(output_root)
    print(f"  找到 {len(token_map)} 篇带 feishu_url 的文档")

    md_files = list(output_root.rglob("*.md"))
    print(f"开始处理 {len(md_files)} 个 Markdown 文件 ...")

    total_changes = 0
    modified_files = []
    unchanged_files = []

    for md_path in md_files:
        result = _process_file(md_path, token_map, dry_run=args.dry_run)
        if result["changes"] > 0:
            total_changes += result["changes"]
            modified_files.append(result)
            if args.dry_run:
                print(f"  [预览] {md_path.name}: {result['changes']} 处链接将被转换")
                for d in result["details"]:
                    print(f"    {d}")
        else:
            unchanged_files.append(str(md_path))

    # 生成报告
    report = {
        "summary": {
            "total_files": len(md_files),
            "modified_files": len(modified_files),
            "total_link_changes": total_changes,
            "dry_run": args.dry_run,
        },
        "modified": modified_files,
    }
    report_path = output_root / "post_process_report.json"
    if not args.dry_run:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print()
    print("=" * 50)
    print(f"处理完成！")
    print(f"  共扫描文件: {len(md_files)}")
    print(f"  修改文件数: {len(modified_files)}")
    print(f"  转换链接数: {total_changes}")
    if not args.dry_run and modified_files:
        print(f"  报告已写入: {report_path}")


if __name__ == "__main__":
    main()
