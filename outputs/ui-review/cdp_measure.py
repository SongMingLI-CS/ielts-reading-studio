"""Measure layout in a real browser without extra dependencies.

Talks the DevTools protocol over a hand-rolled WebSocket client (stdlib only) so the review
can print exact numbers instead of guessing from screenshots.

``python outputs/ui-review/cdp_measure.py <url> <width>x<height> [out.png] [full]``

The screenshot argument is optional; ``full`` captures the whole page instead of the viewport.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

EXPRESSION = r"""
(() => {
  const box = (selector) => {
    const element = document.querySelector(selector);
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {
      top: Math.round(rect.top + window.scrollY),
      left: Math.round(rect.left),
      width: Math.round(rect.width),
      right: Math.round(rect.right),
    };
  };
  const display = (selector) => {
    const element = document.querySelector(selector);
    return element ? getComputedStyle(element).display : 'missing';
  };
  const controlWidth = (selector) => {
    const element = document.querySelector(selector);
    if (!element) return 'missing';
    const rect = element.getBoundingClientRect();
    return { width: Math.round(rect.width), parentWidth: Math.round(element.parentElement.getBoundingClientRect().width) };
  };
  const offenders = [];
  document.querySelectorAll('body *').forEach((element) => {
    const rect = element.getBoundingClientRect();
    if (rect.right > window.innerWidth + 1) {
      const name = element.tagName.toLowerCase()
        + (element.className ? '.' + String(element.className).trim().split(/\s+/).join('.') : '');
      offenders.push({ name, right: Math.round(rect.right), width: Math.round(rect.width) });
    }
  });
  return JSON.stringify({
    viewport: { width: window.innerWidth, height: window.innerHeight },
    scrollWidth: document.documentElement.scrollWidth,
    offenders: offenders.slice(0, 8),
    boxes: {
      header: box('.site-header'),
      nav: box('.site-header nav'),
      hero: box('.hero-copy'),
      heroActions: box('.hero-actions'),
      overview: box('.study-overview'),
      overviewGrid: box('.overview-grid'),
      shell: box('.practice-shell'),
      readingPane: box('.reading-pane'),
      answerPane: box('.answer-pane'),
      firstCard: box('.practice-book-card'),
      toolbar: box('.library-toolbar'),
      productCard: box('.product-card'),
      productLink: box('.product-link'),
      picker: box('.task-picker'),
      writingResult: box('#writing-result'),
      resumeCard: box('.resume-card'),
      resumeAction: box('.resume-action'),
      reviewPanel: box('.review-panel'),
      firstQuickRow: box('.quick-row'),
      toolsHeading: box('.tools-heading'),
      jobsTable: box('.jobs-table'),
      firstJobRow: box('.jobs-table tbody tr'),
      bottomNav: box('.bottom-nav'),
      submitDock: box('.submit-dock'),
      libraryToolbar: box('.library-toolbar'),
      librarySearch: box('.library-search'),
      stateTabs: box('.filter-tabs'),
    },
    checks: {
      writingResultDisplay: display('#writing-result'),
      activeFiltersDisplay: display('#kn-active-filters'),
      radioWidth: controlWidth('input[type=radio]'),
      checkboxWidth: controlWidth('input[type=checkbox]'),
      inlineHandlers: [].slice.call(document.querySelectorAll('[onclick],[onchange],[onsubmit]')).length,
      headerHeight: Math.round(document.querySelector('.site-header').getBoundingClientRect().height),
      navGroups: document.querySelectorAll('.site-header .nav-group').length,
      visibleNavLinks: [].slice.call(document.querySelectorAll('.site-header nav a')).filter((a) => a.getBoundingClientRect().width > 0).length,
      bottomNavDisplay: display('.bottom-nav'),
      bottomNavLinks: [].slice.call(document.querySelectorAll('.bottom-nav a')).filter((a) => a.getBoundingClientRect().width > 0).length,
      bodyPaddingBottom: Math.round(parseFloat(getComputedStyle(document.body).paddingBottom) || 0),
      toolbarDisplay: display('.library-toolbar'),
      mobileQuery700: window.matchMedia('(max-width:700px)').matches,
      sheets: [].slice.call(document.querySelectorAll('link[rel=stylesheet]')).map((l) => l.getAttribute('href')),
      stateTabsWrap: (document.querySelector('.filter-tabs') ? getComputedStyle(document.querySelector('.filter-tabs')).flexWrap : null),
    },
  });
})()
"""


class Socket:
    """Just enough WebSocket to exchange CDP messages."""

    def __init__(self, url: str) -> None:
        _, rest = url.split("://", 1)
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.socket = socket.create_connection((host, int(port)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(handshake.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.socket.recv(4096)
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        assert f"sec-websocket-accept: {expected}".lower() in response.decode(errors="ignore").lower()

    def send_text(self, payload: str) -> None:
        data = payload.encode()
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        self.socket.sendall(bytes(header) + bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)))

    def read_text(self) -> str:
        first = self._recv_exact(2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length)
        if opcode == 0x9:  # ping
            self.socket.sendall(b"\x8a\x80" + os.urandom(4))
            return self.read_text()
        return payload.decode("utf-8", "replace")

    def _recv_exact(self, count: int) -> bytes:
        buffer = b""
        while len(buffer) < count:
            chunk = self.socket.recv(count - len(buffer))
            if not chunk:
                raise RuntimeError("websocket 已关闭")
            buffer += chunk
        return buffer

    def close(self) -> None:
        self.socket.close()


def main() -> None:
    url, size = sys.argv[1], sys.argv[2]
    screenshot = sys.argv[3] if len(sys.argv) > 3 else ""
    full_page = len(sys.argv) > 4 and sys.argv[4] == "full"
    width, height = (int(part) for part in size.split("x"))
    # Unique profile + auto-picked port so concurrent runs can never share a browser.
    profile = Path(f"/tmp/chrome-cdp-measure-{os.getpid()}")
    subprocess.run(["rm", "-rf", str(profile)], check=False)
    chrome = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--disable-extensions",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port = 0
        for _ in range(120):
            active_port = profile / "DevToolsActivePort"
            if active_port.exists():
                lines = active_port.read_text().splitlines()
                if lines and lines[0].strip().isdigit():
                    port = int(lines[0])
                    break
            if chrome.poll() is not None:
                raise SystemExit("Chrome 提前退出")
            time.sleep(0.25)
        if not port:
            raise SystemExit("无法取得 DevTools 端口")

        target = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                    pages = json.load(response)
                target = next((page for page in pages if page.get("type") == "page"), None)
            except Exception:
                target = None
            if target:
                break
            time.sleep(0.5)
        if not target:
            raise SystemExit("无法连接 DevTools")

        client = Socket(target["webSocketDebuggerUrl"])
        counter = 0

        def call(method: str, **params: object) -> dict:
            nonlocal counter
            counter += 1
            client.send_text(json.dumps({"id": counter, "method": method, "params": params}))
            while True:
                message = json.loads(client.read_text())
                if message.get("id") == counter:
                    return message

        call("Page.enable")
        # Force the real viewport: Chrome keeps a minimum window width on macOS, so
        # --window-size alone lays the page out wider than the screenshot crop.
        call(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=1,
            mobile=width < 700,
        )
        call("Page.navigate", url=url)
        time.sleep(3)
        result = call("Runtime.evaluate", expression=EXPRESSION, returnByValue=True)
        print(json.dumps(json.loads(result["result"]["result"]["value"]), ensure_ascii=False, indent=2))
        if screenshot:
            full = call("Page.captureScreenshot", format="png", captureBeyondViewport=full_page)
            Path(screenshot).write_bytes(base64.b64decode(full["result"]["data"]))
            print(f"screenshot -> {screenshot}")
        client.close()
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
        subprocess.run(["rm", "-rf", str(profile)], check=False)


if __name__ == "__main__":
    main()
