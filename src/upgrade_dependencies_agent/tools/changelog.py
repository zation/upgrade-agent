"""Changelog / release-notes tools.

The agent needs to read breaking-change documentation to plan upgrades
intelligently. These tools fetch release notes from GitHub releases and npm
package pages, and can also read arbitrary web pages (e.g. migration guides).

Design:
- ``fetch_releases`` queries the GitHub API for release notes of a repo.
- ``fetch_url`` reads any URL and returns stripped text content.
- All calls are subject to a timeout and size cap to protect the context budget.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from ..core.types import ToolImpl, ToolResult

__all__ = ["FetchReleases", "FetchUrl", "RetrieveSourceChunks"]

_MAX_BYTES = 50_000  # Cap returned content so one fetch can't blow the context.
_REQUEST_TIMEOUT = 30.0  # seconds
_DEFAULT_RETRIEVAL_KEYWORDS = [
    "breaking",
    "removed",
    "deprecated",
    "esm",
    "cjs",
    "commonjs",
    "node minimum",
    "peer dependency",
    "cli",
    "config",
]
_FETCH_CACHE_TTL_SECONDS = 60 * 30
_FETCH_CACHE: dict[str, _FetchedSource] = {}
_RELEASES_CACHE: dict[tuple[str, str, str | None, int], tuple[str, dict[str, Any], float]] = {}


@dataclass(frozen=True)
class _FetchedSource:
    url: str
    final_url: str
    content_type: str
    text: str
    bytes_read: int
    fetched_at: float


# --------------------------------------------------------------------------- #
# Lightweight HTML → text
# --------------------------------------------------------------------------- #
class _TextExtractor(HTMLParser):
    """Pull visible text from HTML, collapsing whitespace."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False
        # Add line breaks for block-level elements so the output is readable.
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        text = " ".join(self._parts)
        # Collapse repeated whitespace / newlines.
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return p.get_text()


# --------------------------------------------------------------------------- #
# GitHub Release fetcher
# --------------------------------------------------------------------------- #
class FetchReleases(ToolImpl):
    name = "fetch_releases"
    description = (
        "Fetch release notes for a GitHub repository. Returns releases with "
        "their tag names, publish dates, and body text (HTML stripped). "
        "Use 'tag' to fetch one specific release (e.g. 'v5.0.0'), or omit it "
        "to get the most recent N releases. "
        "Example: owner='chaijs', repo='chai', tag='v5.0.0' fetches chai's "
        "5.0.0 release notes specifically."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub org or user, e.g. 'chaijs'."},
            "repo": {"type": "string", "description": "Repository name, e.g. 'chai'."},
            "tag": {
                "type": "string",
                "description": (
                    "Optional: a specific git tag, e.g. 'v5.0.0'. "
                    "If set, returns only that release."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max releases to return when tag is not set. Default 5.",
            },
        },
        "required": ["owner", "repo"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        owner = args["owner"]
        repo = args["repo"]
        tag = args.get("tag")
        limit = args.get("limit", 5)
        cache_key = (owner, repo, tag, limit)
        cached = _RELEASES_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[2] <= _FETCH_CACHE_TTL_SECONDS:
            output, metadata, _fetched_at = cached
            return ToolResult(output=output, metadata={**metadata, "cache_hit": True})

        if tag:
            url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
        else:
            url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page={limit}"
        try:
            r = httpx.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "upgrade-dependencies-agent",
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as e:
            return ToolResult(output=f"GitHub API request failed: {e}", is_error=True)

        if r.status_code == 403 and "rate limit" in r.text.lower():
            return ToolResult(
                output="GitHub API rate limit exceeded. Try again later or use fetch_url "
                "to read the releases page directly.",
                is_error=True,
            )
        if r.status_code != 200:
            return ToolResult(output=f"GitHub API returned HTTP {r.status_code}.", is_error=True)

        try:
            releases_raw = r.json()
        except ValueError:
            return ToolResult(output="GitHub API returned non-JSON.", is_error=True)

        if tag:
            # Single-release fetch returns a dict, not a list.
            releases = [releases_raw] if isinstance(releases_raw, dict) else releases_raw
        else:
            releases = releases_raw

        if not isinstance(releases, list):
            return ToolResult(output="Unexpected GitHub API response format.", is_error=True)

        lines: list[str] = []
        for rel in releases[:limit]:
            tag = rel.get("tag_name", "?")
            name = rel.get("name", tag)
            published = rel.get("published_at", "")[:10]
            body = rel.get("body", "")
            # Strip HTML from the release body for readability.
            clean_body = _html_to_text(body)
            # Truncate per-release body to keep total output bounded.
            if len(clean_body) > 3000:
                clean_body = clean_body[:3000] + "\n... (truncated)"
            lines.append(f"## {name} ({tag}) — {published}\n{clean_body}")

        output = "\n\n---\n\n".join(lines)
        if len(output) > _MAX_BYTES:
            output = output[:_MAX_BYTES] + "\n\n... (output truncated)"
        metadata = {"count": len(releases[:limit]), "cache_hit": False}
        _RELEASES_CACHE[cache_key] = (output or "No releases found.", metadata, now)
        return ToolResult(
            output=output or "No releases found.",
            metadata=metadata,
        )


# --------------------------------------------------------------------------- #
# Generic URL fetcher
# --------------------------------------------------------------------------- #
class FetchUrl(ToolImpl):
    name = "fetch_url"
    description = (
        "Fetch the content of any URL — changelogs, migration guides, npm docs. "
        "HTML pages are stripped to plain text. Binary/non-HTML content returns "
        "a short description. Use this to read breaking-change documentation "
        "that npm_releases and npm_view don't cover."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to fetch."},
        },
        "required": ["url"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        url = args["url"]
        # Basic URL validation.
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult(output=f"Unsupported URL scheme: {parsed.scheme}", is_error=True)

        fetched, cache_hit, error = _fetch_text_source(url)
        if error:
            return ToolResult(output=error, is_error=True)
        assert fetched is not None
        text = fetched.text

        if _looks_like_long_changelog(url, text):
            text = _summarize_long_changelog(text)
        elif len(text) > _MAX_BYTES:
            text = text[:_MAX_BYTES] + "\n\n... (truncated)"

        return ToolResult(
            output=text or "(empty response)",
            metadata={
                "url": url,
                "final_url": fetched.final_url,
                "content_type": fetched.content_type,
                "bytes": fetched.bytes_read,
                "cache_hit": cache_hit,
            },
        )


class RetrieveSourceChunks(ToolImpl):
    name = "retrieve_source_chunks"
    description = (
        "Fetch a changelog, release notes, migration guide, or docs URL, then "
        "split it into heading-based chunks and return the chunks most relevant "
        "to dependency-upgrade risks. Use this after dependency_research finds "
        "candidate sources so research can cite the specific source text it read."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to fetch and retrieve from."},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional keywords or phrases to rank by. Defaults to breaking, "
                    "removed, deprecated, ESM/CJS, Node minimum, peers, CLI, config."
                ),
            },
            "max_chunks": {
                "type": "integer",
                "description": "Maximum ranked chunks to return. Default 5.",
            },
        },
        "required": ["url"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        url = args["url"]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult(output=f"Unsupported URL scheme: {parsed.scheme}", is_error=True)

        fetched, cache_hit, error = _fetch_text_source(url)
        if error:
            return ToolResult(output=error, is_error=True)
        assert fetched is not None

        keywords = [
            str(keyword).strip().lower()
            for keyword in args.get("keywords", _DEFAULT_RETRIEVAL_KEYWORDS)
            if str(keyword).strip()
        ]
        max_chunks = max(1, int(args.get("max_chunks", 5)))
        chunks = _rank_chunks(_chunk_text_by_heading(fetched.text), keywords)[:max_chunks]
        data = {
            "source": url,
            "final_url": fetched.final_url,
            "content_type": fetched.content_type,
            "cache_hit": cache_hit,
            "keywords": keywords,
            "chunks": chunks,
            "source_gap": None if chunks else "No chunks matched the requested keywords.",
        }
        return ToolResult(
            output=_json_dumps(data),
            metadata={
                "url": url,
                "final_url": fetched.final_url,
                "cache_hit": cache_hit,
                "chunk_count": len(chunks),
            },
        )


def _fetch_text_source(url: str) -> tuple[_FetchedSource | None, bool, str | None]:
    cached = _FETCH_CACHE.get(url)
    now = time.monotonic()
    if cached and now - cached.fetched_at <= _FETCH_CACHE_TTL_SECONDS:
        return cached, True, None

    try:
        r = httpx.get(
            url,
            headers={"User-Agent": "upgrade-dependencies-agent/0.1"},
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        return None, False, f"Request failed: {e}"

    if r.status_code != 200:
        return None, False, f"HTTP {r.status_code} for {url}"

    content_type = r.headers.get("content-type", "")
    if "text/html" in content_type:
        text = _html_to_text(r.text)
    elif any(
        t in content_type
        for t in ("text/plain", "application/json", "text/markdown", "text/x-markdown")
    ):
        text = r.text
    else:
        return (
            None,
            False,
            f"Cannot read content-type '{content_type}'. "
            f"Body is {len(r.content)} bytes of binary/non-text data.",
        )

    fetched = _FetchedSource(
        url=url,
        final_url=str(getattr(r, "url", url)),
        content_type=content_type,
        text=text,
        bytes_read=len(r.content),
        fetched_at=now,
    )
    _FETCH_CACHE[url] = fetched
    return fetched, False, None


def _chunk_text_by_heading(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    chunks: list[dict[str, str]] = []
    current_heading = "document"
    current_lines: list[str] = []
    for line in lines:
        heading = _markdown_heading(line)
        if heading:
            if current_lines:
                chunks.append(
                    {
                        "heading": current_heading,
                        "text": "\n".join(current_lines).strip(),
                    }
                )
            current_heading = heading
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append({"heading": current_heading, "text": "\n".join(current_lines).strip()})
    return [chunk for chunk in chunks if chunk["text"]]


def _markdown_heading(line: str) -> str | None:
    match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
    if not match:
        return None
    return match.group(1).strip()


def _rank_chunks(chunks: list[dict[str, str]], keywords: list[str]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        searchable = f"{chunk['heading']}\n{chunk['text']}".lower()
        matched = [keyword for keyword in keywords if keyword in searchable]
        score = sum(searchable.count(keyword) for keyword in matched)
        if score == 0:
            continue
        ranked.append(
            {
                "heading": chunk["heading"],
                "score": score,
                "matched_keywords": matched,
                "text": _truncate_chunk(chunk["text"]),
                "_index": index,
            }
        )
    ranked.sort(key=lambda chunk: (-chunk["score"], chunk["_index"]))
    for chunk in ranked:
        del chunk["_index"]
    return ranked


def _truncate_chunk(text: str) -> str:
    if len(text) <= 1200:
        return text
    return text[:1200] + "\n... (chunk truncated)"


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, indent=2)


def _looks_like_long_changelog(url: str, text: str) -> bool:
    lower_url = url.lower()
    if not any(marker in lower_url for marker in ("changelog", "release", "migration")):
        return False
    heading_count = len(re.findall(r"(?m)^#{1,3}\s+", text))
    return len(text) > 1500 or heading_count > 30


def _summarize_long_changelog(text: str) -> str:
    lines = text.splitlines()
    headings = [line for line in lines if re.match(r"^#{1,3}\s+", line)]
    important = [
        line
        for line in lines
        if re.search(
            r"\b(breaking|removed|deprecated|migration|esm|commonjs|cjs|node|peer)\b",
            line,
            re.IGNORECASE,
        )
    ]
    recent_headings = "\n".join(headings[:30])
    important_lines = "\n".join(important[:40])
    tail = "\n".join(lines[-80:])[-1800:]
    return (
        "long changelog summary\n"
        "Recent headings:\n"
        f"{recent_headings or '(none found)'}\n\n"
        "Important lines:\n"
        f"{important_lines or '(none found)'}\n\n"
        "Tail:\n"
        f"{tail}\n"
        "Fetch a more specific URL or release tag if a focused section is needed."
    )
