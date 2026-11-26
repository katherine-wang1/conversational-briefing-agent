"""RSS <source> element (Google News style): outlet captured into extra, " - Outlet" title suffix dropped."""

from datetime import UTC, datetime

from app.sources import rss

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"AI" - Google News</title>
<item>
  <title>Northwind unveils Kestrel-9 model - Reuters</title>
  <link>https://news.google.com/rss/articles/CBMiABC?oc=5</link>
  <guid isPermaLink="false">CBMiABC</guid>
  <pubDate>Wed, 26 Nov 2026 09:00:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/x"&gt;Northwind unveils Kestrel-9&lt;/a&gt;
    &lt;font color="#6f6f6f"&gt;Reuters&lt;/font&gt;</description>
  <source url="https://www.reuters.com">Reuters</source>
</item>
<item>
  <title>A plain item without a source</title>
  <link>https://example.com/plain</link>
</item>
</channel></rss>"""


def test_google_news_source_element():
    gn, plain = rss.parse_feed(FEED, source_id="gn_ai", beat="ai", observed_at=NOW)
    assert gn.title == "Northwind unveils Kestrel-9 model"
    assert gn.extra == {"feed_position": 1, "outlet": "Reuters", "outlet_url": "https://www.reuters.com"}
    assert gn.published_at == datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    assert plain.title == "A plain item without a source"
    assert plain.extra == {"feed_position": 2}  # unchanged for feeds without <source>
