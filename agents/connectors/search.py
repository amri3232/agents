"""Tavily web search, as a callable client.

This module used to run a search at import time with a hardcoded query
("Will Biden drop out of the race?"), so merely importing it fired a network
call and spent API credits, and it exposed no reusable function. It also read
`OPEN_API_KEY`, a typo for `OPENAI_API_KEY`.
"""

import os

from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()


class Search:
    def __init__(self) -> None:
        self.api_key = os.getenv("TAVILY_API_KEY")
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self) -> TavilyClient:
        if self._client is None:
            if not self.available:
                raise RuntimeError("TAVILY_API_KEY is not set")
            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    def get_context(self, query: str, max_results: int = 5) -> str:
        """Search context for `query`, as a string ready to drop into a prompt."""
        return self.client.get_search_context(query=query, max_results=max_results)
