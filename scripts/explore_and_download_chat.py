"""
Explore Pydio DASH data and download chat logs.

Two modes:
  explore  - Recursively list the directory tree, optionally filtering by name pattern
  download - Download all chat-related files found under a given path

Usage:
  python scripts/explore_and_download_chat.py explore /dav/dash-mef/
  python scripts/explore_and_download_chat.py explore /dav/dash-mef/Dash3-GBC/Data --pattern chat
  python scripts/explore_and_download_chat.py download --output docs/DASH/downloaded/chat
"""

import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests

# ── Configuration ────────────────────────────────────────────────────────────

DLE_USER = "hamilton.clouse.1@us.af.mil"
PAT_FILE = Path(r"C:\Users\hsclouse\GoogleDrive\keys\DLE_Pydio_HamiltonClouse_PAT.txt")
BASE_URL = "https://pydio.dle.afrl.af.mil"

# Chat-related filename patterns (case-insensitive)
CHAT_PATTERNS = re.compile(r"(chat|irc|mirc|log\.txt|speech.to.text)", re.IGNORECASE)

# ── Auth ─────────────────────────────────────────────────────────────────────


def get_auth():
    pat = PAT_FILE.read_text().strip()
    return (DLE_USER, pat)


# ── Session with cert handling ───────────────────────────────────────────────


def make_session():
    """Create a requests session. On Windows, try system certs first, fall back to no-verify."""
    s = requests.Session()
    s.auth = get_auth()

    # Try a test request with default certs (Windows should have DoD certs if CAC-enabled)
    try:
        resp = s.request("PROPFIND", f"{BASE_URL}/dav/dash-mef/", headers={"Depth": "0"}, timeout=15)
        resp.raise_for_status()
        print("[OK] Connected with system certificates")
        return s
    except requests.exceptions.SSLError:
        print("[WARN] SSL verification failed with system certs.")
        print("       Trying with verify=False (DoD certs not in default bundle)")
        s.verify = False
        # Suppress the InsecureRequestWarning
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            resp = s.request("PROPFIND", f"{BASE_URL}/dav/dash-mef/", headers={"Depth": "0"}, timeout=15)
            resp.raise_for_status()
            print("[OK] Connected (SSL verify disabled)")
            return s
        except Exception as e:
            print(f"[ERROR] Cannot connect to Pydio: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Cannot connect to Pydio: {e}")
        sys.exit(1)


# ── WebDAV operations ────────────────────────────────────────────────────────


def webdav_list(session, path):
    """PROPFIND a WebDAV path, return list of (href, is_directory) tuples."""
    url = f"{BASE_URL}{path}"
    if not url.endswith("/"):
        url += "/"

    resp = session.request("PROPFIND", url, headers={"Depth": "1"}, timeout=30)
    resp.raise_for_status()

    # Parse all hrefs from WebDAV XML response
    hrefs = re.findall(r"<(?:[^>]+:)?href>([^<]+)</(?:[^>]+:)?href>", resp.text)
    cleaned = [h.split(BASE_URL)[-1] for h in hrefs]

    # Skip self-reference (first item or exact match)
    results = []
    for c in cleaned:
        if c.rstrip("/") == path.rstrip("/"):
            continue
        is_dir = c.endswith("/")
        results.append((c, is_dir))

    return results


def _print(msg):
    """Print with immediate flush so progress is visible."""
    print(msg, flush=True)


# Global counter for progress
_dirs_explored = 0
_files_found = 0
_matches_found = 0


def explore_recursive(session, path, depth=0, max_depth=10, pattern=None, results=None):
    """Recursively explore a WebDAV path. Returns list of all items found."""
    global _dirs_explored, _files_found, _matches_found
    if results is None:
        results = []
        _dirs_explored = 0
        _files_found = 0
        _matches_found = 0
    if depth > max_depth:
        return results

    _dirs_explored += 1
    indent = "  " * depth
    try:
        items = webdav_list(session, path)
    except Exception as e:
        _print(f"{indent}[ERROR] {path}: {e}")
        return results

    dirs_here = sum(1 for _, d in items if d)
    files_here = len(items) - dirs_here
    # Show a status line for the directory being explored
    short_path = path.replace("/dav/dash-mef/", "")
    _print(f"{indent}[{short_path}] ({dirs_here} dirs, {files_here} files)")

    for href, is_dir in items:
        name = unquote(href.rstrip("/").split("/")[-1])
        if is_dir:
            results.append({"path": href, "name": name, "is_dir": True, "depth": depth})
            explore_recursive(session, href, depth + 1, max_depth, pattern, results)
        else:
            _files_found += 1
            matches = pattern and pattern.search(name) if pattern else True
            if pattern and matches:
                _matches_found += 1
                _print(f"{indent}  >>> MATCH: {name}  [{href}]")
            elif not pattern:
                _print(f"{indent}  {name}")
            # When filtering, don't print non-matching files (reduces noise)
            results.append(
                {
                    "path": href,
                    "name": name,
                    "is_dir": False,
                    "depth": depth,
                    "matches": bool(matches),
                }
            )

    return results


def download_file(session, href, dest_path):
    """Download a single file."""
    url = f"{BASE_URL}{href}"
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    resp = session.get(url, stream=True, timeout=60)
    resp.raise_for_status()

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    size = dest_path.stat().st_size
    _print(f"  ✓ {dest_path.name} ({size:,} bytes)")
    return dest_path


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_explore(session, dav_path, pattern=None, max_depth=10):
    """Explore and print the directory tree, optionally filtering by pattern."""
    pat = re.compile(pattern, re.IGNORECASE) if pattern else None
    _print(f"\n{'=' * 60}")
    _print(f"Exploring: {dav_path}")
    if pat:
        _print(f"Filtering for: {pattern}")
    _print(f"{'=' * 60}\n")

    results = explore_recursive(session, dav_path, max_depth=max_depth, pattern=pat)

    _print(f"\n{'=' * 60}")
    _print(f"Explored {_dirs_explored} directories, found {_files_found} files total")
    if pat:
        matches = [r for r in results if r.get("matches")]
        _print(f"  {_matches_found} matched '{pattern}':")
        for m in matches:
            _print(f"    {m['path']}")
    _print(f"{'=' * 60}")

    return results


def cmd_download_chat(session, dav_paths, output_dir, max_depth=10):
    """Search for and download all chat-related files under the given paths."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    all_matches = []

    for dav_path in dav_paths:
        print(f"\nSearching for chat files under: {dav_path}")
        results = explore_recursive(session, dav_path, max_depth=max_depth, pattern=CHAT_PATTERNS)
        matches = [r for r in results if r.get("matches")]
        all_matches.extend(matches)

    if not all_matches:
        print("\nNo chat-related files found.")
        return

    print(f"\n{'=' * 60}")
    print(f"Found {len(all_matches)} chat-related files. Downloading...")
    print(f"{'=' * 60}\n")

    downloaded = []
    for m in all_matches:
        # Preserve directory structure relative to /dav/dash-mef/
        rel_path = m["path"].replace("/dav/dash-mef/", "")
        dest = output / unquote(rel_path)
        try:
            download_file(session, m["path"], dest)
            downloaded.append(dest)
        except Exception as e:
            print(f"  ✗ Failed: {m['path']}: {e}")

    # Auto-extract any zip files
    print(f"\n{'=' * 60}")
    print("Extracting zip files...")
    for f in downloaded:
        if f.suffix.lower() == ".zip":
            extract_dir = f.parent / f.stem
            extract_dir.mkdir(exist_ok=True)
            try:
                with zipfile.ZipFile(f, "r") as zf:
                    zf.extractall(extract_dir)
                    names = zf.namelist()
                print(f"  ✓ Extracted {f.name} → {len(names)} files in {extract_dir}")
            except Exception as e:
                print(f"  ✗ Failed to extract {f.name}: {e}")

    print(f"\n{'=' * 60}")
    print(f"Done! {len(downloaded)} files saved to {output}")
    print(f"{'=' * 60}")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Explore Pydio DASH data and download chat logs")
    sub = parser.add_subparsers(dest="command")

    # explore command
    p_explore = sub.add_parser("explore", help="Explore directory tree on Pydio")
    p_explore.add_argument("path", default="/dav/dash-mef/", nargs="?", help="WebDAV path to explore")
    p_explore.add_argument("--pattern", "-p", help="Regex pattern to highlight matching files")
    p_explore.add_argument("--max-depth", "-d", type=int, default=10, help="Maximum recursion depth (default: 10)")

    # download command
    p_download = sub.add_parser("download", help="Find and download all chat-related files")
    p_download.add_argument(
        "paths",
        nargs="*",
        default=[
            "/dav/dash-mef/Dash1-PAE/",
            "/dav/dash-mef/Dash2-MEF/",
            "/dav/dash-mef/Dash3-GBC/",
        ],
        help="WebDAV paths to search (default: all three DASH events)",
    )
    p_download.add_argument("--output", "-o", default="docs/DASH/downloaded/chat", help="Local output directory")
    p_download.add_argument("--max-depth", "-d", type=int, default=10)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    session = make_session()

    if args.command == "explore":
        cmd_explore(session, args.path, pattern=args.pattern, max_depth=args.max_depth)
    elif args.command == "download":
        cmd_download_chat(session, args.paths, args.output, max_depth=args.max_depth)


if __name__ == "__main__":
    main()
