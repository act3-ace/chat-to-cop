"""Pull OpenAPI/Swagger specs from all MASH service endpoints.

Run this on the MASH network to capture every API schema in one shot.
Saves JSON specs to data/mash_schemas/.

Usage:
    python scripts/pull_mash_schemas.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

SERVICES = [
    ("track_manager", "http://10.5.185.29:3021", "Tracks"),
    ("smart_pack_manager", "http://10.5.185.29:3028", "SmartPack"),
    ("effects_manager", "http://10.5.185.29:3024", "Effects"),
    ("event_manager", "http://10.5.185.29:3016", "PaeOutput"),
    ("mef_manager", "http://10.5.185.29:3027", "MefOutput"),
    ("mission_manager", "http://10.5.185.29:3022", "GbcOutput"),
    ("aoi_manager", "http://10.5.185.29:3015", "Area of Interest"),
    ("poi_manager", "http://10.5.185.29:3017", "Points of Interest"),
    ("deltron_chatparser", "http://10.5.185.30:3060", "Deltron/ChatParser"),
    ("irc_streamer", "http://10.5.185.30:3080", "IRC Streamer"),
]

SPEC_PATHS = [
    "/swagger/v1/swagger.json",
    "/swagger/v2/swagger.json",
    "/openapi.json",
    "/swagger.json",
    "/api-docs",
    "/docs/openapi.json",
    "/v1/swagger.json",
    "/api/v1/swagger.json",
    "/api/swagger.json",
]

OUTPUT_DIR = os.path.join("data", "mash_schemas")


def try_fetch(url: str, timeout: float = 5.0) -> str | None:
    try:
        resp = urlopen(url, timeout=timeout)
        return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError):
        return None


def is_json_spec(body: str | None) -> bool:
    if not body or not body.strip().startswith("{"):
        return False
    try:
        json.loads(body)
        return True
    except json.JSONDecodeError:
        return False


def extract_spec_urls_from_html(html: str) -> list[str]:
    """Parse Swagger UI HTML to find the actual spec URL."""
    urls = []
    for pattern in [
        r'url\s*[:=]\s*["\']([^"\']+\.json)["\']',
        r'url\s*[:=]\s*["\']([^"\']+openapi[^"\']*)["\']',
        r'url\s*[:=]\s*["\']([^"\']+swagger[^"\']*)["\']',
        r'"url"\s*:\s*"([^"]+)"',
        r"configUrl\s*[:=]\s*['\"]([^'\"]+)['\"]",
    ]:
        urls.extend(re.findall(pattern, html, re.IGNORECASE))
    return urls


def fetch_spec(base_url: str) -> tuple[str | None, str | None]:
    """Try common OpenAPI spec paths. Returns (spec_json, path_that_worked)."""
    for path in SPEC_PATHS:
        url = base_url.rstrip("/") + path
        body = try_fetch(url)
        if is_json_spec(body):
            return body, path
    return None, None


def fetch_spec_via_html(base_url: str) -> tuple[str | None, str | None, str | None]:
    """Fetch Swagger HTML, extract spec URL, fetch spec. Returns (spec, path, html)."""
    for suffix in ["/swagger", "/swagger/index.html", "/docs", "/redoc", ""]:
        url = base_url.rstrip("/") + suffix
        html = try_fetch(url)
        if not html or "<html" not in html.lower():
            continue

        all_spec_urls: list[str] = []

        # Extract URLs from the HTML page itself
        all_spec_urls.extend(extract_spec_urls_from_html(html))

        # .NET Swagger loads config from index.js -- fetch and parse that too
        for js_path in ["/swagger/index.js", "/swagger/swagger-config.js"]:
            js_url = base_url.rstrip("/") + js_path
            js_body = try_fetch(js_url)
            if js_body:
                all_spec_urls.extend(extract_spec_urls_from_html(js_body))

        for spec_url in all_spec_urls:
            if spec_url.startswith("/"):
                full_url = base_url.rstrip("/") + spec_url
            elif spec_url.startswith("http"):
                full_url = spec_url
            else:
                full_url = base_url.rstrip("/") + "/" + spec_url
            body = try_fetch(full_url)
            if is_json_spec(body):
                return body, spec_url, html

        return None, None, html
    return None, None, None


def save_spec(name: str, spec: str) -> tuple[str, dict]:
    """Save spec JSON and return (filepath, metadata)."""
    out_file = os.path.join(OUTPUT_DIR, f"{name}.json")
    parsed = json.loads(spec)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)
    schema_count = len(parsed.get("components", {}).get("schemas", {}))
    path_count = len(parsed.get("paths", {}))
    title = parsed.get("info", {}).get("title", "?")
    version = parsed.get("info", {}).get("version", "?")
    return out_file, {
        "title": title,
        "version": version,
        "endpoints": path_count,
        "schemas": schema_count,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"Pulling MASH API schemas -> {OUTPUT_DIR}/")
    print(f"Timestamp: {timestamp}")
    print()

    summary = {}

    for name, base_url, description in SERVICES:
        print(f"  {description} ({base_url}) ...", end=" ", flush=True)

        # Try 1: direct spec paths
        spec, path = fetch_spec(base_url)
        if spec:
            out_file, meta = save_spec(name, spec)
            print(f"OK  ({meta['title']} v{meta['version']}, {meta['endpoints']} endpoints, {meta['schemas']} schemas)")
            summary[name] = {"status": "ok", "url": base_url, "spec_path": path, **meta, "file": out_file}
            continue

        # Try 2: fetch HTML page, parse for spec URL, fetch that
        spec, spec_url, html = fetch_spec_via_html(base_url)
        if spec:
            out_file, meta = save_spec(name, spec)
            print(
                f"OK  ({meta['title']} v{meta['version']}, {meta['endpoints']} endpoints, {meta['schemas']} schemas) [from HTML]"
            )
            summary[name] = {"status": "ok", "url": base_url, "spec_path": spec_url, **meta, "file": out_file}
            if html:
                html_file = os.path.join(OUTPUT_DIR, f"{name}_page.html")
                with open(html_file, "w", encoding="utf-8") as f:
                    f.write(html)
            continue

        # Fallback: save whatever we got
        if html:
            html_file = os.path.join(OUTPUT_DIR, f"{name}_page.html")
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html)
            print("PARTIAL  (saved HTML page, no JSON spec found)")
            summary[name] = {"status": "partial", "url": base_url, "file": html_file}
        else:
            probe = try_fetch(base_url)
            if probe:
                print("UP  (reachable but no spec found)")
                summary[name] = {"status": "up_no_spec", "url": base_url}
            else:
                print("DOWN  (unreachable)")
                summary[name] = {"status": "down", "url": base_url}

    summary_file = os.path.join(OUTPUT_DIR, f"_summary_{timestamp}.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print()
    ok = sum(1 for v in summary.values() if v["status"] == "ok")
    partial = sum(1 for v in summary.values() if v["status"] == "partial")
    print(f"Done: {ok}/{len(SERVICES)} specs pulled, {partial} partial. Summary: {summary_file}")

    if partial > 0:
        print(f"\nPartial results saved as HTML in {OUTPUT_DIR}/")
        print("Open the HTML files in a browser to see the Swagger UI.")

    if ok == 0:
        print("\nNo JSON specs found. The services may use a non-standard spec path.")
        print("Check the saved HTML files for clues, or open the Swagger URLs in a browser.")
        sys.exit(1)


if __name__ == "__main__":
    main()
