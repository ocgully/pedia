"""Website crawler for `pedia backfill --url <seed>`.

Stdlib-only (urllib.request + html.parser). Same-origin BFS with a
depth limit, robots.txt honored, HTML stripped to a tinyweight markdown
representation. Designed to be enough to ingest a docs site into
`.pedia/docs/imported/`, not to be a general-purpose web scraper.

Configuration knobs are module-level constants so tests can monkey
patch them.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


USER_AGENT = "pedia-backfill/0.2.0"
DEFAULT_TIMEOUT_S = 10
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_DEPTH = 3


# ---------------------------------------------------------------------------
# HTML -> markdown (tinyweight)
# ---------------------------------------------------------------------------


_SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript", "form", "svg"}
_MAIN_TAGS = ("main", "article")
_BLOCK_TAGS = {"p", "div", "section", "li", "ul", "ol", "pre", "blockquote", "tr", "td", "th"}


class _Extractor(HTMLParser):
    """Pulls visible text + headings + links from the first <main>/<article>
    region, falling back to the document body.

    Emits a tiny markdown-ish string (headings with #, paragraphs
    separated by blank lines, links as `[text](href)`, list items with
    `- ` prefix, code fences for <pre>).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # buffer for the active region; we keep separate buffers for main/article vs body
        self._main_chunks: List[str] = []
        self._body_chunks: List[str] = []
        # stacks of currently open tags
        self._skip_stack: List[str] = []
        self._in_main: int = 0           # depth of nested main/article tags
        self._in_body: int = 0
        self._in_pre: int = 0
        self._in_code: int = 0
        self._in_li: int = 0
        self._in_ol: int = 0
        self._current_heading: Optional[int] = None  # 1..6 when inside <hN>
        self._href_stack: List[str] = []   # stack of open <a href="...">; we only support one at a time
        self._link_text_buf: Optional[List[str]] = None
        self.title: Optional[str] = None
        self._in_title = False

    # -- helpers ---
    def _append(self, s: str) -> None:
        if self._skip_stack:
            return
        if self._in_main > 0:
            self._main_chunks.append(s)
        elif self._in_body > 0:
            self._body_chunks.append(s)
        else:
            # pre-body content (rare); ignore
            pass

    def _start_block(self) -> None:
        # ensure blank separation between blocks
        self._append("\n\n")

    # -- HTMLParser overrides ---
    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        attrd = dict(attrs)
        if t == "title":
            self._in_title = True
            return
        if t == "body":
            self._in_body += 1
            return
        if t in _MAIN_TAGS:
            self._in_main += 1
            self._start_block()
            return
        if t in _SKIP_TAGS:
            self._skip_stack.append(t)
            return
        if t == "br":
            self._append("  \n")
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._current_heading = int(t[1])
            self._start_block()
            self._append("#" * self._current_heading + " ")
            return
        if t in _BLOCK_TAGS:
            self._start_block()
            if t == "li":
                self._in_li += 1
                prefix = "1. " if self._in_ol > 0 else "- "
                self._append(prefix)
            elif t == "ol":
                self._in_ol += 1
            elif t == "pre":
                self._in_pre += 1
                self._append("```\n")
            return
        if t == "code" and self._in_pre == 0:
            self._in_code += 1
            self._append("`")
            return
        if t == "a":
            href = attrd.get("href") or ""
            self._href_stack.append(href)
            self._link_text_buf = []
            return

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = False
            return
        if t == "body":
            self._in_body = max(0, self._in_body - 1)
            return
        if t in _MAIN_TAGS:
            self._in_main = max(0, self._in_main - 1)
            self._append("\n")
            return
        if self._skip_stack and self._skip_stack[-1] == t:
            self._skip_stack.pop()
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._current_heading = None
            self._append("\n")
            return
        if t in _BLOCK_TAGS:
            if t == "li":
                self._in_li = max(0, self._in_li - 1)
                self._append("\n")
            elif t == "ol":
                self._in_ol = max(0, self._in_ol - 1)
            elif t == "pre":
                self._in_pre = max(0, self._in_pre - 1)
                self._append("\n```\n")
            else:
                self._append("\n")
            return
        if t == "code" and self._in_pre == 0:
            self._in_code = max(0, self._in_code - 1)
            self._append("`")
            return
        if t == "a":
            href = self._href_stack.pop() if self._href_stack else ""
            text = "".join(self._link_text_buf or []).strip()
            self._link_text_buf = None
            if text:
                if href:
                    self._append(f"[{text}]({href})")
                else:
                    self._append(text)
            return

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            self.title = data.strip() or None
            return
        if self._skip_stack:
            return
        if self._link_text_buf is not None:
            self._link_text_buf.append(data)
            return
        self._append(data)

    # -- output ---
    def result(self) -> str:
        chunks = self._main_chunks if self._main_chunks else self._body_chunks
        raw = "".join(chunks)
        # collapse 3+ newlines to exactly 2; strip trailing whitespace per line
        lines = [ln.rstrip() for ln in raw.splitlines()]
        out: List[str] = []
        blank_run = 0
        for ln in lines:
            if ln.strip() == "":
                blank_run += 1
                if blank_run <= 1:
                    out.append("")
            else:
                blank_run = 0
                out.append(ln)
        return "\n".join(out).strip() + "\n"


def html_to_markdown(html_text: str) -> Tuple[str, Optional[str]]:
    """Convert an HTML document to markdown-ish text.

    Returns (markdown, title). `title` is the `<title>` text if present.
    """
    ex = _Extractor()
    try:
        ex.feed(html_text)
        ex.close()
    except Exception:
        # HTMLParser is lenient, but guard anyway; fall through with what we have
        pass
    return ex.result(), ex.title


# ---------------------------------------------------------------------------
# crawler
# ---------------------------------------------------------------------------


@dataclass
class CrawlPage:
    url: str
    title: Optional[str]
    markdown: str
    depth: int


@dataclass
class CrawlResult:
    pages: List[CrawlPage] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (url, reason)


def _same_origin(a: str, b: str) -> bool:
    pa = urllib.parse.urlparse(a)
    pb = urllib.parse.urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _normalize(url: str) -> str:
    # drop fragments; keep query strings (docs sites sometimes version via ?v=...)
    parts = urllib.parse.urlparse(url)
    parts = parts._replace(fragment="")
    return urllib.parse.urlunparse(parts)


_A_HREF_RE = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_links(html_text: str, base_url: str) -> List[str]:
    found: List[str] = []
    for m in _A_HREF_RE.finditer(html_text):
        raw = html.unescape(m.group(1)).strip()
        if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        abs_url = urllib.parse.urljoin(base_url, raw)
        if abs_url.startswith(("http://", "https://")):
            found.append(_normalize(abs_url))
    return found


def _make_robot_parser(seed: str, opener: urllib.request.OpenerDirector) -> Optional[urllib.robotparser.RobotFileParser]:
    parts = urllib.parse.urlparse(seed)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with opener.open(req, timeout=DEFAULT_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        rp.parse(text.splitlines())
        return rp
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError):
        # no robots.txt (or fetch failed) -> treat as "no restrictions"
        return None


def _fetch(url: str, opener: urllib.request.OpenerDirector, timeout: int) -> Optional[Tuple[str, str]]:
    """Return (content_type, body_text) or None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "") or ""
            raw = resp.read()
            # if the server didn't say, guess utf-8
            charset = "utf-8"
            m = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype, re.IGNORECASE)
            if m:
                charset = m.group(1)
            text = raw.decode(charset, errors="replace")
            return ctype, text
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError):
        return None


def crawl(
    seed_url: str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    obey_robots: bool = True,
    opener: Optional[urllib.request.OpenerDirector] = None,
) -> CrawlResult:
    """BFS-crawl from `seed_url`, same origin only, stdlib only.

    `opener` is injectable for tests (so they can avoid real HTTP).
    """
    seed_url = _normalize(seed_url)
    opener = opener or urllib.request.build_opener()
    result = CrawlResult()

    robots = _make_robot_parser(seed_url, opener) if obey_robots else None

    def allowed(url: str) -> bool:
        if robots is None:
            return True
        try:
            return robots.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    queue: List[Tuple[str, int]] = [(seed_url, 0)]
    seen: Set[str] = {seed_url}

    while queue and len(result.pages) < max_pages:
        url, depth = queue.pop(0)
        if not allowed(url):
            result.skipped.append((url, "robots.txt disallow"))
            continue
        fetched = _fetch(url, opener, timeout_s)
        if fetched is None:
            result.skipped.append((url, "fetch-failed"))
            continue
        ctype, body = fetched
        if "html" not in ctype.lower() and not body.lstrip().lower().startswith(("<!doctype", "<html")):
            # binary / non-HTML; skip
            result.skipped.append((url, f"non-html content-type: {ctype}"))
            continue
        md, title = html_to_markdown(body)
        result.pages.append(CrawlPage(url=url, title=title, markdown=md, depth=depth))

        if depth < max_depth:
            for link in _extract_links(body, url):
                if link in seen:
                    continue
                if not _same_origin(seed_url, link):
                    continue
                seen.add(link)
                queue.append((link, depth + 1))

    return result


# ---------------------------------------------------------------------------
# crawl -> IngestItem projection
# ---------------------------------------------------------------------------


def _slug_from_url(url: str) -> str:
    parts = urllib.parse.urlparse(url)
    path = (parts.path or "/").rstrip("/")
    if path in ("", "/"):
        stem = parts.netloc or "index"
    else:
        stem = path.strip("/").replace("/", "-")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    return slug or "page"


def pages_to_markdown_docs(pages: List[CrawlPage]) -> List[Tuple[str, str]]:
    """Return (dest_subpath, body_markdown) pairs relative to `.pedia/`.

    Each page becomes `.pedia/docs/imported/<slug>.md`. Duplicate slugs
    get a numeric suffix so the output is deterministic.
    """
    out: List[Tuple[str, str]] = []
    used: Dict[str, int] = {}
    for p in pages:
        base = _slug_from_url(p.url)
        count = used.get(base, 0)
        used[base] = count + 1
        slug = base if count == 0 else f"{base}-{count}"
        title_line = f"# {p.title or base}\n\n" if not p.markdown.lstrip().startswith("#") else ""
        source_line = f"<!-- source: {p.url} -->\n\n"
        body = source_line + title_line + p.markdown
        out.append((f"docs/imported/{slug}.md", body))
    return out
