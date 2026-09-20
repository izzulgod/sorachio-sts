"""
Sorachio-STS Web Search Engine.
Lightweight instant web search using DuckDuckGo Lite (no API keys required).
Fully fault-tolerant with offline fallback.
"""

from __future__ import annotations

import asyncio
from typing import Any

from utils.logging_setup import get_logger

log = get_logger("utils.web_search")


class WebSearchEngine:
    """Instant web search utility for Sorachio agentic search actions."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def search(self, query: str, max_results: int = 3) -> str:
        """
        Execute web search for query and return text summary.

        Args:
            query: Search query string.
            max_results: Max search snippets to include.

        Returns:
            Concatenated summary string suitable for LLM context injection.
        """
        if not self.enabled:
            return "Web search is disabled in settings."

        query = query.strip()
        if not query:
            return "Empty search query."

        log.info(f"[WebSearch] Searching for: '{query}'")

        try:
            results = await asyncio.to_thread(self._sync_search, query, max_results)

            if not results:
                return f"No web search results found for '{query}'."

            snippets: list[str] = []
            for item in results:
                title = item.get("title", "").strip()
                body = item.get("body", "").strip()
                if title and body:
                    snippets.append(f"- {title}: {body}")
                elif title:
                    snippets.append(f"- {title}")
                elif body:
                    snippets.append(f"- {body}")

            summary = "\n".join(snippets)
            log.info(f"[WebSearch] Retrieved {len(snippets)} snippets for '{query}'")
            return summary

        except Exception as e:
            log.warning(f"[WebSearch] Search failed (network offline or rate limited): {e}")
            return f"Web search failed due to network connection issues ({e})."

    @staticmethod
    def _sync_search(query: str, max_results: int = 3) -> list[dict[str, str]]:
        """Synchronous search scraper using DuckDuckGo Lite with fallback."""
        import httpx
        from lxml.html import document_fromstring

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        }

        # Strategy 1: DuckDuckGo Lite (fastest, plain HTML table structure)
        try:
            with httpx.Client(headers=headers, follow_redirects=True, timeout=8.0) as client:
                resp = client.post("https://lite.duckduckgo.com/lite/", data={"q": query})
                if resp.status_code == 200 and resp.text:
                    tree = document_fromstring(resp.text)
                    rows = tree.xpath("//table[last()]//tr")
                    results: list[dict[str, str]] = []
                    for tr in rows:
                        link = tr.xpath('.//a[@class="result-link"]')
                        snippet = tr.xpath('.//td[@class="result-snippet"]')
                        if link:
                            title = "".join(link[0].xpath(".//text()")).strip()
                            url = link[0].get("href", "")
                            results.append({"title": title, "url": url, "body": ""})
                        elif snippet and results:
                            results[-1]["body"] = "".join(snippet[0].xpath(".//text()")).strip()

                    valid_results = [r for r in results if r.get("title") and r.get("body")]
                    if valid_results:
                        return valid_results[:max_results]
        except Exception as e:
            log.debug(f"[WebSearch] DuckDuckGo Lite search error: {e}")

        # Strategy 2: DuckDuckGo HTML endpoint fallback
        try:
            with httpx.Client(headers=headers, follow_redirects=True, timeout=8.0) as client:
                resp = client.post("https://html.duckduckgo.com/html/", data={"q": query})
                if resp.status_code == 200 and resp.text:
                    tree = document_fromstring(resp.text)
                    results = []
                    for r in tree.xpath('//div[contains(@class, "result__body")]'):
                        title = "".join(r.xpath('.//a[contains(@class, "result__a")]//text()')).strip()
                        snippet = "".join(r.xpath('.//a[contains(@class, "result__snippet")]//text()')).strip()
                        if title and snippet:
                            results.append({"title": title, "body": snippet})
                        if len(results) >= max_results:
                            break
                    if results:
                        return results
        except Exception as e:
            log.debug(f"[WebSearch] DuckDuckGo HTML search error: {e}")

        return []
