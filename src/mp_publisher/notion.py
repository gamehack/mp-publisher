from __future__ import annotations

import hashlib
import json
import re
from html import escape as html_escape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from typing import Any
from uuid import UUID

import httpx
from markdown import markdown as markdown_to_html

from .errors import PublisherError, SkipArticle
from .models import Article
from .profiles import Profile, display_title, expected_title, source_key, validate_period
from . import x_compat


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
READY_RE = re.compile(
    r"^QJAI_PUBLISH_READY v=(?P<version>1) source_key=(?P<source>(?:overseas-stories:(?:0[1-9]|[1-9][0-9]{1,4})|[a-z0-9_-]+:[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?)) content_sha256=(?P<sha>[0-9a-f]{64})$"
)
FORBIDDEN = re.compile(r"(?:^|[^A-Z])(TEST|DUPLICATE|FAILED|DRAFT)(?:$|[^A-Z])", re.IGNORECASE)
ARCHIVE_HEADER = re.compile(
    r"^>\s*潜界 AI 文章归档。来源唯一键：(?:`[^`]+`|<code>[^<>]+</code>)。"
    r"\s*原文 SHA-256：(?:`[0-9a-f]{64}`|<code>[0-9a-f]{64}</code>)。\s*$"
)
X_FOOTER = "关注公众号「潜界 AI」，持续获取值得关注的 AI 创业机会与实用方法。"
OVERSEAS_SOURCES_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+)?(?:\*\*|__)?资料来源(?:\*\*|__)?(?:\s*[：:].*)?\s*$"
)


def normalize_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise PublisherError("Invalid Notion UUID") from None


def notion_title(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(str(p.get("plain_text", "")) for p in prop.get("title", []))
    raise PublisherError("Notion page has no title")


class NotionReader:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None):
        if not token:
            raise PublisherError("NOTION_API_KEY is required")
        self.client = httpx.Client(
            base_url=NOTION_API,
            timeout=25,
            follow_redirects=False,
            transport=transport,
            headers={
                "Authorization": "Bearer " + token,
                "Notion-Version": NOTION_VERSION,
                "Accept": "application/json",
                "User-Agent": "QJAI-Text-Publisher/1.0",
            },
        )

    def close(self) -> None:
        self.client.close()

    def _get(self, path: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        if not path.startswith("/") or ".." in path or "//" in path:
            raise PublisherError("Invalid Notion path")
        try:
            response = self.client.get(path, params=params)
            if response.status_code != 200:
                raise PublisherError(f"Notion API returned HTTP {response.status_code}")
            if len(response.content) > 1_000_000:
                raise PublisherError("Notion response exceeds safety limit")
            data = response.json()
        except PublisherError:
            raise
        except (httpx.HTTPError, ValueError):
            raise PublisherError("Notion API connection or response failed") from None
        if not isinstance(data, dict):
            raise PublisherError("Notion API returned a non-object")
        return data

    def page(self, page_id: str) -> dict[str, Any]:
        return self._get("/pages/" + normalize_uuid(page_id))

    def children(self, block_id: str) -> list[dict[str, Any]]:
        parent = normalize_uuid(block_id)
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params: dict[str, object] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get(f"/blocks/{parent}/children", params)
            batch = data.get("results")
            if not isinstance(batch, list) or not isinstance(data.get("has_more"), bool):
                raise PublisherError("Incomplete Notion pagination response")
            if not all(isinstance(item, dict) for item in batch):
                raise PublisherError("Invalid Notion child block")
            results.extend(batch)
            if not data["has_more"]:
                return results
            cursor = data.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise PublisherError("Inconsistent Notion pagination cursor")
            seen.add(cursor)

    def overseas_issues(self, parent_id: str) -> tuple[str, ...]:
        """Enumerate contiguous issue numbers, ignoring internal editorial pages."""
        serials: set[int] = set()
        for child in self.children(parent_id):
            if child.get("type") != "child_page":
                continue
            title = (child.get("child_page") or {}).get("title")
            if not isinstance(title, str):
                raise PublisherError("Invalid overseas child title")
            match = re.match(r"^海外创业志 (0[1-9]|[1-9][0-9]{1,4})\|", title)
            if match:
                serial = int(match.group(1))
                if serial in serials:
                    raise PublisherError("Duplicate overseas issue in Notion")
                serials.add(serial)
        if serials and sorted(serials) != list(range(1, max(serials) + 1)):
            raise PublisherError("Overseas issues contain a gap; manual review required")
        return tuple(f"{n:02d}" for n in sorted(serials))

    def all_blocks(self, page_id: str) -> list[dict[str, Any]]:
        def descend(parent: str, depth: int) -> list[dict[str, Any]]:
            if depth > 12:
                raise PublisherError("Notion nesting exceeds safety limit")
            out: list[dict[str, Any]] = []
            for block in self.children(parent):
                out.append(block)
                if block.get("has_children"):
                    out.extend(descend(str(block.get("id", "")), depth + 1))
            return out

        return descend(page_id, 0)

    def markdown(self, page_id: str) -> str:
        data = self._get("/pages/" + normalize_uuid(page_id) + "/markdown", {"markdown_version": "v2"})
        if data.get("object") != "page_markdown" or not isinstance(data.get("markdown"), str):
            raise PublisherError("Invalid Notion markdown response")
        if data.get("truncated") is not False:
            raise PublisherError("Notion markdown is truncated")
        if data.get("unknown_block_ids") not in ([], None):
            raise PublisherError("Notion markdown contains unknown blocks")
        if data.get("warnings") not in ([], None):
            raise PublisherError("Notion markdown returned warnings")
        return data["markdown"]

    def select(self, profile: Profile, period: str) -> tuple[str, str]:
        validate_period(profile, period)
        selector, exact = expected_title(profile, period)
        matches: list[tuple[str, str]] = []
        for child in self.children(profile.parent_id):
            if child.get("type") != "child_page":
                continue
            title = (child.get("child_page") or {}).get("title")
            if not isinstance(title, str):
                raise PublisherError("Invalid child page title")
            if (title == selector) if exact else title.startswith(selector):
                matches.append((normalize_uuid(str(child.get("id", ""))), title))
        if not matches:
            raise SkipArticle("Expected Notion article is missing")
        if len(matches) != 1:
            raise PublisherError("Notion period has duplicate child pages")
        page_id, title = matches[0]
        if FORBIDDEN.search(title):
            raise SkipArticle("Notion title contains a forbidden lifecycle label")
        page = self.page(page_id)
        parent = page.get("parent") or {}
        if parent.get("type") != "page_id" or normalize_uuid(parent.get("page_id", "")) != normalize_uuid(profile.parent_id):
            raise PublisherError("Notion page is not a direct child of its allowlisted parent")
        if notion_title(page) != title:
            raise PublisherError("Notion title changed during selection")
        return page_id, title


def canonical_content(value: str) -> str:
    return value.rstrip() + "\n"


def split_ready(markdown: str, expected_source_key: str) -> tuple[str, str]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    matches = [(index, READY_RE.fullmatch(line.strip().replace(r"\_", "_"))) for index, line in enumerate(lines)]
    matches = [(index, match) for index, match in matches if match is not None]
    if not matches:
        raise SkipArticle("Notion page is not READY")
    if len(matches) != 1:
        raise PublisherError("Notion page has duplicate READY markers")
    index, match = matches[0]
    assert match is not None
    if any(line.strip() for line in lines[index + 1:]):
        raise PublisherError("READY marker must be the final content line")
    if match.group("source") != expected_source_key:
        raise PublisherError("READY sourceKey does not match the selected period")
    before = canonical_content("\n".join(lines[:index]))
    digest = hashlib.sha256(before.encode("utf-8")).hexdigest()
    if digest != match.group("sha"):
        raise SkipArticle("Notion content changed after READY verification")
    return before, digest


def reader_markdown(value: str, title: str) -> str:
    lines = value.splitlines()
    if lines and ARCHIVE_HEADER.fullmatch(lines[0].strip()):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    if not body:
        raise PublisherError("Reader-facing Markdown is empty")
    if not body.lstrip().startswith("# "):
        body = f"# {title}\n\n" + body
    return body.rstrip() + "\n"


def summary_from_markdown(value: str) -> str:
    for paragraph in re.split(r"\n\s*\n", value):
        text = paragraph.strip()
        if not text or text.startswith(("#", ">", "---", "<")):
            continue
        text = re.sub(r"[*_`\[\]()]", "", text)
        if text:
            return text[:120]
    raise PublisherError("Cannot derive a safe article summary")


class _SafeArticleHtml(HTMLParser):
    """Allow only inert, predictable article markup in WeChat draft HTML.

    Python-Markdown accepts raw HTML and arbitrary link protocols by default.
    Notion content (and imported source text) must never be able to inject
    scripts, event handlers, style attributes, iframes or javascript: links.
    """

    ELEMENTS = {
        "p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em",
        "b", "i", "ul", "ol", "li", "blockquote", "code", "pre",
        "a", "hr", "br", "table", "thead", "tbody", "tr", "td",
        "th", "del", "sup", "sub", "dl", "dt", "dd",
    }
    VOID = {"hr", "br"}

    def __init__(self, profile: str = ""):
        super().__init__(convert_charrefs=True)
        self.profile = profile
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.ELEMENTS:
            raise PublisherError(f"Unsupported article HTML element: {tag}")
        if tag == "a":
            hrefs = [v for k, v in attrs if k == "href"]
            if len(hrefs) != 1 or not hrefs[0]:
                raise PublisherError("Article link must have one explicit destination")
            url = hrefs[0]
            parsed = urlsplit(url)
            if (parsed.scheme not in {"https", "http"} or not parsed.netloc or
                    parsed.username is not None or parsed.password is not None or
                    any(ord(c) < 33 or ord(c) == 127 for c in url)):
                raise PublisherError("Article contains an unsafe link destination")
            self.parts.append('<a href="' + html_escape(url, quote=True) + '">')
        elif tag in self.VOID:
            self.parts.append(f"<{tag}/>")
        else:
            # All source-provided attributes (including on* and style) are
            # discarded. Only these trusted, per-column styles are emitted.
            styles = {
                "producthunt": {
                    "h2": "margin-top:24px;font-size:19px;color:#155e75;border-left:4px solid #0891b2;padding-left:10px",
                    "p": "margin:12px 0",
                    "li": "margin:8px 0",
                },
                "trustmrr": {
                    "h2": "font-size:20px;color:#243447;margin:20px 0 12px",
                    "h3": "font-size:17px;color:#243447;margin:20px 0 10px",
                    "p": "margin:10px 0",
                    "li": "margin:7px 0",
                },
            }
            trusted_style = styles.get(self.profile, {}).get(tag)
            self.parts.append(f'<{tag} style="{trusted_style}">' if trusted_style else f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.ELEMENTS:
            raise PublisherError(f"Unsupported article HTML element: {tag}")
        if tag not in self.VOID:
            self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.VOID:
            raise PublisherError(f"Unsupported self-closing HTML element: {tag}")
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self.parts.append(html_escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        raise PublisherError("HTML comments are not allowed in article content")

    def handle_decl(self, decl: str) -> None:
        raise PublisherError("HTML declarations are not allowed in article content")


def wechat_markdown(profile: str, reader_body: str) -> str:
    """Derive WeChat text without changing the canonical Notion document."""
    lines = reader_body.splitlines()
    # WeChat displays the title from its draft metadata; don't repeat the H1.
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    if profile == "producthunt":
        # PH API links are often very long, tracked URLs. The previous WeChat
        # renderer hid them; retain them only in canonical Notion Markdown.
        lines = [
            line for line in lines
            if not re.match(r"^\s*[-*]\s*资料来源：", line)
        ]
    elif profile == "overseas":
        # Source references are part of the canonical Notion record and READY
        # hash, but are internal editorial metadata, not reader-facing content.
        # Remove the final section only from the WeChat rendering.
        for index, line in enumerate(lines):
            if OVERSEAS_SOURCES_HEADING.fullmatch(line.strip()):
                lines = lines[:index]
                break
    return "\n".join(lines).strip() + "\n"


def html_from_markdown(value: str, profile: str = "") -> str:
    # Escape raw HTML in the original Markdown before rendering. This preserves
    # Markdown structure but never interprets arbitrary HTML supplied by source.
    escaped = value.replace("<", "&lt;")
    rendered = markdown_to_html(escaped, extensions=["extra", "sane_lists"])
    sanitizer = _SafeArticleHtml(profile)
    sanitizer.feed(rendered)
    sanitizer.close()
    section_styles = {
        "producthunt": "font-size:16px;color:#25364b;line-height:1.85;letter-spacing:.2px",
        "trustmrr": "font-size:15px;color:#243447;line-height:1.85",
    }
    outer_style = section_styles.get(
        profile, "max-width:680px;margin:auto;background:#fff;padding:8px 12px;"
                 "font-size:16px;line-height:1.85;color:#25364a"
    )
    return '<section style="' + outer_style + '">' + "".join(sanitizer.parts) + "</section>"


def load_article(reader: NotionReader, profile: Profile, period: str) -> Article:
    period = validate_period(profile, period)
    key = source_key(profile, period)
    page_id, page_title = reader.select(profile, period)
    title = display_title(profile, period, page_title)
    if profile.name == "x":
        raw = reader.all_blocks(page_id)
        x_compat.validate_ready(raw)
        blocks, digest = x_compat.extract_blocks(raw)
        cleaned = x_compat.reader_blocks(blocks)
        body = x_compat.render_markdown(cleaned, title, X_FOOTER)
        summary = x_compat.digest_from_summary(cleaned)
        html = x_compat.render_html(cleaned, X_FOOTER)
    else:
        raw_markdown = reader.markdown(page_id)
        verified, digest = split_ready(raw_markdown, key)
        body = reader_markdown(verified, title)
        summary = summary_from_markdown(body)
        html = html_from_markdown(wechat_markdown(profile.name, body), profile.name)
    return Article(
        profile=profile.name,
        period=period,
        source_key=key,
        notion_page_id=page_id,
        notion_title=page_title,
        title=title,
        summary=summary,
        body_markdown=body,
        wechat_html=html,
        content_sha256=digest,
    )
