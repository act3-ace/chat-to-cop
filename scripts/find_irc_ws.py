"""Find the IRC WebSocket endpoint on the MASH network.

Run this to discover which WebSocket URL chat-to-cop should connect to.
Tries known endpoints from DASH 3, common WebSocket paths, and scrapes
the web client page for WebSocket URLs.

Usage:
    python scripts/find_irc_ws.py
    python scripts/find_irc_ws.py --host 10.0.0.1
"""

from __future__ import annotations

import argparse
import asyncio
import re
import socket
from urllib.request import urlopen


def check_tcp(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


async def _ws_connect_and_probe(url: str) -> tuple[bool, str]:
    """Inner coroutine: connect, send NICK/USER, try to read one line."""
    import websockets

    async with websockets.connect(url, open_timeout=5, close_timeout=2) as ws:
        await ws.send("NICK probe\r\n")
        await ws.send("USER probe 0 * :probe\r\n")
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=3.0)
            return True, f"got response: {response[:80]}"
        except (asyncio.TimeoutError, TimeoutError):
            return True, "connected (no immediate response, but socket open)"


async def check_websocket(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """Try connecting to a WebSocket URL. Returns (success, detail)."""
    try:
        import websockets  # noqa: F401
    except ImportError:
        return False, "websockets not installed -- run: pip install websockets"

    try:
        return await asyncio.wait_for(_ws_connect_and_probe(url), timeout=timeout)
    except asyncio.TimeoutError:
        return False, "timeout"
    except ConnectionRefusedError:
        return False, "connection refused"
    except OSError as e:
        return False, f"OS error: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def scrape_web_client(host: str) -> list[str]:
    """Fetch the IRC web client page and look for WebSocket URLs."""
    urls_found = []
    pages_to_try = [
        f"http://{host}/irc",
        f"http://{host}/irc/",
        f"http://{host}:8097",
    ]

    for page_url in pages_to_try:
        try:
            resp = urlopen(page_url, timeout=5)
            html = resp.read().decode("utf-8", errors="replace")
            ws_urls = re.findall(r'wss?://[^"\'<>\s]+', html)
            for u in ws_urls:
                if u not in urls_found:
                    urls_found.append(u)
        except Exception:
            pass

    return urls_found


async def main():
    parser = argparse.ArgumentParser(description="Find IRC WebSocket endpoint on MASH network")
    parser.add_argument("--host", default="10.0.0.1", help="IRC server IP (default: 10.0.0.1)")
    args = parser.parse_args()

    host = args.host
    print(f"Probing IRC server at {host}...\n")

    # Step 1: Check known TCP ports
    print("=== TCP port scan ===")
    ports = [6667, 8097, 80, 443, 7681, 9090]
    open_ports = []
    for port in ports:
        status = check_tcp(host, port)
        label = "OPEN" if status else "closed"
        if status:
            open_ports.append(port)
        print(f"  {host}:{port:<6} {label}")

    # Step 2: Scrape web client for WebSocket URLs
    print("\n=== Scraping web client for WebSocket URLs ===")
    scraped_urls = scrape_web_client(host)
    if scraped_urls:
        for url in scraped_urls:
            print(f"  Found: {url}")
    else:
        print("  No WebSocket URLs found in web client HTML")

    # Step 3: Try WebSocket connections
    print("\n=== Testing WebSocket connections ===")
    candidates = [
        f"ws://{host}:8097",
        f"ws://{host}:8097/irc",
        f"ws://{host}:8097/webirc/websocket",
        f"ws://{host}:6667",
        f"ws://{host}/irc",
        f"ws://{host}:80/irc",
        f"ws://{host}:80/webirc/websocket",
        f"ws://{host}:80/ws",
    ]

    # Add any URLs found by scraping
    for url in scraped_urls:
        if url not in candidates:
            candidates.insert(0, url)

    # Also try any open ports we found
    for port in open_ports:
        url = f"ws://{host}:{port}"
        if url not in candidates:
            candidates.append(url)

    working = []
    for url in candidates:
        ok, detail = await check_websocket(url)
        status = "OK" if ok else "no"
        print(f"  {url:<45} {status}  ({detail})")
        if ok:
            working.append(url)

    # Summary
    print("\n" + "=" * 60)
    if working:
        best = working[0]
        print(f"SUCCESS -- use this URL:\n")
        print(f"  run.bat --live {best}")
        print(f"\nOr manually:")
        print(f"  python -m chat_to_cop --irc-url {best}")
        print(f"\nEnv var (if needed):")
        print(f"  set CHAT_TO_COP_IRC_URL={best}")
        if len(working) > 1:
            print(f"\nOther working endpoints: {working[1:]}")
    else:
        print("No WebSocket endpoint found automatically.\n")
        print("The IRC server is on port 6667 (raw TCP), not WebSocket.")
        print("Options:")
        print("  1. Ask exercise control if there's a WebSocket gateway")
        print("  2. Open http://10.0.0.1/irc in Chrome, press F12,")
        print("     Network tab > WS filter, and report the URL")
        print(f"\nOpen TCP ports found: {open_ports}")
        if scraped_urls:
            print(f"WebSocket URLs in page source: {scraped_urls}")


if __name__ == "__main__":
    asyncio.run(main())
