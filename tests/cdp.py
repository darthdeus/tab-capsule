"""Minimal Chrome DevTools Protocol driver for the browser tests, using only the stdlib.

Chrome is started with --remote-debugging-pipe: it reads protocol messages from file
descriptor 3 and writes replies/events to descriptor 4, each a JSON object terminated by
a NUL byte. That avoids WebSocket clients, Node.js and any package installation; the
only requirement is a Chrome/Chromium executable.
"""

from __future__ import annotations

import fcntl
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

TIMEOUT = 10.0
CHROME_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)
# Checked after PATH. macOS apps are not on PATH; ~ is the user's own Applications folder.
CHROME_LOCATIONS = tuple(
    f"{root}/{app}.app/Contents/MacOS/{app}"
    for root in ("/Applications", "~/Applications")
    for app in ("Google Chrome", "Chromium", "Google Chrome for Testing")
) + ("/opt/google/chrome/chrome",)


def find_chrome() -> str | None:
    """CHROME_BIN wins; then a Chrome/Chromium name on PATH; then the usual install
    locations, so a standard macOS (/Applications) or Linux (/opt) install needs no setup."""
    if os.environ.get("CHROME_BIN"):
        return os.path.abspath(os.environ["CHROME_BIN"])
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for location in CHROME_LOCATIONS:
        path = os.path.expanduser(location)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class ProtocolError(RuntimeError):
    pass


class Browser:
    """One headless Chrome process; pages get isolated browser contexts."""

    def __init__(self, executable: str) -> None:
        self.profile = tempfile.mkdtemp(prefix="tab-capsule-chrome-")
        to_chrome_r, self._to_chrome = os.pipe()
        self._from_chrome, from_chrome_w = os.pipe()

        def place_pipe_fds() -> None:
            # Move both ends above the target numbers first so the dup2 calls cannot
            # clobber each other, then expose them to Chrome as fds 3 and 4.
            read_end = fcntl.fcntl(to_chrome_r, fcntl.F_DUPFD, 10)
            write_end = fcntl.fcntl(from_chrome_w, fcntl.F_DUPFD, 10)
            os.dup2(read_end, 3)
            os.dup2(write_end, 4)

        args = [
            executable,
            "--headless",
            "--remote-debugging-pipe",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-dev-shm-usage",
            "--mute-audio",
            "about:blank",
        ]
        if os.geteuid() == 0:
            # Chrome refuses to start as root with its sandbox (common in containers).
            args.insert(1, "--no-sandbox")
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=place_pipe_fds,
            pass_fds=(3, 4),
        )
        os.close(to_chrome_r)
        os.close(from_chrome_w)

        self._next_id = 0
        self._replies: dict[int, dict[str, Any]] = {}
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._listeners: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        threading.Thread(target=self._read, daemon=True).start()
        self.send("Target.setDiscoverTargets", {"discover": False})

    # Transport

    def _read(self) -> None:
        buffer = b""
        while True:
            try:
                chunk = os.read(self._from_chrome, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                self._inbox.put(None)
                return
            buffer += chunk
            while b"\0" in buffer:
                message, buffer = buffer.split(b"\0", 1)
                self._inbox.put(json.loads(message))

    def _write(self, method: str, params: dict[str, Any], session: str | None) -> int:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method, "params": params}
        if session:
            message["sessionId"] = session
        os.write(self._to_chrome, json.dumps(message).encode() + b"\0")
        return self._next_id

    def _pump(self, timeout: float) -> None:
        """Handle one incoming message: store a reply or dispatch an event."""
        try:
            message = self._inbox.get(timeout=timeout)
        except queue.Empty:
            return
        if message is None:
            raise ProtocolError("Chrome closed the debugging pipe")
        if "id" in message:
            self._replies[message["id"]] = message
            return
        for listener in self._listeners.get(message.get("sessionId", ""), []):
            listener(message)

    def send(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None
    ) -> dict[str, Any]:
        number = self._write(method, params or {}, session)
        deadline = time.monotonic() + TIMEOUT
        while number not in self._replies:
            if time.monotonic() > deadline:
                raise TimeoutError(f"no reply to {method}")
            self._pump(0.05)
        reply = self._replies.pop(number)
        if "error" in reply:
            raise ProtocolError(f"{method}: {reply['error'].get('message')}")
        result: dict[str, Any] = reply.get("result", {})
        return result

    def send_nowait(self, method: str, params: dict[str, Any], session: str) -> None:
        """For event handlers: a reply is stored and ignored rather than awaited."""
        self._write(method, params, session)

    def idle(self, seconds: float) -> None:
        """Let time pass while still dispatching events (e.g. request interception)."""
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            self._pump(min(left, 0.05))
        self._replies.clear()

    def listen(self, session: str, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.setdefault(session, []).append(listener)

    # Lifecycle

    def new_page(self, timezone: str = "Europe/Prague", locale: str = "en-GB") -> Page:
        context = self.send("Target.createBrowserContext", {"disposeOnDetach": True})
        context_id = context["browserContextId"]
        target = self.send(
            "Target.createTarget", {"url": "about:blank", "browserContextId": context_id}
        )
        attached = self.send(
            "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
        )
        return Page(self, context_id, attached["sessionId"], timezone, locale)

    def close(self) -> None:
        try:
            self.send("Browser.close")
        except (ProtocolError, TimeoutError, OSError):
            pass
        try:
            self.process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        os.close(self._to_chrome)
        shutil.rmtree(self.profile, ignore_errors=True)


KEYS = {
    "/": {"key": "/", "code": "Slash", "windowsVirtualKeyCode": 191, "text": "/"},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
}


class Page:
    """A tab in its own browser context, recording errors and blocked web requests."""

    def __init__(
        self, browser: Browser, context_id: str, session: str, timezone: str, locale: str
    ) -> None:
        self.browser = browser
        self.context_id = context_id
        self.session = session
        self.errors: list[str] = []
        self.requests: list[str] = []
        browser.listen(session, self._event)
        self.send("Runtime.enable")
        self.send("Page.enable")
        # Every http(s) request is recorded and failed; tests assert none happened.
        self.send(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "http://*"}, {"urlPattern": "https://*"}]},
        )
        self.send("Emulation.setTimezoneOverride", {"timezoneId": timezone})
        self.send("Emulation.setLocaleOverride", {"locale": locale})

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.browser.send(method, params, self.session)

    def _event(self, message: dict[str, Any]) -> None:
        method, params = message["method"], message.get("params", {})
        if method == "Runtime.exceptionThrown":
            details = params["exceptionDetails"]
            self.errors.append(details.get("exception", {}).get("description") or details["text"])
        elif method == "Runtime.consoleAPICalled" and params["type"] in ("error", "assert"):
            self.errors.append(
                " ".join(str(a.get("value", a.get("description", ""))) for a in params["args"])
            )
        elif method == "Fetch.requestPaused":
            self.requests.append(params["request"]["url"])
            self.browser.send_nowait(
                "Fetch.failRequest",
                {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
                self.session,
            )

    def close(self) -> None:
        self.browser.send("Target.disposeBrowserContext", {"browserContextId": self.context_id})

    # Reading the page

    def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript (promises are awaited) and return a JSON-compatible value."""
        result = self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise ProtocolError(details.get("exception", {}).get("description") or details["text"])
        return result["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not self.evaluate(expression):
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for: {expression}")
            self.browser.idle(0.025)

    def wait(self, seconds: float) -> None:
        self.browser.idle(seconds)

    def goto(self, url: str) -> None:
        self.send("Page.navigate", {"url": url})
        self.wait_for(f"location.href === {json.dumps(url)} && document.readyState === 'complete'")

    def _all(self, selector: str, body: str) -> str:
        return f"Array.from(document.querySelectorAll({json.dumps(selector)}), e => {body})"

    def count(self, selector: str) -> int:
        result: int = self.evaluate(f"document.querySelectorAll({json.dumps(selector)}).length")
        return result

    def texts(self, selector: str) -> list[str]:
        """textContent of every match, in document order."""
        result: list[str] = self.evaluate(self._all(selector, "e.textContent"))
        return result

    def _one(self, selector: str, index: int) -> str:
        """JavaScript for the index-th match (negative counts from the end), or an error."""
        return (
            f"(() => {{ const all = document.querySelectorAll({json.dumps(selector)});"
            f" const e = all[{index} < 0 ? all.length + {index} : {index}];"
            f" if (!e) throw new Error('no element ' + {json.dumps(selector)}"
            f" + ' at index {index} of ' + all.length); return e; }})()"
        )

    def inner_text(self, selector: str, index: int = 0) -> str:
        result: str = self.evaluate(f"{self._one(selector, index)}.innerText")
        return result

    def attribute(self, selector: str, name: str, index: int = 0) -> str | None:
        result: str | None = self.evaluate(
            f"{self._one(selector, index)}.getAttribute({json.dumps(name)})"
        )
        return result

    def value(self, selector: str) -> str:
        result: str = self.evaluate(f"{self._one(selector, 0)}.value")
        return result

    def is_focused(self, selector: str) -> bool:
        return bool(self.evaluate(f"{self._one(selector, 0)} === document.activeElement"))

    # Input, dispatched as trusted browser events like a real user

    def click(self, selector: str, index: int = 0) -> None:
        """Scroll the element into view and click its centre, refusing obscured targets."""
        x, y = self.evaluate(
            f"(() => {{ const e = {self._one(selector, index)};"
            " e.scrollIntoView({block: 'center', inline: 'center'});"
            " const r = e.getBoundingClientRect();"
            " const x = r.left + r.width / 2, y = r.top + r.height / 2;"
            " const hit = document.elementFromPoint(x, y);"
            " if (!r.width || !r.height || !hit || !(hit === e || e.contains(hit)))"
            "   throw new Error('element is hidden or covered at its centre');"
            " return [x, y]; })()"
        )
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.send(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    def click_button(self, label: str) -> None:
        """Click the single button whose trimmed text equals label exactly."""
        index = self.evaluate(
            "Array.from(document.querySelectorAll('button'))"
            f".findIndex(b => b.textContent.trim() === {json.dumps(label)})"
        )
        if index < 0:
            raise AssertionError(f"no button labelled {label!r}")
        self.click("button", index)

    def press(self, key: str) -> None:
        spec = KEYS[key]
        down = dict(spec, type="keyDown" if "text" in spec else "rawKeyDown")
        self.send("Input.dispatchKeyEvent", down)
        self.send(
            "Input.dispatchKeyEvent",
            {k: v for k, v in spec.items() if k != "text"} | {"type": "keyUp"},
        )

    def fill(self, selector: str, value: str) -> None:
        """Replace an input's value the way typing does, firing a trusted input event."""
        self.evaluate(f"(() => {{ const e = {self._one(selector, 0)}; e.focus(); e.select(); }})()")
        if value:
            self.send("Input.insertText", {"text": value})
        elif self.value(selector):
            self.press("Backspace")
