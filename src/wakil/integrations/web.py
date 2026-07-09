"""Web article fetching and text extraction."""

from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from readability import Document

USER_AGENT = "wakil/0.1 (+https://github.com/ebridges/wakil) article ingest"
MAX_BYTES = 5_000_000


class FetchError(RuntimeError):
    pass


@dataclass
class Article:
    url: str
    title: str
    text: str


def fetch_article(url: str) -> Article:
    """Fetch a URL and extract readable article text."""
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise FetchError(f"Could not fetch {url}: {exc}") from exc
    if response.status_code != 200:
        raise FetchError(f"{url} returned HTTP {response.status_code}")
    if len(response.content) > MAX_BYTES:
        raise FetchError(f"{url} response too large ({len(response.content)} bytes)")
    return extract_article(url, response.text)


def extract_article(url: str, html: str) -> Article:
    """Extract title and readable text from raw HTML."""
    document = Document(html)
    title = (document.short_title() or "").strip()
    content_html = document.summary(html_partial=True)
    text = _html_to_text(content_html)
    if not text.strip():
        text = _html_to_text(html)
    return Article(url=url, title=title or url, text=text.strip())


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text("\n").splitlines()]
    # Collapse runs of blank lines left behind by block elements.
    text_lines: list[str] = []
    for line in lines:
        if line or (text_lines and text_lines[-1]):
            text_lines.append(line)
    return "\n".join(text_lines).strip()
