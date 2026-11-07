import hashlib
import json

import httpx
import pytest

from mp_publisher.errors import PublisherError, SkipArticle
from mp_publisher.notion import NotionReader, load_article, split_ready
from mp_publisher.profiles import PROFILES


def test_ready_marker_hash_and_mutation_detection():
    body = "# 标题\n\n正文\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    marker = f"QJAI_PUBLISH_READY v=1 source_key=producthunt:2026-11-05 content_sha256={digest}"
    assert split_ready(body + marker + "\n", "producthunt:2026-11-05") == (body, digest)
    with pytest.raises(SkipArticle, match="changed"):
        split_ready(body + "被改动\n" + marker, "producthunt:2026-11-05")



def test_notion_v2_escaped_ready_and_code_header_are_accepted():
    from mp_publisher.notion import reader_markdown
    content = "> 潜界 AI 文章归档。来源唯一键：<code>producthunt:2026-10-30</code>。 原文 SHA-256：<code>" + "a" * 64 + "</code>。\n\n正文\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    marker = f"QJAI_PUBLISH_READY v=1 source_key=producthunt:2026-10-30 content_sha256={digest}"
    escaped = marker.replace("_", r"\_")
    verified, got = split_ready(content + escaped + "\n", "producthunt:2026-10-30")
    assert (verified, got) == (content, digest)
    assert "潜界 AI 文章归档" not in reader_markdown(verified, "测试标题")
    assert "# 测试标题" in reader_markdown(verified, "测试标题")
    with pytest.raises(SkipArticle, match="changed"):
        split_ready(content + "被改动\n" + escaped + "\n", "producthunt:2026-10-30")


def test_pagination_requires_consistent_cursor():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"results": [], "has_more": True, "next_cursor": None})

    reader = NotionReader("not-a-real-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(PublisherError, match="cursor"):
        reader.children(PROFILES["producthunt"].parent_id)
    reader.close()


def test_parent_validation_and_full_markdown_read():
    parent = PROFILES["producthunt"].parent_id
    child = "11111111-1111-4111-8111-111111111111"
    body = "# 今日产品\n\n正文\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    marker = f"QJAI_PUBLISH_READY v=1 source_key=producthunt:2026-11-05 content_sha256={digest}\n"

    def handler(request: httpx.Request):
        path = request.url.path
        if path == f"/v1/blocks/{parent}/children":
            return httpx.Response(200, json={"results": [{"id": child, "type": "child_page", "child_page": {"title": "2026-11-05｜今日产品"}}], "has_more": False})
        if path == f"/v1/pages/{child}":
            return httpx.Response(200, json={"parent": {"type": "page_id", "page_id": parent}, "properties": {"title": {"type": "title", "title": [{"plain_text": "2026-11-05｜今日产品"}]}}})
        if path == f"/v1/pages/{child}/markdown":
            return httpx.Response(200, json={"object": "page_markdown", "markdown": body + marker, "truncated": False, "unknown_block_ids": [], "warnings": []})
        raise AssertionError(path)

    reader = NotionReader("not-a-real-secret", transport=httpx.MockTransport(handler))
    article = load_article(reader, PROFILES["producthunt"], "2026-11-05")
    assert article.source_key == "producthunt:2026-11-05"
    assert article.body_markdown == body
    reader.close()


def test_wrong_direct_parent_stops_before_markdown():
    parent = PROFILES["producthunt"].parent_id
    child = "11111111-1111-4111-8111-111111111111"

    def handler(request: httpx.Request):
        if request.url.path.endswith(f"blocks/{parent}/children"):
            return httpx.Response(200, json={"results": [{"id": child, "type": "child_page", "child_page": {"title": "2026-11-05｜标题"}}], "has_more": False})
        return httpx.Response(200, json={"parent": {"type": "page_id", "page_id": "22222222-2222-4222-8222-222222222222"}, "properties": {"title": {"type": "title", "title": [{"plain_text": "2026-11-05｜标题"}]}}})

    reader = NotionReader("not-a-real-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(PublisherError, match="direct child"):
        load_article(reader, PROFILES["producthunt"], "2026-11-05")
    reader.close()


def test_truncated_markdown_never_becomes_draft():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"object": "page_markdown", "markdown": "x", "truncated": True, "unknown_block_ids": [], "warnings": []})

    reader = NotionReader("not-a-real-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(PublisherError, match="truncated"):
        reader.markdown("11111111-1111-4111-8111-111111111111")
    reader.close()
