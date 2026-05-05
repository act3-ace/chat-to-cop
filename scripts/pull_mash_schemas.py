"""Pull OpenAPI/Swagger specs from all MASH service endpoints.

Run this on the MASH network to capture every API schema in one shot.
Saves JSON specs to data/mash_schemas/.

Usage:
    python scripts/pull_mash_schemas.py
"""

from __future__ import annotations

import json
import os
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
]

OUTPUT_DIR = os.path.join("data", "mash_schemas")


def try_fetch(url: str, timeout: float = 10.0) -> str | None:
    try:
        resp = urlopen(url, timeout=timeout)
        return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError):
        return None


def fetch_spec(base_url: str) -> tuple[str | None, str | None]:
    """Try common OpenAPI spec paths. Returns (spec_json, path_that_worked)."""
    for path in SPEC_PATHS:
        url = base_url.rstrip("/") + path
        body = try_fetch(url)
        if body and body.strip().startswith("{"):
            try:
                json.loads(body)
                return body, path
            except json.JSONDecodeError:
                continue
    return None, None


def fetch_html(base_url: str) -> str | None:
    """Fetch the Swagger/docs HTML page as fallback."""
    for suffix in ["/swagger", "/swagger/index.html", "/docs", "/redoc"]:
        url = base_url.rstrip("/") + suffix
        body = try_fetch(url)
        if body and "<html" in body.lower():
            return body
    return None


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"Pulling MASH API schemas -> {OUTPUT_DIR}/")
    print(f"Timestamp: {timestamp}")
    print()

    summary = {}

    for name, base_url, description in SERVICES:
        print(f"  {description} ({base_url}) ...", end=" ", flush=True)

        spec, path = fetch_spec(base_url)
        if spec:
            out_file = os.path.join(OUTPUT_DIR, f"{name}.json")
            with open(out_file, "w", encoding="utf-8") as f:
                parsed = json.loads(spec)
                json.dump(parsed, f, indent=2)

            schema_count = len(parsed.get("components", {}).get("schemas", {}))
            path_count = len(parsed.get("paths", {}))
            title = parsed.get("info", {}).get("title", "?")
            version = parsed.get("info", {}).get("version", "?")
            print(f"OK  ({title} v{version}, {path_count} endpoints, {schema_count} schemas)")
            summary[name] = {
                "status": "ok",
                "url": base_url,
                "spec_path": path,
                "title": title,
                "version": version,
                "endpoints": path_count,
                "schemas": schema_count,
                "file": out_file,
            }
        else:
            html = fetch_html(base_url)
            if html:
                out_file = os.path.join(OUTPUT_DIR, f"{name}_page.html")
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"PARTIAL  (saved HTML page, no JSON spec found)")
                summary[name] = {"status": "partial", "url": base_url, "file": out_file}
            else:
                health = try_fetch(base_url)
                if health:
                    print(f"UP  (reachable but no spec found)")
                    summary[name] = {"status": "up_no_spec", "url": base_url}
                else:
                    print(f"DOWN  (unreachable)")
                    summary[name] = {"status": "down", "url": base_url}

    summary_file = os.path.join(OUTPUT_DIR, f"_summary_{timestamp}.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print()
    ok = sum(1 for v in summary.values() if v["status"] == "ok")
    print(f"Done: {ok}/{len(SERVICES)} specs pulled. Summary: {summary_file}")

    if ok == 0:
        print("\nNo specs found. Are you on the MASH network (10.5.x)?")
        sys.exit(1)


if __name__ == "__main__":
    main()
