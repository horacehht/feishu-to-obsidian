"""
飞书文档块（Block）→ Obsidian Markdown 转换器

支持块类型：
  文本/段落、标题 1-9、无序列表、有序列表（嵌套）、代码块、
  引用、待办事项、Callout、分割线、图片、表格、引用容器

图片输出格式：  ![[文件名.png]]  （Obsidian wikilink）
文档内链格式：  [[文档标题]]
"""

from dataclasses import dataclass, field

# ── 块类型常量 ─────────────────────────────────────────────────────────────────
PAGE            = 1
TEXT            = 2
HEADING1        = 3   # heading1 ~ heading9 连续
HEADING9        = 11
BULLET          = 12
ORDERED         = 13
CODE            = 14
QUOTE           = 15
TODO            = 17
CALLOUT         = 19
GRID            = 24
GRID_COLUMN     = 25
DIVIDER         = 21
FILE            = 22
IMAGE           = 27
TABLE           = 30
TABLE_CELL      = 31
QUOTE_CONTAINER = 34

# 代码块语言 ID → 标识符
_CODE_LANG: dict[int, str] = {
    1: "",        3: "bash",    4: "c",       5: "cpp",     6: "csharp",
    8: "css",     10: "go",     12: "html",   13: "java",   14: "javascript",
    16: "json",   19: "kotlin", 21: "latex",  22: "lua",    25: "markdown",
    27: "objc",   28: "php",    29: "perl",   31: "python", 32: "r",
    34: "ruby",   35: "rust",   37: "sql",    39: "scala",  41: "shell",
    43: "swift",  44: "tex",    45: "typescript", 48: "xml", 49: "yaml",
}

# Callout 背景色 ID → Obsidian callout 类型
_CALLOUT_TYPE: dict[int, str] = {
    1: "note", 2: "tip", 3: "important", 4: "warning", 5: "caution",
}


@dataclass
class _Ctx:
    blocks: dict        # block_id → block dict
    images: list        # 收集 (file_token, filename) 供外层下载
    doc_map: dict       # obj_token → title（用于内链解析）
    _counters: dict = field(default_factory=dict)  # 有序列表计数


# ── 公开接口 ───────────────────────────────────────────────────────────────────

def convert_blocks(blocks_list: list[dict], doc_map: dict | None = None) -> tuple[str, list]:
    """
    将飞书文档的 flat blocks 列表转为 Markdown 字符串。

    Returns:
        (markdown_text, images)
        images: list of (file_token: str, filename: str)
    """
    blocks = {b["block_id"]: b for b in blocks_list}
    ctx = _Ctx(blocks=blocks, images=[], doc_map=doc_map or {})

    page = next((b for b in blocks_list if b["block_type"] == PAGE), None)
    if not page:
        return "", []

    parts = []
    for child_id in page.get("children", []):
        chunk = _block(child_id, ctx, depth=0)
        if chunk is not None:
            parts.append(chunk)

    return "\n\n".join(p for p in parts if p), ctx.images


# ── 块分发 ─────────────────────────────────────────────────────────────────────

def _block(block_id: str, ctx: _Ctx, depth: int = 0, ordered_idx: int = 1) -> str | None:
    b = ctx.blocks.get(block_id)
    if b is None:
        return None

    bt = b["block_type"]

    if bt == TEXT:
        return _text_block(b, ctx)

    if HEADING1 <= bt <= HEADING9:
        level = bt - HEADING1 + 1
        key = f"heading{level}"
        elems = b.get(key, {}).get("elements", [])
        return f"{'#' * level} {_elements(elems, ctx)}"

    if bt == BULLET:
        return _list_item(b, ctx, depth, ordered=False)

    if bt == ORDERED:
        return _list_item(b, ctx, depth, ordered=True, idx=ordered_idx)

    if bt == CODE:
        return _code_block(b, ctx)

    if bt == QUOTE:
        text = _elements(b.get("quote", {}).get("elements", []), ctx)
        return f"> {text}"

    if bt == TODO:
        todo = b.get("todo", {})
        done = todo.get("style", {}).get("done", False)
        check = "x" if done else " "
        text = _elements(todo.get("elements", []), ctx)
        return f"- [{check}] {text}"

    if bt == CALLOUT:
        return _callout(b, ctx)

    if bt == DIVIDER:
        return "---"

    if bt == IMAGE:
        return _image(b, ctx)

    if bt == TABLE:
        return _table(b, ctx)

    if bt == TABLE_CELL:
        return None  # 由 _table 统一处理

    if bt == QUOTE_CONTAINER:
        return _quote_container(b, ctx)

    if bt == GRID:
        # 多栏布局：将各列内容顺序拼接
        parts = []
        for col_id in b.get("children", []):
            col = ctx.blocks.get(col_id)
            if col is None:
                continue
            for child_id in col.get("children", []):
                chunk = _block(child_id, ctx, depth=0)
                if chunk:
                    parts.append(chunk)
        return "\n\n".join(parts) if parts else None

    if bt == GRID_COLUMN:
        return None  # 由 GRID 统一处理

    if bt == FILE:
        name = b.get("file", {}).get("name", "附件")
        return f"> 📎 **附件**：{name}（飞书附件，需在飞书中手动下载）"

    # 未知块类型：尝试降级提取文本
    return None


# ── 富文本元素 ─────────────────────────────────────────────────────────────────

def _elements(elems: list, ctx: _Ctx) -> str:
    return "".join(_elem(e, ctx) for e in elems)


def _elem(e: dict, ctx: _Ctx) -> str:
    if "text_run" in e:
        return _text_run(e["text_run"])
    if "mention_doc" in e:
        return _mention_doc(e["mention_doc"], ctx)
    if "mention_user" in e:
        return f"@{e['mention_user'].get('name', '用户')}"
    if "equation" in e:
        return f"${e['equation'].get('content', '')}$"
    return ""


def _text_run(run: dict) -> str:
    content = run.get("content", "")
    if not content:
        return ""
    style = run.get("text_element_style", {})

    # 超链接：不叠加其他样式，仅允许 http/https 协议防止注入
    link_url = style.get("link", {}).get("url", "")
    if link_url:
        if link_url.startswith(("http://", "https://")):
            return f"[{content}]({link_url})"
        return content  # 非安全协议，降级为纯文本

    if style.get("inline_code"):
        return f"`{content}`"

    bold = style.get("bold", False)
    italic = style.get("italic", False)
    if bold and italic:
        content = f"***{content}***"
    elif bold:
        content = f"**{content}**"
    elif italic:
        content = f"*{content}*"

    if style.get("strikethrough"):
        content = f"~~{content}~~"
    if style.get("underline"):
        content = f"<u>{content}</u>"

    return content


def _mention_doc(mention: dict, ctx: _Ctx) -> str:
    token = mention.get("token", "")
    title = mention.get("title") or token
    # 优先用 doc_map 里的本地标题
    if token in ctx.doc_map:
        title = ctx.doc_map[token]
    return f"[[{title}]]"


# ── 具体块转换 ─────────────────────────────────────────────────────────────────

def _text_block(b: dict, ctx: _Ctx) -> str:
    return _elements(b.get("text", {}).get("elements", []), ctx)


def _list_item(b: dict, ctx: _Ctx, depth: int, ordered: bool, idx: int = 1) -> str:
    key = "ordered" if ordered else "bullet"
    data = b.get(key, {})
    text = _elements(data.get("elements", []), ctx)

    indent = "  " * depth
    prefix = f"{indent}{idx}. " if ordered else f"{indent}- "
    lines = [prefix + text]

    # 递归子节点（嵌套列表）
    child_ordered_idx = 1
    for child_id in b.get("children", []):
        child = ctx.blocks.get(child_id)
        if child is None:
            continue
        child_bt = child["block_type"]
        result = _block(child_id, ctx, depth=depth + 1,
                        ordered_idx=child_ordered_idx if child_bt == ORDERED else 1)
        if result:
            lines.append(result)
        if child_bt == ORDERED:
            child_ordered_idx += 1

    return "\n".join(lines)


def _code_block(b: dict, ctx: _Ctx) -> str:
    code = b.get("code", {})
    lang = _CODE_LANG.get(code.get("style", {}).get("language", 1), "")
    text = "".join(
        e.get("text_run", {}).get("content", "")
        for e in code.get("elements", [])
    )
    return f"```{lang}\n{text}\n```"


def _callout(b: dict, ctx: _Ctx) -> str:
    bg = b.get("callout", {}).get("background_color", 1)
    callout_type = _CALLOUT_TYPE.get(bg, "note")
    lines = [f"> [!{callout_type}]"]
    for child_id in b.get("children", []):
        child_text = _block(child_id, ctx)
        if child_text:
            for line in child_text.splitlines():
                lines.append(f"> {line}")
    return "\n".join(lines)


def _image(b: dict, ctx: _Ctx) -> str:
    token = b.get("image", {}).get("token", "")
    if not token:
        return ""
    filename = f"{token}.png"   # 扩展名在下载时按 magic bytes 修正
    ctx.images.append((token, filename))
    return f"![[{filename}]]"


def _table(b: dict, ctx: _Ctx) -> str:
    prop = b.get("table", {}).get("property", {})
    rows = prop.get("row_size", 0)
    cols = prop.get("column_size", 0)
    cell_ids = b.get("children", [])

    if rows == 0 or cols == 0:
        return ""

    grid: list[list[str]] = []
    for r in range(rows):
        row = []
        for c in range(cols):
            idx = r * cols + c
            cell_text = ""
            if idx < len(cell_ids):
                cell_b = ctx.blocks.get(cell_ids[idx])
                if cell_b:
                    cell_text = _cell_text(cell_b, ctx)
            row.append(cell_text)
        grid.append(row)

    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    for row in grid[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _cell_text(cell_b: dict, ctx: _Ctx) -> str:
    parts = []
    for child_id in cell_b.get("children", []):
        t = _block(child_id, ctx)
        if t:
            parts.append(t.replace("\n", " ").replace("|", "\\|"))
    return " ".join(parts)


def _quote_container(b: dict, ctx: _Ctx) -> str:
    lines = []
    for child_id in b.get("children", []):
        child_text = _block(child_id, ctx)
        if child_text:
            for line in child_text.splitlines():
                lines.append(f"> {line}")
    return "\n".join(lines)
