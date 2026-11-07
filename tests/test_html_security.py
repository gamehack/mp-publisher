"""Security regressions for Markdown-derived WeChat draft content.

Notion originals and source text are data, not trusted HTML.
"""
import pytest

from mp_publisher.errors import PublisherError
from mp_publisher.notion import html_from_markdown, wechat_markdown


def test_raw_html_is_inert_and_escaped():
    output = html_from_markdown(
        "# AI 商业机会\n\n"
        "<script>alert('x')</script>\n\n"
        "<img src=x onerror=alert(1)>\n\n"
        "普通正文 **重要**"
    )
    assert "<script" not in output
    assert "<img" not in output
    assert "<strong>重要</strong>" in output
    assert "&lt;script&gt;" in output
    assert "onerror=" in output  # Visible text only; never an HTML attribute.


@pytest.mark.parametrize("target", [
    "javascript:alert", "data:text/html;base64,abc", "file:///etc/passwd",
])
def test_executable_or_local_links_fail_closed(target):
    with pytest.raises(PublisherError, match="unsafe link"):
        html_from_markdown(f"# 标题\n\n[恶意链接]({target})")


def test_legacy_http_and_https_sources_are_preserved_without_source_attributes():
    output = html_from_markdown(
        "# 标题\n\n"
        "[HTTP 来源](http://example.com/a?q=1&v=2 \"ignored\") "
        "[HTTPS 来源](https://example.org/b)"
    )
    assert 'href="http://example.com/a?q=1&amp;v=2"' in output
    assert 'href="https://example.org/b"' in output
    assert 'ignored' not in output


def test_markdown_images_fail_closed_until_media_upload_is_supported():
    with pytest.raises(PublisherError, match="Unsupported .*HTML element: img"):
        html_from_markdown("![远程追踪图](https://example.org/pixel.png)")


def test_inline_markup_and_quote_remain_readable():
    output = html_from_markdown("# 标题\n\n> 免责声明\n\n- 项目一\n- 项目二")
    assert "<h1>标题</h1>" in output
    assert "<blockquote>" in output
    assert "<li>项目二</li>" in output


def test_producthunt_wechat_hides_source_rows_without_changing_notion_article():
    canonical = (
        "# 产品猎人日报标题\n\n引言\n\n"
        "## 产品机会\n\n这里是正文。\n\n"
        "- 资料来源：[很长的 Product Hunt 链接](https://www.producthunt.com/products/example?utm_source=tracking)\n\n"
        "## 创业观察\n\n可执行的行动建议。\n"
    )
    channel_body = wechat_markdown("producthunt", canonical)
    assert "资料来源" not in channel_body
    assert "utm_source" not in channel_body
    assert canonical.startswith("# 产品猎人日报标题")  # unchanged source
    assert "utm_source=tracking" in canonical
    html = html_from_markdown(channel_body, "producthunt")
    assert "这里是正文" in html
    assert 'border-left:4px solid #0891b2' in html
    assert "utm_source" not in html
    assert "<h1>" not in html


def test_trustmrr_wechat_preserves_provenance_and_uses_report_style():
    markdown = "# 周报\n\n## TrustMRR 增长 Top 25\n\n[TrustMRR 来源](https://trustmrr.com/startup/example)\n"
    channel_body = wechat_markdown("trustmrr", markdown)
    assert channel_body.startswith("## TrustMRR")
    html = html_from_markdown(channel_body, "trustmrr")
    assert 'href="https://trustmrr.com/startup/example"' in html
    assert 'font-size:15px;color:#243447' in html
