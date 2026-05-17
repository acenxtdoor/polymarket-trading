from dataclasses import dataclass
from datetime import datetime, timedelta
import requests
import config


@dataclass
class NewsArticle:
    title: str
    description: str
    source: str
    published_at: str
    url: str

    def to_text(self) -> str:
        return f"[{self.source}] {self.title}: {self.description}"


class NewsAPIClient:
    BASE_URL = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or config.NEWSAPI_KEY

    def fetch_articles(self, query: str, max_articles: int = 5) -> list[NewsArticle]:
        if not self.api_key:
            return []

        from_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "relevancy",
            "pageSize": max_articles,
            "language": "en",
            "apiKey": self.api_key,
        }

        try:
            resp = requests.get(
                self.BASE_URL, params=params, timeout=config.REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        articles = []
        for item in data.get("articles", [])[:max_articles]:
            articles.append(
                NewsArticle(
                    title=item.get("title") or "",
                    description=item.get("description") or "",
                    source=(item.get("source") or {}).get("name", "Unknown"),
                    published_at=item.get("publishedAt", ""),
                    url=item.get("url", ""),
                )
            )
        return articles


def fetch_market_news(market_title: str, max_articles: int = None) -> list[NewsArticle]:
    n = max_articles if max_articles is not None else config.NEWS_MAX_ARTICLES
    return NewsAPIClient().fetch_articles(market_title, n)
