import pytest

from wakil.integrations import web

ARTICLE_HTML = """
<html>
  <head><title>How Graph Memory Helps Claims Routing | Example Blog</title></head>
  <body>
    <nav><a href="/">Home</a><a href="/about">About</a></nav>
    <article>
      <h1>How Graph Memory Helps Claims Routing</h1>
      <p>Graph memory lets a routing system remember relationships between
      claims, people, and decisions.</p>
      <p>Teams using it report faster FNOL triage.</p>
      <script>trackPageView();</script>
    </article>
    <footer>Copyright Example Blog</footer>
  </body>
</html>
"""


def test_extract_article_pulls_title_and_body():
    article = web.extract_article("https://example.com/post", ARTICLE_HTML)
    assert "Graph Memory" in article.title
    assert "faster FNOL triage" in article.text
    assert "trackPageView" not in article.text


def test_extract_article_falls_back_to_full_page_text():
    article = web.extract_article("https://example.com/x", "<html><body>tiny</body></html>")
    assert article.text == "tiny"


def test_fetch_article_raises_on_http_error(monkeypatch):
    class FakeResponse:
        status_code = 404
        content = b""
        text = ""

    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: FakeResponse())
    with pytest.raises(web.FetchError, match="HTTP 404"):
        web.fetch_article("https://example.com/missing")


def test_fetch_article_extracts(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = ARTICLE_HTML.encode()
        text = ARTICLE_HTML

    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: FakeResponse())
    article = web.fetch_article("https://example.com/post")
    assert "Graph Memory" in article.title
