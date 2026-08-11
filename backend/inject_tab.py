#!/usr/bin/env python3
"""Inject the 1Valet listener into a Chrome tab over the DevTools Protocol.

Chrome has to be started once with --remote-debugging-port=9222:
  macOS:   open -a "Google Chrome" --args --remote-debugging-port=9222
  Linux:   google-chrome --remote-debugging-port=9222 &
  Windows: chrome.exe --remote-debugging-port=9222
"""

import sys
import json
import os
import urllib.request
import urllib.error

CDP_PORT = 9222
TAB_INDEX = 2   # 0-based, so tab 3 in the browser bar


def _get_tabs():
    try:
        with urllib.request.urlopen(
            f"http://localhost:{CDP_PORT}/json/list", timeout=4
        ) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _find_tab(tabs):
    page_tabs = [t for t in tabs if t.get("type") == "page"]

    # Prefer the 1Valet tab by URL
    for t in page_tabs:
        if "1valetbas" in t.get("url", "").lower():
            return t, "1Valet URL match"

    # Fall back to the fixed tab index
    if TAB_INDEX < len(page_tabs):
        return page_tabs[TAB_INDEX], f"tab index {TAB_INDEX} (tab {TAB_INDEX + 1})"

    # Last resort: first available tab
    if page_tabs:
        return page_tabs[0], "first available tab"

    return None, "no tabs found"


def _inject(tab, js):
    try:
        import websocket  # websocket-client
    except ImportError:
        print("   pip3 install websocket-client")
        sys.exit(1)

    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("Tab has no webSocketDebuggerUrl — is another DevTools window open on this tab?")

    ws = websocket.create_connection(ws_url, timeout=10)
    ws.send(json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": js, "returnByValue": False},
    }))
    result = json.loads(ws.recv())
    ws.close()
    return result


def _load_script(server_url: str) -> str:
    script_path = os.path.join(os.path.dirname(__file__), "smartlockerscript.js")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script not found: {script_path}")
    with open(script_path) as f:
        js = f.read()
    return js.replace("__SERVER_URL__", server_url)


def _launch_chrome_with_debug():
    """Open Chrome with the debug flag if it isn't already running."""
    import subprocess, platform
    plat = platform.system()
    try:
        if plat == "Darwin":
            subprocess.Popen([
                "open", "-a", "Google Chrome",
                "--args", "--remote-debugging-port=9222",
            ])
        elif plat == "Linux":
            for exe in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
                try:
                    subprocess.Popen([exe, f"--remote-debugging-port={CDP_PORT}"])
                    break
                except FileNotFoundError:
                    continue
        return True
    except Exception:
        return False


def main():
    server_url = (sys.argv[1] if len(sys.argv) > 1
                  else os.getenv("SERVER_URL", "http://localhost:5002")).rstrip("/")

    tabs = _get_tabs()

    if tabs is None:
        print(f"\n   Chrome CDP not reachable on port {CDP_PORT}.")
        print("   Attempting to launch Chrome with debug port...")
        _launch_chrome_with_debug()

        import time
        for _ in range(10):
            time.sleep(1)
            tabs = _get_tabs()
            if tabs is not None:
                break

    if tabs is None:
        print(f"\n❌  Cannot reach Chrome on port {CDP_PORT}.")
        print("─" * 58)
        print("Run Chrome once with the debug flag, then re-run start.sh:")
        print("  macOS:  open -a 'Google Chrome' --args --remote-debugging-port=9222")
        print("  Linux:  google-chrome --remote-debugging-port=9222 &")
        print("─" * 58)
        sys.exit(1)

    tab, reason = _find_tab(tabs)
    if not tab:
        print("❌  No page tabs found in Chrome. Open at least one page.")
        sys.exit(1)

    title = tab.get("title", "?")[:55]
    url   = tab.get("url",   "?")[:75]
    print(f"   Targeting ({reason}): [{title}]")
    print(f"   URL: {url}")

    js = _load_script(server_url)
    result = _inject(tab, js)

    err = result.get("result", {}).get("exceptionDetails") or result.get("error")
    if err:
        print(f"⚠️   Injection note: {err}")
        return

    print(f"   SERVER_URL injected: {server_url}")

    # Confirm the globals landed on the window
    verify = _inject(tab, "typeof window.startValetListener === 'function'")
    confirmed = verify.get("result", {}).get("result", {}).get("value", False)
    if confirmed:
        print("   Script globals confirmed on window (startValetListener ✓)")
    else:
        print("⚠️   startValetListener not found — injection may have failed")
        return

    # Is the ADD DELIVERY popup already open?
    popup_check = _inject(tab, """
        (function() {
            var inputs = Array.prototype.slice.call(document.querySelectorAll('input'));
            var found = inputs.find(function(inp) {
                return inp.placeholder &&
                       inp.placeholder.toLowerCase().includes('suite') &&
                       inp.offsetParent !== null;
            });
            return !!found;
        })()
    """)
    popup_open = popup_check.get("result", {}).get("result", {}).get("value", False)
    if popup_open:
        print("   'ADD DELIVERY' popup detected — listener will start automatically ✓")
    else:
        print("")
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  ⚠️  'ADD DELIVERY' popup is NOT open in this tab.  │")
        print("  │  Open it now — the listener auto-starts in 3 s.    │")
        print("  │  Or check the console and call startValetListener() │")
        print("  └─────────────────────────────────────────────────────┘")
        print("")
    print("  To verify in Chrome DevTools console:")
    print("    valetStatus()           ← shows running state + server URL")
    print("    startValetListener()    ← (re-)start manually if needed")


if __name__ == "__main__":
    main()
