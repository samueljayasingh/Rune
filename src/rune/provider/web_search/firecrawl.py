"""Firecrawl web search provider."""

from typing import TYPE_CHECKING
import httpx

from .base import WebSearchProvider, SearchResult

if TYPE_CHECKING:
    from rune.utils.config import Config


class FirecrawlSearchProvider(WebSearchProvider):
    """Web search provider using Firecrawl's /search API."""

    BASE_URL = "https://api.firecrawl.dev/v1/search"

    def __init__(self, config: "Config"):
        """Initialize Firecrawl provider."""
        self.api_key = config.websearch.api_key

    async def search(self, query: str) -> list[SearchResult]:
        """Search the web using Firecrawl's API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "limit": 10},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("data", []):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                )
            )

        return results
