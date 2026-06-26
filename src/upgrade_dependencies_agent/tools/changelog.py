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
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from ..core.types import ToolImpl, ToolResult

__all__ = ["FetchReleases", "FetchUrl"]

_MAX_BYTES = 50_000  # Cap returned content so one fetch can't blow the context.
_REQUEST_TIMEOUT = 30.0  # seconds


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
        return ToolResult(
            output=output or "No releases found.",
            metadata={"count": len(releases[:limit])},
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

        try:
            r = httpx.get(
                url,
                headers={"User-Agent": "upgrade-dependencies-agent/0.1"},
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            return ToolResult(output=f"Request failed: {e}", is_error=True)

        if r.status_code != 200:
            return ToolResult(output=f"HTTP {r.status_code} for {url}", is_error=True)

        content_type = r.headers.get("content-type", "")

        if "text/html" in content_type:
            text = _html_to_text(r.text)
        elif any(
            t in content_type
            for t in ("text/plain", "application/json", "text/markdown", "text/x-markdown")
        ):
            text = r.text
        else:
            return ToolResult(
                output=f"Cannot read content-type '{content_type}'. "
                f"Body is {len(r.content)} bytes of binary/non-text data.",
                is_error=True,
            )

        if len(text) > _MAX_BYTES:
            text = text[:_MAX_BYTES] + "\n\n... (truncated)"

        return ToolResult(
            output=text or "(empty response)",
            metadata={"url": url, "content_type": content_type, "bytes": len(r.content)},
        )
