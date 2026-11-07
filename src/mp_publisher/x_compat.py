"""Compatibility renderer for the established X Daily Notion format.

The source-row rules intentionally mirror qjai-x-mp-publisher. Ambiguous links
fail closed instead of leaking raw X source rows into the reader-facing draft.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
import re
from urllib.parse import urlsplit

from .errors import PublisherError, SkipArticle


REPORT_META = (
    "日期：", "采集区间：", "生成者：", "交付状态：", "实际完成时间",
    "原帖来源数量：", "公众号主题：",
)
SOURCE_LABELS = ("原帖链接：", "原始帖文链接：")
SOURCE_URL_ROW = re.compile(r"^\s*(?:原帖链接|原始帖文链接)：\s*(https?://[\x21-\x7e]+)\s*$")
ALLOWED_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
    "numbered_list_item", "quote", "divider", "to_do",
}
MARKDOWN_SPECIAL = re.compile(r"([\\\x60*_{}\[\]()#+.!|<>~-])")


@dataclass(frozen=True)
class Block:
    type: str
    text: str = ""
    links: tuple[str, ...] = ()
    link_spans: tuple[tuple[int, int, str], ...] = ()


def rich_text(block: dict) -> tuple[str, tuple[str, ...]]:
    kind = block["type"]
    obj = block.get(kind) or {}
    prefix = ("☑ " if obj.get("checked") else "☐ ") if kind == "to_do" else ""
    parts = obj.get("rich_text", [])
    return prefix + "".join(p.get("plain_text", "") for p in parts), tuple(
        p.get("href") or ((p.get("text") or {}).get("link") or {}).get("url") or ""
        for p in parts
    )


def validate_ready(raw: list[dict]) -> None:
    statuses = []
    for block in raw:
        if block.get("type") not in ALLOWED_TYPES:
            continue
        # Grok writes the report metadata as one multiline paragraph. Require
        # one exact status row within it; a loose substring match could accept
        # prose that merely mentions READY.
        for line in rich_text(block)[0].splitlines():
            text = line.strip()
            if text.startswith("交付状态："):
                statuses.append(text.removeprefix("交付状态：").strip().upper())
    if statuses != ["READY"]:
        if not statuses:
            raise SkipArticle("X page is missing READY status")
        raise SkipArticle("X page status is not exactly READY")


def extract_blocks(raw: list[dict]) -> tuple[list[Block], str]:
    source = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(source.encode()).hexdigest()
    result = []
    for item in raw:
        kind = item.get("type")
        if kind not in ALLOWED_TYPES:
            raise PublisherError(f"Unsupported Notion block: {kind}")
        text, links = rich_text(item)
        offset = 2 if kind == "to_do" else 0
        spans = []
        for part in (item.get(kind) or {}).get("rich_text", []):
            value = part.get("plain_text", "")
            href = part.get("href") or ((part.get("text") or {}).get("link") or {}).get("url")
            if href and value:
                spans.append((offset, offset + len(value), href))
            offset += len(value)
        result.append(Block(kind, text, links, tuple(spans)))
    if not result:
        raise PublisherError("Notion article has no blocks")
    return result, digest


def _is_x_url(url: str) -> bool:
    try:
        return urlsplit(url).hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
    except ValueError:
        return False


def _strip_mixed_source_rows(block: Block) -> Block:
    lines = block.text.splitlines(keepends=True)
    kept: list[str] = []
    removed_ranges: list[tuple[int, int]] = []
    offset = 0
    source_types = {"paragraph", "bulleted_list_item", "numbered_list_item"}
    for line in lines:
        trimmed = line.strip()
        can_strip = block.type == "paragraph" or (
            block.type in {"bulleted_list_item", "numbered_list_item"} and len(lines) == 1
        )
        match = SOURCE_URL_ROW.fullmatch(trimmed) if can_strip and block.type in source_types else None
        if match and _is_x_url(match.group(1)):
            for start, end, target in block.link_spans:
                if offset <= start and end <= offset + len(line) and target != match.group(1):
                    raise PublisherError("Source-row link target differs from displayed X URL")
            if block.type in {"bulleted_list_item", "numbered_list_item"} and any(
                target and target != match.group(1) for target in block.links
            ):
                raise PublisherError("Source-row link target differs from displayed X URL")
            removed_ranges.append((offset, offset + len(line)))
        elif trimmed.startswith(SOURCE_LABELS):
            raise PublisherError("Ambiguous X source row needs editorial review")
        else:
            kept.append(line)
        offset += len(line)
    for start, end, url in block.link_spans:
        if _is_x_url(url) and not any(lo <= start and end <= hi for lo, hi in removed_ranges):
            raise PublisherError("Embedded X URL outside source-only row needs editorial review")
    for url in block.links:
        if _is_x_url(url) and not any(span[2] == url for span in block.link_spans):
            raise PublisherError("Embedded X URL without source-row provenance")
    return Block(block.type, "".join(kept).strip(), tuple(u for u in block.links if not _is_x_url(u)))


def reader_blocks(blocks: list[Block]) -> list[Block]:
    result = []
    internal = False
    for block in blocks:
        text = block.text.strip()
        if block.type.startswith("heading_"):
            internal = text == "来源与核验说明"
        if internal or text.startswith(REPORT_META):
            continue
        if not text and block.type != "divider":
            continue
        cleaned = _strip_mixed_source_rows(block)
        if not cleaned.text and block.type != "divider":
            continue
        result.append(cleaned)
    if not result:
        raise PublisherError("No reader-facing content after filtering")
    return result


def digest_from_summary(blocks: list[Block]) -> str:
    inside = False
    for block in blocks:
        if block.type.startswith("heading_"):
            if block.text.strip() == "今日摘要":
                inside = True
            elif inside:
                break
        elif inside and block.text.strip():
            return block.text.strip()[:120]
    raise PublisherError("Cannot find digest in 今日摘要")


def render_html(blocks: list[Block], footer: str) -> str:
    styles = {
        "heading_1": "font-size:22px;font-weight:700;color:#12365e;margin:24px 0 12px;line-height:1.5;",
        "heading_2": "font-size:20px;font-weight:700;color:#14527e;border-left:4px solid #20b4c5;padding-left:12px;margin:27px 0 12px;line-height:1.5;",
        "heading_3": "font-size:17px;font-weight:700;color:#176786;margin:20px 0 10px;line-height:1.65;",
        "paragraph": "font-size:16px;line-height:1.85;color:#25364a;margin:12px 0;",
        "quote": "font-size:15px;line-height:1.85;color:#465e75;background:#f1f7fb;padding:10px 14px;margin:15px 0;",
        "list": "font-size:16px;line-height:1.85;color:#25364a;margin:6px 0;padding-left:4px;",
    }
    out = ['<section style="max-width:680px;margin:auto;background:#fff;padding:8px 12px;">']
    for block in blocks:
        text = html.escape(block.text, quote=True)
        if block.type.startswith("heading_"):
            tag = "h" + block.type[-1]
            out.append(f'<{tag} style="{styles[block.type]}">{text}</{tag}>')
        elif block.type == "quote":
            out.append(f'<blockquote style="{styles["quote"]}">{text}</blockquote>')
        elif block.type == "divider":
            out.append('<hr style="border:0;border-top:1px solid #dbeaf0;margin:20px 0;"/>')
        else:
            key = "list" if block.type in {"bulleted_list_item", "numbered_list_item"} else "paragraph"
            marker = "• " if key == "list" else ""
            out.append(f'<p style="{styles[key]}">{marker}{text}</p>')
    out.append('<p style="font-size:14px;line-height:1.8;color:#087b91;margin-top:28px;border-top:1px solid #dbeaf0;padding-top:16px;">'
               + html.escape(footer) + "</p>")
    out.append("</section>")
    return "".join(out)


def render_markdown(blocks: list[Block], title: str, footer: str) -> str:
    def esc(value: str) -> str:
        normalized = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        return MARKDOWN_SPECIAL.sub(r"\\\1", normalized)

    lines = [f"# {esc(title)}", ""]
    for block in blocks:
        text = esc(block.text)
        if block.type.startswith("heading_"):
            lines.extend(["", "#" * int(block.type[-1]) + " " + text, ""])
        elif block.type == "quote":
            lines.extend(["> " + text, ""])
        elif block.type == "divider":
            lines.extend(["", "---", ""])
        elif block.type == "bulleted_list_item":
            lines.append("- " + text)
        elif block.type == "numbered_list_item":
            lines.append("1. " + text)
        else:
            lines.extend([text, ""])
    lines.extend(["", esc(footer), ""])
    return "\n".join(lines).strip() + "\n"
