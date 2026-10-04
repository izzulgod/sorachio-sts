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
        """Synchronous multi-engine search with ISP-resilient fallbacks.

        Engines tried in order:
        1. Bing Search (primary, highly reliable & unblocked across ISPs)
        2. Google News RSS (ideal for current affairs & latest news queries)
        3. DuckDuckGo Lite & HTML (fallback)
        4. Wikipedia Search API (knowledge fallback)
        """
        import urllib.request
        import urllib.parse
        from lxml.html import document_fromstring
        import xml.etree.ElementTree as ET

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        }

        # Strategy 1: Bing Search (unblocked & comprehensive snippets)
        try:
            encoded_q = urllib.parse.quote_plus(query)
            req = urllib.request.Request(f"https://www.bing.com/search?q={encoded_q}", headers=headers)
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                tree = document_fromstring(resp.read().decode("utf-8", errors="ignore"))
                results: list[dict[str, str]] = []
                for li in tree.xpath('//li[contains(@class, "b_algo")]'):
                    h2 = li.xpath('.//h2//a')
                    snippet = li.xpath('.//div[contains(@class, "b_caption")]//p') or li.xpath('.//p')
                    if h2 and snippet:
                        title = "".join(h2[0].xpath(".//text()")).strip()
                        desc = "".join(snippet[0].xpath(".//text()")).strip()
                        if title and desc:
                            results.append({"title": title, "body": desc})
                    if len(results) >= max_results:
                        break
                if results:
                    return results
        except Exception as e:
            log.debug(f"[WebSearch] Bing search failed: {e}")

        # Strategy 2: Google News RSS (superb for live situations, news, and current events)
        try:
            encoded_q = urllib.parse.quote_plus(query)
            url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"
            req = urllib.request.Request(url, headers={"User-Agent": headers["User-Agent"]})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                tree = ET.fromstring(resp.read())
                items = tree.findall(".//item")[:max_results]
                results = []
                for item in items:
                    title_elem = item.find("title")
                    pub_elem = item.find("pubDate")
                    if title_elem is not None and title_elem.text:
                        title = title_elem.text.strip()
                        date_str = f" ({pub_elem.text.strip()})" if pub_elem is not None and pub_elem.text else ""
                        results.append({"title": title, "body": f"Published: {date_str}" if date_str else title})
                if results:
                    return results
        except Exception as e:
            log.debug(f"[WebSearch] Google News RSS failed: {e}")

        # Strategy 3: DuckDuckGo Lite & HTML
        try:
            import httpx
            with httpx.Client(headers=headers, follow_redirects=True, timeout=5.0) as client:
                resp = client.post("https://lite.duckduckgo.com/lite/", data={"q": query})
                if resp.status_code == 200 and resp.text:
                    tree = document_fromstring(resp.text)
                    rows = tree.xpath("//table[last()]//tr")
                    ddg_results: list[dict[str, str]] = []
                    for tr in rows:
                        link = tr.xpath('.//a[@class="result-link"]')
                        snippet = tr.xpath('.//td[@class="result-snippet"]')
                        if link:
                            title = "".join(link[0].xpath(".//text()")).strip()
                            ddg_results.append({"title": title, "body": ""})
                        elif snippet and ddg_results:
                            ddg_results[-1]["body"] = "".join(snippet[0].xpath(".//text()")).strip()

                    valid = [r for r in ddg_results if r.get("title") and r.get("body")]
                    if valid:
                        return valid[:max_results]
        except Exception as e:
            log.debug(f"[WebSearch] DuckDuckGo search error: {e}")

        # Strategy 4: Wikipedia Search API fallback
        try:
            import json
            encoded_q = urllib.parse.quote_plus(query)
            wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded_q}&format=json"
            req = urllib.request.Request(wiki_url, headers={"User-Agent": headers["User-Agent"]})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                search_hits = data.get("query", {}).get("search", [])
                wiki_results = []
                import re
                for hit in search_hits[:max_results]:
                    title = hit.get("title", "")
                    snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", "")).strip()
                    if title and snippet:
                        wiki_results.append({"title": title, "body": snippet})
                if wiki_results:
                    return wiki_results
        except Exception as e:
            log.debug(f"[WebSearch] Wikipedia search failed: {e}")

        return []
