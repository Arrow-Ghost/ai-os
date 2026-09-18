"""
Reading the web.

Stdlib only -- urllib plus a small HTML-to-text pass. No requests, no
BeautifulSoup, nothing to install.

web.search has two backends and picks whichever is configured:

  google  Google Programmable Search (Custom Search JSON API). Best quality.
          Free tier is 100 queries/day. Needs BOTH GOOGLE_API_KEY and
          GOOGLE_CSE_ID in .env -- the key alone is not enough, because the
          API has no concept of "the whole web" without a search engine id.
          Make one free at https://programmablesearchengine.google.com/
          (create an engine, switch on "Search the entire web", copy the
          "Search engine ID").

  duckduckgo  No key, no signup, no quota. Parses the HTML results page.
          UNVERIFIED: DuckDuckGo blocks datacenter IPs outright (it serves a
          landing page instead of results), so this could not be tested from
          a sandbox. It may work fine from a home connection -- try
          `backend=duckduckgo` on your own machine. The parser logic is
          unit-tested against a markup fixture either way. Treat it as a
          bonus, not something to build a demo on.

Groq's `groq/compound` models advertise built-in web search but return 413 on
this account's tier, so they are not an option here.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from html.parser import HTMLParser

from servant.sdk import Tier, ToolError, tool

USER_AGENT = "servant/0.1 (+local agent)"
MAX_BYTES = 2_000_000
SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}
BREAK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}


class _TextExtractor(HTMLParser):
    """Pull readable text out of HTML without pulling in a dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BREAK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        # Title first: <title> sits inside <head>, which is a skipped tag, so
        # checking skip_depth before this would throw every page title away.
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self.chunks.append(text)

    def text(self) -> str:
        joined = " ".join(self.chunks)
        lines = [" ".join(line.split()) for line in joined.split("\n")]
        return "\n".join(line for line in lines if line)


@tool(
    name="web.fetch",
    tier=Tier.READ,
    params={
        "url": "Full URL, http or https",
        "max_chars": "Truncate the extracted text after this many characters",
        "raw": "True to return the raw body instead of extracted text",
    },
)
def web_fetch(ctx, url: str, max_chars: int = 6000, raw: bool = False) -> str:
    """Fetch a web page and return its readable text. Use before answering anything time-sensitive."""
    if not url.lower().startswith(("http://", "https://")):
        raise ToolError(f"url must start with http:// or https://, got {url!r}")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(MAX_BYTES)
            content_type = response.headers.get("Content-Type", "")
            status = response.status
    except urllib.error.HTTPError as exc:
        raise ToolError(f"{url} returned HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"could not reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ToolError(f"{url} timed out after 20s") from exc

    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
    text = body.decode(charset, errors="replace")

    if not raw and "html" in content_type.lower():
        parser = _TextExtractor()
        parser.feed(text)
        heading = f"# {parser.title}\n\n" if parser.title else ""
        text = heading + parser.text()

    ctx.log(f"fetched {url} ({status}, {len(body) // 1024}KB)")
    # Pages can contain anything, including keys in a code sample.
    return ctx.scrub(text[:max_chars])


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

class _DuckDuckGoParser(HTMLParser):
    """Pull (title, url, snippet) triples out of DuckDuckGo's HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._mode: str | None = None
        self._current: dict = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a" and tag != "div":
            return
        classes = dict(attrs).get("class", "") or ""
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._mode = "title"
            self._current = {"url": _clean_ddg_url(dict(attrs).get("href", "")), "title": "", "snippet": ""}
        elif "result__snippet" in classes:
            self._mode = "snippet"

    def handle_endtag(self, tag):
        if self._mode == "title" and tag == "a":
            self._mode = None
        elif self._mode == "snippet" and tag in {"a", "div"}:
            if self._current:
                self.results.append(self._current)
                self._current = {}
            self._mode = None

    def handle_data(self, data):
        if self._mode and self._current:
            self._current[self._mode if self._mode != "title" else "title"] += data.strip() + " "


def _clean_ddg_url(href: str) -> str:
    """DuckDuckGo wraps results in /l/?uddg=<encoded>. Unwrap it."""
    import urllib.parse

    if "uddg=" not in href:
        return href
    query = urllib.parse.urlparse(href).query
    target = urllib.parse.parse_qs(query).get("uddg", [""])[0]
    return target or href


def _search_google(ctx, query: str, max_results: int) -> list[dict]:
    import json
    import urllib.parse

    key, cse = ctx.secret("GOOGLE_API_KEY"), ctx.secret("GOOGLE_CSE_ID")
    params = urllib.parse.urlencode(
        {"key": key, "cx": cse, "q": query, "num": min(max_results, 10)}
    )
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read(MAX_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code == 403:
            raise ToolError(
                "Google search refused (403). Either the daily free quota (100) is used up, "
                f"or the Custom Search API is not enabled for this key. Detail: {detail}"
            ) from exc
        raise ToolError(f"Google search failed with HTTP {exc.code}: {detail}") from exc

    return [
        {"title": item.get("title", ""), "url": item.get("link", ""),
         "snippet": item.get("snippet", "")}
        for item in payload.get("items", [])[:max_results]
    ]


def _search_duckduckgo(ctx, query: str, max_results: int) -> list[dict]:  # noqa: ARG001
    import urllib.parse

    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) servant/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read(MAX_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ToolError(
            f"DuckDuckGo returned HTTP {exc.code}. It rate-limits aggressively; "
            "add GOOGLE_CSE_ID to .env for a quota-backed backend."
        ) from exc

    parser = _DuckDuckGoParser()
    parser.feed(html)

    if not parser.results and "result__a" not in html and "result-link" not in html:
        # No result markup at all means we were served the landing/block page,
        # which is a very different thing from "this query has no hits".
        raise ToolError(
            "DuckDuckGo served a page with no results in it -- it blocks "
            "datacenter IPs and non-browser clients, and it changes its markup "
            "without warning. Use the google backend instead: add GOOGLE_CSE_ID "
            "to .env (free, 1 minute, https://programmablesearchengine.google.com/)."
        )

    return [
        {k: " ".join(v.split()) for k, v in r.items()}
        for r in parser.results[:max_results]
    ]


@tool(
    name="web.search",
    tier=Tier.READ,
    params={
        "query": "What to search for",
        "max_results": "How many results to return (1-10)",
        "backend": "auto | google | duckduckgo",
    },
)
def web_search(ctx, query: str, max_results: int = 5, backend: str = "auto") -> str:
    """Search the web and return titles, URLs and snippets. Follow up with web.fetch to read one."""
    if not query.strip():
        raise ToolError("query is empty")
    max_results = max(1, min(int(max_results), 10))

    has_google = bool(ctx.secret("GOOGLE_API_KEY") and ctx.secret("GOOGLE_CSE_ID"))
    if backend == "auto":
        backend = "google" if has_google else "duckduckgo"

    if backend == "google":
        if not has_google:
            raise ToolError(
                "backend=google needs BOTH GOOGLE_API_KEY and GOOGLE_CSE_ID in .env. "
                "Make a free search engine at https://programmablesearchengine.google.com/ "
                "and copy its Search engine ID."
            )
        results = _search_google(ctx, query, max_results)
    elif backend == "duckduckgo":
        results = _search_duckduckgo(ctx, query, max_results)
    else:
        raise ToolError(f"backend must be auto, google or duckduckgo -- got {backend!r}")

    if not results:
        return f"no results for {query!r} (via {backend})"

    ctx.log(f"searched {query!r} via {backend}: {len(results)} result(s)")
    lines = [f"{len(results)} result(s) for {query!r} via {backend}:"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:300]}")
    return ctx.scrub("\n".join(lines))
