"""Gather current information about a market before forecasting it.

Why this exists: the forecaster previously reasoned only from the market
description that shipped with the market. It therefore had no information the
market did not already have, which is the structural reason it could not
expect to beat the price. The repo already shipped news and search connectors;
nothing in the trading path called them.

This is best-effort by design. Every source is optional and every failure is
swallowed, because a research outage must downgrade the forecast rather than
abort the run. What it does NOT do is hide the downgrade: the result carries
which sources actually answered, the prompt is told when it is forecasting
blind, and the journal records it, so calibration can later be split between
forecasts made with current data and forecasts made without.
"""

from typing import List

MAX_CONTEXT_CHARS = 4000


class Research:
    def __init__(self, news=None, search=None):
        self.news = news
        self.search = search

    def _news_context(self, question: str) -> str:
        articles = self.news.get_articles_for_question(question)
        if not articles:
            return ""
        lines = []
        for article in articles:
            title = (article.get("title") or "").strip()
            published = (article.get("publishedAt") or "")[:10]
            source = ((article.get("source") or {}).get("name") or "").strip()
            description = (article.get("description") or "").strip()
            if not title:
                continue
            lines.append(f"- [{published}] {source}: {title}. {description}")
        return "\n".join(lines)

    def gather(self, question: str) -> dict:
        """Collect context for `question`.

        Returns the context text and the list of sources that answered. An
        empty `sources` list means the forecast will be made blind.
        """
        sections: List[str] = []
        sources: List[str] = []

        if self.search is not None and getattr(self.search, "available", False):
            try:
                context = self.search.get_context(question)
                if context and str(context).strip():
                    sections.append(f"Web search context:\n{context}")
                    sources.append("tavily")
            except Exception as e:
                sections.append(f"(web search unavailable: {e})")

        if self.news is not None and getattr(self.news, "available", False):
            try:
                headlines = self._news_context(question)
                if headlines:
                    sections.append(f"Recent news headlines:\n{headlines}")
                    sources.append("newsapi")
            except Exception as e:
                sections.append(f"(news unavailable: {e})")

        text = "\n\n".join(sections)[:MAX_CONTEXT_CHARS]
        return {"context": text, "sources": sources}
