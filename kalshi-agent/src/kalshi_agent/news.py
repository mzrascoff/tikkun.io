"""News pressure analysis from public RSS feeds.

The agent reads public RSS feeds from the New York Times, Wall Street
Journal, and Financial Times every morning. These feeds contain
headlines + abstracts, not full article text — FT/NYT/WSJ articles
themselves are paywalled. Headlines are still informative for detecting
when a thesis is moving in the news.

For each opportunity in the daily scan, the agent counts how many
fresh (last 48h) headlines match the series' keyword set. If the count
is non-zero, the prior's confidence is reduced (a noisy news cycle is
not the time to fade a market with a static base rate), and the top 3
matching headlines are attached to the opportunity for inclusion in
the report and email.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

# Public RSS feeds. These do not require auth and serve headlines +
# abstracts only. URLs verified as of late 2024 / early 2025; they
# occasionally rotate, so failures are non-fatal — the agent simply
# skips a feed it can't fetch.
DEFAULT_FEEDS: tuple[tuple[str, str], ...] = (
    # New York Times
    ("NYT World",      "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("NYT Science",    "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml"),
    ("NYT Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("NYT Business",   "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("NYT US",         "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    # Wall Street Journal
    ("WSJ World",      "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("WSJ Markets",    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("WSJ Business",   "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("WSJ Tech",       "https://feeds.a.dj.com/rss/RSSWSJD.xml"),
    # Financial Times. FT's free RSS coverage is limited but the world
    # and companies feeds tend to be reachable.
    ("FT World",       "https://www.ft.com/world?format=rss"),
    ("FT Companies",   "https://www.ft.com/companies?format=rss"),
)

DEFAULT_LOOKBACK = timedelta(hours=48)
MAX_HEADLINES_PER_OPPORTUNITY = 3
# How much to reduce prior.confidence per matching headline (capped).
CONFIDENCE_REDUCTION_PER_HIT = 0.05
MAX_CONFIDENCE_REDUCTION = 0.25


@dataclass(frozen=True)
class Headline:
    source: str
    title: str
    link: str
    published: datetime
    summary: str = ""

    def matches(self, patterns: list[re.Pattern]) -> bool:
        text = f"{self.title} {self.summary}"
        return any(p.search(text) for p in patterns)


@dataclass
class NewsContext:
    """Result of one morning's RSS sweep. Held for the whole scan run."""
    headlines: list[Headline] = field(default_factory=list)
    fetch_errors: dict[str, str] = field(default_factory=dict)

    def fresh(self, lookback: timedelta = DEFAULT_LOOKBACK) -> list[Headline]:
        cutoff = datetime.now(tz=timezone.utc) - lookback
        return [h for h in self.headlines if h.published >= cutoff]

    def matching(
        self,
        keywords: list[str],
        *,
        lookback: timedelta = DEFAULT_LOOKBACK,
    ) -> list[Headline]:
        if not keywords:
            return []
        patterns = [re.compile(k, re.IGNORECASE) for k in keywords]
        hits = [h for h in self.fresh(lookback) if h.matches(patterns)]
        # Most recent first.
        hits.sort(key=lambda h: h.published, reverse=True)
        return hits[:MAX_HEADLINES_PER_OPPORTUNITY]


def _parse_date(text: str | None) -> datetime:
    if not text:
        return datetime.now(tz=timezone.utc) - timedelta(days=365)
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc) - timedelta(days=365)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_rss(xml_text: str, source: str) -> list[Headline]:
    """Parse an RSS 2.0 or Atom feed body into Headlines."""
    out: list[Headline] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    # RSS 2.0: <rss><channel><item>...
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = _parse_date(item.findtext("pubDate"))
        desc = _strip_html(item.findtext("description") or "")
        if title:
            out.append(Headline(source=source, title=title, link=link, published=pub, summary=desc))

    # Atom: <feed><entry>...
    if not out:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            pub = _parse_date(entry.findtext("a:updated", default="", namespaces=ns))
            summary = _strip_html(entry.findtext("a:summary", default="", namespaces=ns) or "")
            if title:
                out.append(Headline(source=source, title=title, link=link, published=pub, summary=summary))

    return out


def fetch_news(
    feeds: tuple[tuple[str, str], ...] = DEFAULT_FEEDS,
    *,
    timeout: float = 10.0,
    client: httpx.Client | None = None,
) -> NewsContext:
    """Fetch all configured RSS feeds, returning a NewsContext."""
    ctx = NewsContext()
    own_client = client is None
    if own_client:
        client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": "kalshi-agent/0.1 (research; reads public RSS only)",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            follow_redirects=True,
        )
    try:
        for source, url in feeds:
            try:
                resp = client.get(url)
                if resp.status_code != 200:
                    ctx.fetch_errors[source] = f"HTTP {resp.status_code}"
                    continue
                ctx.headlines.extend(parse_rss(resp.text, source))
            except httpx.HTTPError as e:
                ctx.fetch_errors[source] = f"{type(e).__name__}: {e}"
    finally:
        if own_client:
            client.close()
    return ctx


def confidence_adjustment(num_matching_headlines: int) -> float:
    """Return the absolute amount to subtract from prior.confidence.

    A noisy news cycle is *not* the time to fade a market with a static
    base rate — even if the underlying science is sound, the *price*
    can move on flow. We therefore reduce the trade's weighting in the
    final ranker (without touching the probability) when news pressure
    is high.

    Capped at MAX_CONFIDENCE_REDUCTION so a flood of headlines can't
    drive a high-confidence prior to zero.
    """
    if num_matching_headlines <= 0:
        return 0.0
    return min(MAX_CONFIDENCE_REDUCTION, num_matching_headlines * CONFIDENCE_REDUCTION_PER_HIT)
