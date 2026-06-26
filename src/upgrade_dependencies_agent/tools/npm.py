"""npm tools.

Query the npm registry WITHOUT modifying anything: list outdated deps, fetch
package metadata/versions, and prepare dependency-research summaries. These are
the agent's eyes for the "research" phase — e.g. discovering chai is at v5 and
is ESM-only.

Mutating actions (``npm install``) intentionally go through ``run_command`` so
they show up in the command trace and the human can review them.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..core.types import ToolImpl, ToolResult

__all__ = ["DependencyResearch", "NpmOutdated", "NpmReleases", "NpmView"]

_REGISTRY = "https://registry.npmjs.org"


class NpmOutdated(ToolImpl):
    name = "npm_outdated"
    description = (
        "Run `npm outdated --json` in the project and return which dependencies "
        "are behind, with current/wanted/latest versions. This is the starting "
        "point for an upgrade task."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        # Reuse run_command machinery by shelling out — npm has its own auth/config.
        from .shell import RunCommand  # local import to avoid cycle at module load

        res = RunCommand().run({"command": "npm outdated --json"}, ctx)
        raw = res.output
        # npm outdated exits non-zero when deps are outdated, but still emits JSON.
        marker = raw.find("{")
        if marker == -1:
            return ToolResult(output="npm outdated produced no JSON; all deps up to date?")
        try:
            data = json.loads(raw[marker:])
        except json.JSONDecodeError:
            return res
        if not data:
            return ToolResult(output="All dependencies are up to date.")
        pretty = json.dumps(data, indent=2)
        return ToolResult(output=pretty, metadata={"count": len(data)})


class NpmView(ToolImpl):
    name = "npm_view"
    description = (
        "Fetch metadata for an npm package from the registry: latest version, "
        "all versions, description, homepage, repository. Use to learn what "
        "versions exist and where the changelog lives."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "npm package name"}},
        "required": ["name"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        name = args["name"]
        try:
            r = httpx.get(f"{_REGISTRY}/{name}", timeout=20)
        except httpx.HTTPError as e:
            return ToolResult(output=f"Registry request failed: {e}", is_error=True)
        if r.status_code == 404:
            return ToolResult(output=f"Package '{name}' not found on npm.", is_error=True)
        if r.status_code != 200:
            return ToolResult(output=f"Registry returned HTTP {r.status_code}.", is_error=True)
        try:
            data = r.json()
        except ValueError:
            return ToolResult(output="Registry returned non-JSON.", is_error=True)

        versions = list(data.get("versions", {}).keys())
        latest = data.get("dist-tags", {}).get("latest", versions[-1] if versions else "?")
        summary = {
            "name": data.get("name", name),
            "latest": latest,
            "versions_count": len(versions),
            "first_version": versions[0] if versions else None,
            "description": data.get("description", ""),
            "homepage": data.get("homepage", ""),
            "repository": _repo_url(data.get("repository")),
            "recent_versions": versions[-12:],
        }
        return ToolResult(output=json.dumps(summary, indent=2), metadata={"latest": latest})


class NpmReleases(ToolImpl):
    name = "npm_releases"
    description = (
        "List the most recent versions of a package with their publish times. "
        "Helps the agent reason about how old the project's pinned version is "
        "and which major versions exist between current and latest."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "limit": {"type": "integer", "description": "How many recent versions. Default 20."},
        },
        "required": ["name"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        name = args["name"]
        limit = args.get("limit", 20)
        try:
            r = httpx.get(f"{_REGISTRY}/{name}", timeout=20)
        except httpx.HTTPError as e:
            return ToolResult(output=f"Registry request failed: {e}", is_error=True)
        if r.status_code != 200:
            return ToolResult(output=f"Registry returned HTTP {r.status_code}.", is_error=True)
        data = r.json()
        times = data.get("time", {})
        # Drop the special non-version keys, sort by time desc.
        items = [(v, t) for v, t in times.items() if v not in ("created", "modified")]
        items.sort(key=lambda kv: kv[1], reverse=True)
        rows = [f"{v}\t{t}" for v, t in items[:limit]]
        return ToolResult(output="\n".join(rows) or "No versions found.")


class DependencyResearch(ToolImpl):
    name = "dependency_research"
    description = (
        "Research one npm dependency for an upgrade. Returns structured JSON with "
        "current/target/latest versions, major-version span, repository/homepage, "
        "candidate changelog/release URLs, and risk hints. Use before or during "
        "major upgrades to seed changelog/RAG research."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "npm package name"},
            "current": {
                "type": "string",
                "description": "Current project version/range, e.g. '^4.0.1'. Optional.",
            },
            "target": {
                "type": "string",
                "description": "Target version/range. Defaults to npm latest.",
            },
        },
        "required": ["name"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        name = args["name"]
        current = args.get("current")
        try:
            data = _fetch_package(name)
        except httpx.HTTPError as e:
            return ToolResult(output=f"Registry request failed: {e}", is_error=True)
        except RegistryError as e:
            return ToolResult(output=str(e), is_error=True)

        versions = list(data.get("versions", {}).keys())
        latest = data.get("dist-tags", {}).get("latest", versions[-1] if versions else "?")
        target = args.get("target") or latest
        repository = _repo_url(data.get("repository"))
        homepage = data.get("homepage", "")
        current_major = _major(current)
        target_major = _major(target)

        result = {
            "name": data.get("name", name),
            "current": current,
            "target": target,
            "latest": latest,
            "major_span": _major_span(current_major, target_major),
            "description": data.get("description", ""),
            "repository": repository,
            "homepage": homepage,
            "candidate_sources": _candidate_sources(repository, homepage),
            "recent_versions": versions[-12:],
            "risk_hints": _risk_hints(current_major, target_major),
        }
        return ToolResult(
            output=json.dumps(result, indent=2),
            metadata={
                "latest": latest,
                "repository": repository,
                "major_span": result["major_span"],
            },
        )


class RegistryError(Exception):
    """Registry returned a non-success response or malformed payload."""


def _fetch_package(name: str) -> dict[str, Any]:
    r = httpx.get(f"{_REGISTRY}/{name}", timeout=20)
    if r.status_code == 404:
        raise RegistryError(f"Package '{name}' not found on npm.")
    if r.status_code != 200:
        raise RegistryError(f"Registry returned HTTP {r.status_code}.")
    try:
        data = r.json()
    except ValueError as e:
        raise RegistryError("Registry returned non-JSON.") from e
    if not isinstance(data, dict):
        raise RegistryError("Registry returned unexpected JSON.")
    return data


def _repo_url(repo_field: Any) -> str:
    """Normalize npm's repository field (str | {url: str}) to a clean URL."""
    url = repo_field.get("url", "") if isinstance(repo_field, dict) else str(repo_field or "")
    return url.replace("git+", "").replace(".git", "")


def _major(version_or_range: str | None) -> int | None:
    if not version_or_range:
        return None
    match = re.search(r"\d+", version_or_range)
    return int(match.group(0)) if match else None


def _major_span(current_major: int | None, target_major: int | None) -> str:
    if current_major is None or target_major is None:
        return "unknown"
    if target_major == current_major:
        return "same-major"
    if target_major > current_major:
        return f"{current_major}->{target_major}"
    return f"downgrade? {current_major}->{target_major}"


def _risk_hints(current_major: int | None, target_major: int | None) -> list[str]:
    hints: list[str] = []
    if current_major is None or target_major is None:
        hints.append("Could not infer major-version span; read package metadata and changelog.")
    elif target_major > current_major:
        hints.append("Major-version upgrade; read every breaking-change section between majors.")
    else:
        hints.append("No major-version jump detected; still check deprecations and peer warnings.")
    hints.append(
        "Check minimum Node.js version, module system changes, CLI/config changes, and peers."
    )
    return hints


def _candidate_sources(repository: str, homepage: str) -> list[str]:
    sources: list[str] = []
    if repository:
        sources.append(repository)
        github = _github_owner_repo(repository)
        if github:
            owner, repo = github
            base = f"https://github.com/{owner}/{repo}"
            sources.extend(
                [
                    f"{base}/releases",
                    f"{base}/blob/main/CHANGELOG.md",
                    f"{base}/blob/master/CHANGELOG.md",
                    f"{base}/blob/main/README.md",
                ]
            )
    if homepage and homepage not in sources:
        sources.append(homepage)
    return sources


def _github_owner_repo(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        return None
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")
