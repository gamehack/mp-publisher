import pytest

from mp_publisher.errors import PublisherError, SkipArticle
from mp_publisher.x_compat import Block, reader_blocks, validate_ready


def _raw(text: str):
    return {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}


def test_mixed_source_row_is_removed_without_losing_prose():
    url = "https://x.com/example/status/1"
    text = "真实正文\n原帖链接：" + url + "\n后续正文"
    start = text.index(url)
    block = Block("paragraph", text, (url,), ((start, start + len(url), url),))
    assert reader_blocks([block])[0].text == "真实正文\n后续正文"


def test_embedded_x_link_in_prose_fails_closed():
    url = "https://x.com/example/status/1"
    text = "请阅读 " + url
    start = text.index(url)
    with pytest.raises(PublisherError, match="outside source-only row"):
        reader_blocks([Block("paragraph", text, (url,), ((start, start + len(url), url),))])


def test_ready_must_be_exactly_one():
    validate_ready([_raw("交付状态：READY")])
    validate_ready([_raw("日期：2026-11-06\n生成者：Grok\n交付状态：READY\n实际完成时间：06:12")])
    with pytest.raises(SkipArticle):
        validate_ready([_raw("交付状态：DRAFT")])
    with pytest.raises(SkipArticle):
        validate_ready([_raw("交付状态：READY"), _raw("交付状态：READY")])
