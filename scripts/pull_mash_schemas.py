"""Pull everything we can from all MASH service endpoints.

Aggressively fetches OpenAPI specs, Swagger pages, JS config files,
sample API responses, health endpoints, and probes for unknown services.
Saves everything to data/mash_schemas/ organized by service.

Usage:
    python scripts/pull_mash_schemas.py
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

# --- Known services from the MASH network info sheet ---

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
    ("mash_ui", "http://10.5.185.29:3011", "JadBMW/MASH UI"),
    ("dev_chat", "http://10.5.185.30:9000", "Dev Chat Service"),
]

# Every spec path we can think of
SPEC_PATHS = [
    "/swagger/v1/swagger.json",
    "/swagger/v2/swagger.json",
    "/swagger/v3/swagger.json",
    "/openapi.json",
    "/openapi/v1.json",
    "/openapi/v2.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger.yaml",
    "/api-docs",
    "/api-docs.json",
    "/docs/openapi.json",
    "/v1/swagger.json",
    "/v1/api-docs",
    "/v2/swagger.json",
    "/v2/api-docs",
    "/api/v1/swagger.json",
    "/api/v2/swagger.json",
    "/api/swagger.json",
    "/api/openapi.json",
    "/_swagger/swagger.json",
    "/swagger/docs/v1",
    "/swagger/docs/v2",
]

# Pages that might contain or link to specs
HTML_PAGES = [
    "/swagger",
    "/swagger/",
    "/swagger/index.html",
    "/docs",
    "/docs/",
    "/redoc",
    "/redoc/",
    "/api-docs",
    "/api",
    "",
    "/",
]

# JS/config files that Swagger UIs load specs from
JS_CONFIG_FILES = [
    "/swagger/index.js",
    "/swagger/swagger-config.js",
    "/swagger/swagger-initializer.js",
    "/swagger/custom.js",
    "/swagger/config.json",
    "/docs/swagger-config.js",
]

# Common API endpoints to probe for sample data
SAMPLE_ENDPOINTS = [
    "/health",
    "/api/health",
    "/healthz",
    "/status",
    "/api/status",
    "/version",
    "/api/version",
    "/api/v1/tracks",
    "/api/v1/events",
    "/api/v1/effects",
    "/api/tracks",
    "/api/events",
    "/api/effects",
    "/tracks",
    "/events",
    "/effects",
    "/missions",
    "/smartpacks",
    "/category",
    "/entity-table",
    "/threat-table",
    "/messages?page_size=3",
    "/api/v1/aoi",
    "/api/v1/poi",
    "/hubs",
    "/signalr",
    "/signalr/negotiate",
]

# Extra ports to probe on the known hosts
EXTRA_PORTS = [
    80,
    443,
    3000,
    3010,
    3012,
    3013,
    3014,
    3018,
    3019,
    3020,
    3023,
    3025,
    3026,
    3029,
    3030,
    3050,
    3070,
    3090,
    5000,
    5001,
    8000,
    8080,
    8443,
    9090,
]

OUTPUT_DIR = os.path.join("data", "mash_schemas")


def fetch(url: str, timeout: float = 5.0, accept: str | None = None) -> str | None:
    try:
        req = Request(url)
        if accept:
            req.add_header("Accept", accept)
        resp = urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError):
        return None


def is_json(body: str | None) -> bool:
    if not body:
        return False
    s = body.strip()
    if not (s.startswith("{") or s.startswith("[")):
        return False
    try:
        json.loads(s)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def save_file(svc_dir: str, filename: str, content: str) -> str:
    path = os.path.join(svc_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def save_json(svc_dir: str, filename: str, content: str) -> str:
    path = os.path.join(svc_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        try:
            parsed = json.loads(content)
            json.dump(parsed, f, indent=2)
        except (json.JSONDecodeError, ValueError):
            f.write(content)
    return path


def extract_urls_from_text(text: str) -> list[str]:
    """Extract anything that looks like an API/spec URL from text."""
    urls = []
    for pattern in [
        r'url\s*[:=]\s*["\']([^"\']+)["\']',
        r'"url"\s*:\s*"([^"]+)"',
        r"configUrl\s*[:=]\s*['\"]([^'\"]+)['\"]",
        r'href\s*=\s*["\']([^"\']*(?:swagger|openapi|api-docs)[^"\']*)["\']',
        r'src\s*=\s*["\']([^"\']+\.js)["\']',
        r"urls\s*:\s*\[(.*?)\]",
    ]:
        urls.extend(re.findall(pattern, text, re.IGNORECASE | re.DOTALL))
    return urls


def resolve_url(base_url: str, ref: str) -> str:
    if ref.startswith("http"):
        return ref
    if ref.startswith("/"):
        return base_url.rstrip("/") + ref
    return base_url.rstrip("/") + "/" + ref


def pull_service(name: str, base_url: str, description: str) -> dict:
    """Pull everything we can from a single service."""
    svc_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(svc_dir, exist_ok=True)

    result = {"name": name, "url": base_url, "description": description, "files": []}
    spec_found = False

    # --- Phase 1: Try all known spec paths ---
    print(f"    specs: ", end="", flush=True)
    for path in SPEC_PATHS:
        url = base_url.rstrip("/") + path
        body = fetch(url, accept="application/json")
        if body and is_json(body):
            safe_name = path.strip("/").replace("/", "_") + ".json"
            if not safe_name.endswith(".json.json"):
                pass
            else:
                safe_name = safe_name.replace(".json.json", ".json")
            f = save_json(svc_dir, f"spec_{safe_name}", body)
            result["files"].append(f)
            if not spec_found:
                save_json(svc_dir, "openapi_spec.json", body)
                parsed = json.loads(body)
                result["spec_path"] = path
                result["title"] = parsed.get("info", {}).get("title", "?")
                result["version"] = parsed.get("info", {}).get("version", "?")
                result["endpoints"] = len(parsed.get("paths", {}))
                result["schemas"] = len(parsed.get("components", {}).get("schemas", {}))
                spec_found = True
                print("OK ", end="", flush=True)
    if not spec_found:
        print("- ", end="", flush=True)

    # --- Phase 2: Fetch HTML pages ---
    print("pages: ", end="", flush=True)
    html_bodies = {}
    for suffix in HTML_PAGES:
        url = base_url.rstrip("/") + suffix
        body = fetch(url)
        if body and len(body) > 50:
            tag = suffix.strip("/").replace("/", "_") or "root"
            if "<html" in body.lower() or "<!" in body[:50]:
                fname = f"page_{tag}.html"
            elif is_json(body):
                fname = f"page_{tag}.json"
            else:
                fname = f"page_{tag}.txt"
            f = save_file(svc_dir, fname, body)
            result["files"].append(f)
            html_bodies[suffix] = body
    print(f"{len(html_bodies)} ", end="", flush=True)

    # --- Phase 3: Fetch JS/config files ---
    print("js: ", end="", flush=True)
    js_count = 0
    all_discovered_urls: list[str] = []
    for js_path in JS_CONFIG_FILES:
        url = base_url.rstrip("/") + js_path
        body = fetch(url)
        if body and len(body) > 10:
            tag = js_path.strip("/").replace("/", "_")
            f = save_file(svc_dir, f"config_{tag}", body)
            result["files"].append(f)
            js_count += 1
            all_discovered_urls.extend(extract_urls_from_text(body))

    # Also extract URLs from any HTML pages we fetched
    for body in html_bodies.values():
        all_discovered_urls.extend(extract_urls_from_text(body))

    print(f"{js_count} ", end="", flush=True)

    # --- Phase 4: Try any URLs discovered in HTML/JS ---
    if not spec_found and all_discovered_urls:
        print("discovered: ", end="", flush=True)
        seen = set()
        for ref in all_discovered_urls:
            full_url = resolve_url(base_url, ref)
            if full_url in seen or "cdn.jsdelivr" in full_url or full_url.endswith(".css"):
                continue
            seen.add(full_url)
            body = fetch(full_url, accept="application/json")
            if body and is_json(body):
                safe = re.sub(r"[^a-zA-Z0-9._-]", "_", ref.strip("/"))[:80]
                f = save_json(svc_dir, f"discovered_{safe}.json", body)
                result["files"].append(f)
                parsed = json.loads(body)
                if "paths" in parsed or "openapi" in parsed or "swagger" in parsed:
                    save_json(svc_dir, "openapi_spec.json", body)
                    result["spec_path"] = ref
                    result["title"] = parsed.get("info", {}).get("title", "?")
                    result["version"] = parsed.get("info", {}).get("version", "?")
                    result["endpoints"] = len(parsed.get("paths", {}))
                    result["schemas"] = len(parsed.get("components", {}).get("schemas", {}))
                    spec_found = True
                    print("SPEC! ", end="", flush=True)
        if not spec_found:
            print("- ", end="", flush=True)

    # --- Phase 5: Probe common API endpoints for sample data ---
    print("samples: ", end="", flush=True)
    sample_count = 0
    for endpoint in SAMPLE_ENDPOINTS:
        url = base_url.rstrip("/") + endpoint
        body = fetch(url, timeout=3.0, accept="application/json")
        if body and len(body) > 2:
            safe = endpoint.strip("/").replace("/", "_").replace("?", "_")[:60]
            if is_json(body):
                f = save_json(svc_dir, f"sample_{safe}.json", body)
            else:
                f = save_file(svc_dir, f"sample_{safe}.txt", body)
            result["files"].append(f)
            sample_count += 1
    print(f"{sample_count} ", end="", flush=True)

    result["status"] = "ok" if spec_found else ("partial" if result["files"] else "down")
    result["total_files"] = len(result["files"])
    return result


def probe_extra_ports():
    """Probe for unknown services on the MASH hosts."""
    hosts = ["10.5.185.29", "10.5.185.30"]
    found = []

    print("\n  Probing extra ports...", flush=True)
    for host in hosts:
        for port in EXTRA_PORTS:
            url = f"http://{host}:{port}"
            body = fetch(url, timeout=2.0)
            if body and len(body) > 10:
                found.append(
                    {"host": host, "port": port, "url": url, "size": len(body), "is_html": "<html" in body.lower()}
                )
                print(f"    {url} -- UP ({len(body)} bytes)", flush=True)

    return found


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"MASH schema pull -- {timestamp}")
    print(f"Output: {OUTPUT_DIR}/")
    print(f"Services: {len(SERVICES)}")
    print()

    results = {}

    for name, base_url, description in SERVICES:
        print(f"  [{name}] {description} ({base_url})")
        result = pull_service(name, base_url, description)
        results[name] = result
        status = result["status"].upper()
        files = result["total_files"]
        extra = ""
        if "title" in result:
            extra = f" -- {result['title']} v{result['version']}, {result.get('endpoints', '?')} endpoints, {result.get('schemas', '?')} schemas"
        print(f"\n    => {status} ({files} files){extra}")
        print()

    # --- Probe for unknown services ---
    extra_ports = probe_extra_ports()
    if extra_ports:
        extras_dir = os.path.join(OUTPUT_DIR, "_extra_ports")
        os.makedirs(extras_dir, exist_ok=True)
        save_json(extras_dir, "discovered_ports.json", json.dumps(extra_ports))

        # For any we found, grab the root page
        for svc in extra_ports:
            body = fetch(svc["url"])
            if body:
                safe = f"{svc['host']}_{svc['port']}"
                if is_json(body):
                    save_json(extras_dir, f"{safe}.json", body)
                else:
                    save_file(extras_dir, f"{safe}.html", body)

    # --- Also try the ethernet hostname ---
    print("\n  Trying ethernet hostname (rdwwtws-41lgz2)...", flush=True)
    eth_dir = os.path.join(OUTPUT_DIR, "_ethernet_host")
    os.makedirs(eth_dir, exist_ok=True)
    eth_found = 0
    for _, wifi_url, _ in SERVICES:
        port = wifi_url.split(":")[-1].split("/")[0]
        eth_url = f"http://rdwwtws-41lgz2:{port}"
        body = fetch(eth_url, timeout=3.0)
        if body and len(body) > 10:
            safe = f"port_{port}"
            if is_json(body):
                save_json(eth_dir, f"{safe}.json", body)
            else:
                save_file(eth_dir, f"{safe}.html", body)
            eth_found += 1
    print(f"    {eth_found} ports responded on ethernet hostname")

    # --- Summary ---
    summary = {
        "timestamp": timestamp,
        "services": results,
        "extra_ports": extra_ports,
    }
    summary_file = os.path.join(OUTPUT_DIR, f"_summary_{timestamp}.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print()
    print("=" * 60)
    ok = sum(1 for v in results.values() if v["status"] == "ok")
    partial = sum(1 for v in results.values() if v["status"] == "partial")
    total_files = sum(v["total_files"] for v in results.values())
    print(f"Specs found: {ok}/{len(SERVICES)}")
    print(f"Partial: {partial}")
    print(f"Total files saved: {total_files}")
    print(f"Summary: {summary_file}")

    for name, r in results.items():
        status = r["status"].upper()
        files = r["total_files"]
        extra = ""
        if "title" in r:
            extra = f" {r['title']} v{r['version']}"
        print(f"  {name:<25} {status:<10} {files:>3} files{extra}")

    print()
    if ok < len(SERVICES):
        print(f"Check {OUTPUT_DIR}/<service>/ for saved HTML/JS/samples.")
        print("The JS config files may contain the spec URL pattern.")


if __name__ == "__main__":
    main()
