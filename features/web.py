"""
Reading the web.

Stdlib only -- urllib plus a small HTML-to-text pass. No requests, no
BeautifulSoup, nothing to install.

Note on search: Groq's `groq/compound` models advertise built-in web search,
but they return 413 on this account's tier, so there is no free search here.
If you need `web.search`, add a provider (Tavily/Brave/SerpAPI), put the key
in .env, and read it with ctx.secret(). Do not scrape Google -- it breaks
within a day and gets the IP blocked.
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
