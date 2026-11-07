from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Article:
    profile: str
    period: str
    source_key: str
    notion_page_id: str
    notion_title: str
    title: str
    summary: str
    body_markdown: str
    wechat_html: str
    content_sha256: str
    version: int = 1

    def public_summary(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "period": self.period,
            "source_key": self.source_key,
            "notion_page_id": self.notion_page_id,
            "title": self.title,
            "content_sha256": self.content_sha256,
            "version": self.version,
        }
