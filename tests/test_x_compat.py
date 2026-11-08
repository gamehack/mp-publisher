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


def test_repeated_source_row_with_same_as_above_annotation_is_removed():
    url = "https://x.com/SISpotlight/status/2119124244042756551"
    text = "真实正文\n原帖链接：" + url + "（同上）\n后续正文"
    start = text.index(url)
    block = Block("paragraph", text, (url,), ((start, start + len(url), url),))
    assert reader_blocks([block])[0].text == "真实正文\n后续正文"


def test_annotated_bullet_source_row_is_removed():
    url = "https://x.com/SISpotlight/status/2119124244042756551"
    text = "原帖链接：" + url + "（同上）"
    start = text.index(url)
    block = Block("bulleted_list_item", text, (url,), ((start, start + len(url), url),))
    assert reader_blocks([Block("paragraph", "说明"), block]) == [Block("paragraph", "说明")]


def test_annotated_source_row_still_rejects_mismatched_link():
    url = "https://x.com/example/status/1"
    other = "https://x.com/example/status/2"
    text = "原帖链接：" + url + "（同上）"
    start = text.index(url)
    with pytest.raises(PublisherError, match="target differs"):
        reader_blocks([Block("bulleted_list_item", text, (other,), ((start, start + len(url), other),))])


@pytest.mark.parametrize("suffix", ["（未经核验）", "（同上）多余文字", "（同上）https://evil.example/"])
def test_source_row_rejects_other_trailing_content(suffix: str):
    url = "https://x.com/example/status/1"
    with pytest.raises(PublisherError, match="Ambiguous X source row"):
        reader_blocks([Block("bulleted_list_item", "原帖链接：" + url + suffix)])


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
