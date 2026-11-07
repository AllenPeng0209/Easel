"""Short-lived, account-owned views of login pages.

Only image and bounded input operations cross the HTTP boundary. Browser/CDP
objects and endpoint addresses remain inside the backend; this is not a desktop
viewer or a general-purpose browser automation API.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from contextlib import suppress
import inspect
from io import BytesIO
import math
import os
import re
import time
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException


def enabled() -> bool:
    return os.environ.get("EASEL_REMOTE_LOGIN", "").strip() == "1"


async def _call(callback):
    result = callback()
    return await result if inspect.isawaitable(result) else result


def _invalid():
    return HTTPException(400, "登录操作无效，请刷新登录窗口后重试")


def _css_screenshot(data, width, height):
    """Normalize browser pixels to CSS pixels without trusting page JS/DPR."""
    from PIL import Image
    raw = base64.b64decode(data, validate=True)
    with Image.open(BytesIO(raw)) as image:
        if image.width * image.height > 32_000_000:
            raise ValueError("登录窗口图像过大")
        if image.size == (width, height):
            return data
        resized = image.resize((width, height), Image.Resampling.LANCZOS)
        output = BytesIO()
        resized.convert("RGB").save(output, format="JPEG", quality=85)
        return base64.b64encode(output.getvalue()).decode("ascii")


_KEYS = frozenset({
    "Enter", "Tab", "Shift+Tab", "Backspace", "Delete", "Escape", "Space",
    "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End",
    "PageUp", "PageDown", "Control+a", "Control+A", "Meta+a", "Meta+A",
    "Shift+ArrowLeft", "Shift+ArrowRight", "Shift+ArrowUp", "Shift+ArrowDown",
    "Shift+Home", "Shift+End", "Shift+PageUp", "Shift+PageDown",
    "Control+Z", "Control+Y", "Control+Shift+Z",
})


class RemoteLoginSession:
    def __init__(self, project_id, actor_id, platform, task_id, *, is_active=None, cancel=None, on_close=None):
        self.id = uuid.uuid4().hex
        self.project_id = project_id
        self.actor_id = actor_id
        self.platform = platform
        self.task_id = task_id
        self._is_active = is_active
        self._cancel = cancel
        self._on_close = on_close
        self._deadline = time.monotonic() + 600
        self._closed = False
        self._monitor_task = None
        self._finish_task = None
        self._lock = asyncio.Lock()
        self._frame_lock = asyncio.Lock()
        self._qr_lock = asyncio.Lock()
        self._qr_epoch = 0
        self._qr_navigation = {}
        self._context = None
        self._root_page = None
        self._allowed_pages = None
        self._listeners = []
        self._observe_page = None
        self._generation = 0
        self._pages = {}
        self._selected = ""
        self._dimensions = {}
        self._pressed = {}
        self._pending_releases = []
        self._pointer_positions = {}
        self._cdp_targets = {}
        self._pending_cdp_releases = []
        self._frame = None
        self._frame_at = 0.0
        self._frame_failures = 0
        self._frame_retry_after = 0.0
        self._input_revision = 0
        self._capture_cdps = {}
        self._capture_cleanup = set()
        self._endpoint = ""
        self._page_target_id = ""
        self._driver = None
        self._browser = None
        self._cdp_retry_after = 0.0
        self._cdp_failures = 0
        self._input_times = []
        self._start_monitor()

    def _start_monitor(self):
        if self._monitor_task is not None or self._closed:
            return
        try:
            self._monitor_task = asyncio.get_running_loop().create_task(self._monitor())
        except RuntimeError:
            # Synchronous callers can create a session before the first request.
            # The first async operation starts its lifecycle watcher.
            pass

    async def _monitor(self):
        try:
            while not self._closed:
                await asyncio.sleep(1)
                async with self._lock:
                    if not await self._check():
                        return
                    await self._flush_releases()
        except asyncio.CancelledError:
            pass
        except Exception:
            async with self._lock:
                self._begin_close_locked(True)

    def public(self):
        return {"id": self.id, "platform": self.platform}

    def _forget_pages(self):
        for page, listener in self._qr_navigation.items():
            with suppress(Exception):
                page.remove_listener('framenavigated', listener)
        self._qr_navigation.clear()
        for page in list(self._capture_cdps):
            self._discard_capture_cdp(page)
        for identifier, buttons in self._pressed.items():
            page = self._pages.get(identifier)
            if page is not None and buttons:
                self._pending_releases.append((page, tuple(buttons)))
        for page, listener in self._listeners:
            with suppress(Exception):
                page.remove_listener("popup", listener)
        self._listeners.clear()
        self._observe_page = None
        self._pages.clear()
        self._selected = ""
        self._dimensions.clear()
        self._pressed.clear()
        self._pointer_positions.clear()
        self._cdp_targets.clear()
        self._frame = None
        self._frame_at = 0.0
        self._frame_failures = 0
        self._frame_retry_after = 0.0
        self._input_revision += 1
        self._generation += 1

    def _detach_capture_cdp(self, cdp):
        async def detach():
            with suppress(Exception):
                await asyncio.wait_for(cdp.detach(), timeout=1)
        task = asyncio.create_task(detach())
        self._capture_cleanup.add(task)
        def finished(done):
            self._capture_cleanup.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)

    def _discard_capture_cdp(self, page):
        entry = self._capture_cdps.pop(page, None)
        if entry is not None:
            self._detach_capture_cdp(entry[1])

    async def _release_buttons(self, page, buttons, authorize=None):
        for button in buttons:
            if authorize is not None:
                await _call(authorize)
            if page.is_closed():
                break
            with suppress(Exception):
                await asyncio.wait_for(page.mouse.up(button=button), timeout=1)

    async def _flush_releases(self, authorize=None):
        # bind_context is synchronous because it is also a login callback.
        # Drain releases from the old context before exposing/using the new one.
        while self._pending_releases:
            page, buttons = self._pending_releases[0]
            await self._release_buttons(page, buttons, authorize)
            self._pending_releases.pop(0)

    async def _select_page(self, identifier, authorize=None):
        if identifier == self._selected:
            return
        generation = self._generation
        previous = self._selected
        page = self._pages.get(previous)
        buttons = tuple(self._pressed.get(previous, ()))
        if page is not None and buttons:
            await self._release_buttons(page, buttons, authorize)
            self._pressed.pop(previous, None)
        if generation == self._generation:
            self._selected = identifier
            self._frame = None

    def bind_context(self, context, page=None):
        """Bind one login context, optionally limited to a page and its popups.

        Native account managers reuse contexts for background checks. They pass
        the interactive page explicitly so those background pages stay private.
        Subprocess contexts are dedicated to one login and may expose all tabs.
        """
        if self._closed:
            return
        if context is self._context and page is self._root_page:
            return
        if page is not None and page.context is not context:
            raise ValueError("登录页面不属于本次浏览器")
        self._forget_pages()
        self._context, self._root_page = context, page
        self._allowed_pages = set() if page is not None else None
        generation = self._generation

        def observe(candidate):
            if self._closed or self._generation != generation:
                return
            if candidate.context is not context or candidate in self._allowed_pages:
                return
            self._allowed_pages.add(candidate)
            candidate.on("popup", observe)
            self._listeners.append((candidate, observe))
            self._frame = None

        if page is not None:
            self._observe_page = observe
            observe(page)

    def bind_cdp(self, endpoint, *, page_target_id=""):
        """Accept only an endpoint discovered by a backend-owned login process."""
        try:
            parsed = urlsplit(endpoint)
            valid = (parsed.scheme in {"http", "ws"} and parsed.hostname == "127.0.0.1"
                     and 1024 <= parsed.port <= 65535 and not parsed.username
                     and not parsed.password and not parsed.query and not parsed.fragment
                     and (parsed.path in {"", "/"} if parsed.scheme == "http"
                          else parsed.path.startswith("/devtools/browser/")))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("登录浏览器连接地址无效")
        if not isinstance(page_target_id, str) or (page_target_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", page_target_id)):
            raise ValueError("登录页面标识无效")
        if self._endpoint and self._endpoint != endpoint:
            raise ValueError("登录浏览器已经绑定")
        if self._page_target_id and self._page_target_id != page_target_id:
            raise ValueError("登录页面已经绑定")
        if not self._closed:
            self._endpoint = endpoint
            self._page_target_id = page_target_id

    def _begin_close_locked(self, cancel, authorize=None):
        if self._closed:
            return None
        self._closed = True
        self._forget_pages()
        self._context = self._root_page = None
        self._allowed_pages = None
        self._endpoint = ""
        self._page_target_id = ""
        monitor, self._monitor_task = self._monitor_task, None
        if monitor is not None and monitor is not asyncio.current_task():
            monitor.cancel()
        driver, self._driver = self._driver, None
        self._browser = None
        async def finalize():
            authorization_error = None
            try:
                try:
                    await asyncio.gather(*tuple(self._capture_cleanup), return_exceptions=True)
                    try:
                        if authorize is not None:
                            await _call(authorize)
                        await self._flush_releases(authorize)
                    except Exception as error:
                        # A mouse-up is still input. Revocation forbids further
                        # input, but cannot abandon resources already owned by
                        # the closing session or release their capacity early.
                        authorization_error = error
                        self._pending_releases.clear()
                finally:
                    try:
                        # This separate driver owns only a CDP connection.
                        # Detach is private cleanup, never a privileged input.
                        if driver is not None:
                            with suppress(Exception):
                                await asyncio.wait_for(driver.stop(), timeout=4)
                    finally:
                        if cancel and self._cancel is not None:
                            await _call(self._cancel)
                if authorize is not None and authorization_error is None:
                    try:
                        await _call(authorize)
                    except Exception as error:
                        authorization_error = error
            finally:
                if self._on_close is not None:
                    await _call(self._on_close)
            if authorization_error is not None:
                raise authorization_error
        # Cancellation may await a login runner whose finally block calls stop.
        # Finalize outside the lock and let an already-closed stop return at once.
        self._finish_task = asyncio.create_task(finalize())
        # A monitor/request can finish before cleanup; consume any background
        # exception without printing URLs or credentials from callback errors.
        self._finish_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return self._finish_task

    async def stop(self, cancel=True, *, authorize=None):
        async with self._lock:
            if authorize is not None:
                await _call(authorize)
            finalizer = self._begin_close_locked(cancel, authorize)
        if finalizer is not None:
            await asyncio.shield(finalizer)

    async def _check(self, authorize=None):
        if authorize is not None:
            await _call(authorize)
        if self._closed:
            return False
        if time.monotonic() >= self._deadline:
            self._begin_close_locked(True)
            return False
        if self._is_active is not None and not await _call(self._is_active):
            self._begin_close_locked(False)
            return False
        return not self._closed

    async def _connect(self):
        if self._browser is not None and not self._browser.is_connected():
            # A detached CDP client leaves stale Playwright Context/Page objects.
            # Forget only our connection, preserving the exact endpoint/target
            # and the externally owned browser/profile for a bounded retry.
            driver, self._driver = self._driver, None
            self._browser = None
            for identifier, buttons in self._pressed.items():
                target = self._cdp_targets.get(self._pages.get(identifier))
                position = self._pointer_positions.get(identifier)
                if target and buttons and position is not None:
                    self._pending_cdp_releases.append((target, tuple(buttons), position))
            self._forget_pages()
            self._context = self._root_page = None
            self._allowed_pages = None
            await self._flush_releases()
            if driver is not None:
                with suppress(Exception):
                    await asyncio.wait_for(driver.stop(), timeout=4)
        if self._context is not None or not self._endpoint:
            return
        if time.monotonic() < self._cdp_retry_after:
            return
        from playwright.async_api import async_playwright
        endpoint, generation = self._endpoint, self._generation
        driver = await async_playwright().start()
        try:
            browser = await driver.chromium.connect_over_cdp(endpoint, timeout=4000)
            contexts = browser.contexts
            # The login helper launches one persistent context. Multiple contexts
            # indicate that this endpoint is not the dedicated browser expected.
            if len(contexts) != 1:
                raise ValueError("登录浏览器会话不唯一")
            context = contexts[0]
            root = None
            targets = {}
            for candidate in context.pages:
                cdp = await context.new_cdp_session(candidate)
                try:
                    targets[candidate] = (await cdp.send("Target.getTargetInfo")).get("targetInfo", {})
                finally:
                    await cdp.detach()
            if self._page_target_id:
                root = next((candidate for candidate, info in targets.items()
                             if info.get("targetId") == self._page_target_id), None)
            else:
                # Compatibility for a dedicated browser with exactly one root.
                # CDP page ordering differs from the runner's context ordering;
                # never guess among background tabs based on list position.
                roots = [candidate for candidate, info in targets.items() if not info.get("openerId")]
                if len(roots) == 1:
                    root = roots[0]
            if root is None:
                raise ValueError("尚未取得本次登录页面")
            if self._closed or self._endpoint != endpoint or self._generation != generation:
                raise ValueError("登录浏览器连接已结束或切换")
        except BaseException:
            await driver.stop()
            self._cdp_failures += 1
            self._cdp_retry_after = time.monotonic() + min(8, 2 ** min(self._cdp_failures - 1, 3))
            raise
        self._cdp_failures = 0
        self._cdp_retry_after = 0.0
        self._driver, self._browser = driver, browser
        self.bind_context(context, page=root)
        self._cdp_targets = {candidate: info.get("targetId") for candidate, info in targets.items()}
        # The old client may have disconnected between pointer down/up. Release
        # only our recorded buttons on the exact same target, at its last known
        # pointer position. Never replay a click, text input, or a drag path.
        while self._pending_cdp_releases:
            target, buttons, (x, y) = self._pending_cdp_releases[0]
            candidate = next((page for page, info in targets.items() if info.get("targetId") == target), None)
            if candidate is not None:
                cdp = await context.new_cdp_session(candidate)
                try:
                    remaining = set(buttons)
                    for button in buttons:
                        remaining.discard(button)
                        await cdp.send("Input.dispatchMouseEvent", {
                            "type": "mouseReleased", "x": x, "y": y, "button": button,
                            "buttons": sum({"left": 1, "right": 2}[value] for value in remaining),
                            "clickCount": 0,
                        })
                finally:
                    await cdp.detach()
            self._pending_cdp_releases.pop(0)
        # Playwright does not reconstruct Page.opener() for popups that existed
        # before CDP attachment. The browser's target metadata retains this tree.
        allowed_targets = {targets[root].get("targetId")}
        for _ in range(len(targets)):
            found = False
            for candidate, info in targets.items():
                if candidate not in self._allowed_pages and info.get("openerId") in allowed_targets:
                    self._observe_page(candidate)
                    allowed_targets.add(info.get("targetId"))
                    found = True
            if not found:
                break

    async def _discover_popups(self):
        """Recover opener descendants created before the CDP bridge attached."""
        context, generation = self._context, self._generation
        if context is None or self._allowed_pages is None or self._observe_page is None:
            return
        pending = [page for page in context.pages if page not in self._allowed_pages and not page.is_closed()]
        for _ in range(len(pending)):
            discovered = False
            for page in list(pending):
                opener = await page.opener()
                if generation != self._generation or self._closed:
                    return
                if opener in self._allowed_pages:
                    self._observe_page(page)
                    pending.remove(page)
                    discovered = True
            if not discovered:
                return

    async def _sync_pages(self, authorize=None):
        candidates = list(self._context.pages) if self._context is not None else []
        live = [page for page in candidates if not page.is_closed()
                and (self._allowed_pages is None or page in self._allowed_pages)]
        for candidate in list(self._capture_cdps):
            if candidate not in live:
                self._discard_capture_cdp(candidate)
        selected = self._selected
        self._pages = {identifier: page for identifier, page in self._pages.items() if page in live}
        for page in live:
            if page not in self._pages.values():
                self._pages[uuid.uuid4().hex] = page
                selected = next(reversed(self._pages))
                self._frame = None
        if selected not in self._pages:
            selected = next(reversed(self._pages), "")
        await self._select_page(selected, authorize)
        return self._pages.get(self._selected)

    async def _capture_viewport(self, page):
        # A live login view must remain visible while the site loads fonts.
        # Page.screenshot() waits for document.fonts.ready and can time out for
        # every frame on remote/CDN-restricted login pages. CDP captures the
        # rendered viewport immediately, including the browser's fallback text.
        generation = self._generation
        entry = self._capture_cdps.get(page)
        if entry is None:
            cdp = await page.context.new_cdp_session(page)
            if self._closed or generation != self._generation or page.context is not self._context:
                self._detach_capture_cdp(cdp)
                raise ValueError("登录页面已经结束或切换")
            self._capture_cdps[page] = (generation, cdp)
        else:
            cdp = entry[1]
        try:
            metrics = await cdp.send("Page.getLayoutMetrics")
            viewport = metrics["cssLayoutViewport"]
            width, height = int(viewport["clientWidth"]), int(viewport["clientHeight"])
            if not (1 <= width <= 4096 and 1 <= height <= 4096):
                raise ValueError("登录窗口尺寸无效")
            result = await cdp.send("Page.captureScreenshot", {
                "format": "jpeg", "quality": 85, "fromSurface": True,
                "captureBeyondViewport": False,
                # Layout client dimensions exclude classic scrollbars. Capture
                # that exact content rectangle before pixel normalization;
                # scaling a full screenshot including gutters shifts clicks.
                "clip": {"x": viewport["pageX"], "y": viewport["pageY"],
                         "width": width, "height": height, "scale": 1},
            })
        except BaseException:
            if self._capture_cdps.get(page) == (generation, cdp):
                self._discard_capture_cdp(page)
            raise
        image = await asyncio.to_thread(_css_screenshot, result["data"], width, height)
        return width, height, image

    async def frame(self, *, authorize=None):
        self._start_monitor()
        if self._frame_lock.locked():
            # A second HTTP poll must not enqueue another expensive capture.
            async with self._lock:
                if not await self._check(authorize):
                    return {"state": "closed"}
                if self._frame is not None and time.monotonic() - self._frame_at < 0.5:
                    return dict(self._frame)
                return {"state": "starting"}
        async with self._frame_lock:
            generation = selected = page = None
            try:
                async with self._lock:
                    if not await self._check(authorize):
                        return {"state": "closed"}
                    if time.monotonic() < self._frame_retry_after:
                        return {"state": "starting", "message": "平台画面响应较慢，正在重试连接"}
                # CDP connection and popup discovery may perform network I/O.
                # They never publish images; authorization and ownership are
                # checked again after setup, before starting the snapshot.
                await self._connect()
                await self._discover_popups()
                async with self._lock:
                    if not await self._check(authorize):
                        return {"state": "closed"}
                    await self._flush_releases(authorize)
                    page = await self._sync_pages(authorize)
                    if page is None:
                        return {"state": "starting"}
                    generation, selected = self._generation, self._selected
                    revision = self._input_revision
                    candidates = list(self._pages.items())
                    if self._frame is not None and time.monotonic() - self._frame_at < 0.5:
                        return dict(self._frame)
                # Keep only the capture mutex while Chrome works. Mouse/key
                # input, permission revocation and close use the state lock and
                # must not wait behind a slow screenshot.
                width, height, screenshot = await asyncio.wait_for(self._capture_viewport(page), timeout=6)
                tabs = []
                for identifier, candidate in candidates:
                    try:
                        title = await asyncio.wait_for(candidate.title(), timeout=0.5)
                    except Exception:
                        # Navigation metadata must not hide a captured login page.
                        title = "登录页面"
                    tabs.append({"id": identifier, "title": str(title or "登录页面")[:120]})
                async with self._lock:
                    if not await self._check(authorize):
                        return {"state": "closed"}
                    if (generation != self._generation or selected != self._selected
                            or self._pages.get(selected) is not page or page.is_closed()):
                        return {"state": "starting"}
                    self._dimensions[selected] = (width, height)
                    result = {"state": "ready", "image": "data:image/jpeg;base64," + screenshot,
                              "width": width, "height": height, "pages": tabs, "pageId": selected}
                    self._frame_failures = 0
                    self._frame_retry_after = 0.0
                    # Input may have changed the page while Chrome captured it.
                    # Return the authorized frame, but do not cache it over the
                    # input invalidation; the next poll must capture fresh state.
                    self._frame = result if revision == self._input_revision else None
                    self._frame_at = time.monotonic()
                    return dict(result)
            except HTTPException:
                raise
            except Exception:
                # Browser exceptions often contain authentication URLs or input.
                # Never send their text to clients or log it in this layer.
                async with self._lock:
                    if not await self._check(authorize):
                        return {"state": "closed"}
                    if generation is not None and (generation != self._generation or selected != self._selected):
                        return {"state": "starting"}
                    self._frame_failures += 1
                    self._frame_retry_after = time.monotonic() + min(4, 2 ** min(self._frame_failures - 1, 2))
                    return {"state": "starting", "message": "正在连接平台登录窗口，请稍候"}

    def _validate(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"pageId", "actions"}:
            raise _invalid()
        identifier, actions = payload["pageId"], payload["actions"]
        if not isinstance(identifier, str) or identifier not in self._pages:
            raise HTTPException(409, "登录页面已更新，请等待新画面后重试")
        if not isinstance(actions, list) or not 1 <= len(actions) <= 64:
            raise _invalid()
        dimensions = self._dimensions.get(identifier)
        for action in actions:
            if not isinstance(action, dict):
                raise _invalid()
            kind = action.get("kind")
            if kind == "pointer":
                if not {"kind", "event", "x", "y"} <= action.keys() or action.keys() - {"kind", "event", "x", "y", "button"}:
                    raise _invalid()
                if (not isinstance(action["event"], str) or action["event"] not in {"down", "move", "up"}
                        or not isinstance(action.get("button", "left"), str)
                        or action.get("button", "left") not in {"left", "right"} or dimensions is None):
                    raise _invalid()
                for value, limit in zip((action["x"], action["y"]), dimensions):
                    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < limit:
                        raise _invalid()
            elif kind == "wheel":
                if set(action) != {"kind", "deltaX", "deltaY"}:
                    raise _invalid()
                if any(type(action[key]) not in (int, float) or not math.isfinite(action[key])
                       or abs(action[key]) > 4000 for key in ("deltaX", "deltaY")):
                    raise _invalid()
            elif kind == "key":
                if set(action) != {"kind", "key"} or not isinstance(action["key"], str) or action["key"] not in _KEYS:
                    raise _invalid()
            elif kind == "text":
                if (set(action) != {"kind", "text"} or not isinstance(action["text"], str)
                        or not 1 <= len(action["text"]) <= 4096
                        or any(ord(c) < 32 and c not in "\n\t" for c in action["text"])):
                    raise _invalid()
            elif kind == "page":
                if (set(action) != {"kind", "pageId"} or not isinstance(action["pageId"], str)
                        or action["pageId"] not in self._pages or len(actions) != 1):
                    raise _invalid()
            elif kind == "reset":
                if set(action) != {"kind"} or len(actions) != 1:
                    raise _invalid()
            else:
                raise _invalid()
        return identifier, actions

    def _qr_version(self, observed, page_id):
        subject = [self.id, self._generation, page_id, self._input_revision,
                   self._qr_epoch, observed.get('state'), observed.get('image', ''), observed.get('_source_key', '')]
        return hashlib.sha256(repr(subject).encode()).hexdigest()

    def _watch_qr_navigation(self, page):
        if page in self._qr_navigation:
            return
        def navigated(_):
            self._qr_epoch += 1
        page.on('framenavigated', navigated)
        self._qr_navigation[page] = navigated

    async def qrcode(self, *, authorize=None):
        """Read a small QR preview; only the connection task decides login success."""
        from easel import login_qr
        self._start_monitor()
        async with self._lock:
            if not await self._check(authorize):
                return {'state': 'closed'}
            if login_qr.get_qr_platform(self.platform) is None:
                return {'state': 'unavailable', 'message': '此平台请使用验证窗口完成登录'}
        if self._qr_lock.locked():
            return {'state': 'starting'}
        async with self._qr_lock:
            try:
                await asyncio.wait_for(self._connect(), timeout=4)
                await asyncio.wait_for(self._discover_popups(), timeout=1)
                async with self._lock:
                    if not await self._check(authorize):
                        return {'state': 'closed'}
                    page = await self._sync_pages(authorize)
                    if page is None:
                        return {'state': 'starting'}
                    self._watch_qr_navigation(page)
                    generation, selected, revision = self._generation, self._selected, self._input_revision
                    qr_epoch = self._qr_epoch
                observed = await asyncio.wait_for(login_qr.read_qr(page, self.platform), timeout=2)
                async with self._lock:
                    if not await self._check(authorize):
                        return {'state': 'closed'}
                    if (generation != self._generation or selected != self._selected
                            or revision != self._input_revision or qr_epoch != self._qr_epoch
                            or self._pages.get(selected) is not page or page.is_closed()
                            or not login_qr.same_source(page, self.platform, observed)):
                        return {'state': 'starting'}
                    state = observed['state']
                    result = {'state': state}
                    if state in {'ready', 'expired'}:
                        result.update(pageId=selected, version=self._qr_version(observed, selected))
                    if state == 'ready':
                        result['image'] = observed['image']
                    if 'message' in observed:
                        result['message'] = observed['message']
                    return result
            except HTTPException:
                raise
            except Exception:
                async with self._lock:
                    if not await self._check(authorize):
                        return {'state': 'closed'}
                return {'state': 'starting', 'message': '二维码正在准备，请稍候；也可打开验证窗口'}

    async def refresh_qrcode(self, payload, *, authorize=None):
        """Refresh one observed expired code through the platform's own control."""
        from easel import login_qr
        if (not isinstance(payload, dict) or set(payload) != {'pageId', 'version'}
                or not isinstance(payload.get('pageId'), str)
                or not isinstance(payload.get('version'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', payload['version'])):
            raise _invalid()
        if self._qr_lock.locked():
            raise HTTPException(409, '二维码正在更新，请稍候重试')
        async with self._qr_lock:
            async with self._lock:
                if not await self._check(authorize):
                    raise HTTPException(410, '本次登录已结束，请重新连接账号')
                if login_qr.get_qr_platform(self.platform) is None or payload['pageId'] != self._selected:
                    raise HTTPException(409, '登录页面已变化，请重新读取二维码')
                page = self._pages.get(self._selected)
                generation, revision = self._generation, self._input_revision
                if page is None or page.is_closed():
                    raise HTTPException(409, '登录页面已变化，请重新读取二维码')
            observed = await asyncio.wait_for(login_qr.read_qr(page, self.platform), timeout=2)
            control = await asyncio.wait_for(login_qr.refresh_control(page, self.platform, observed), timeout=1)
            try:
                async with self._lock:
                    if not await self._check(authorize):
                        raise HTTPException(410, '本次登录已结束，请重新连接账号')
                    if (generation != self._generation or revision != self._input_revision
                            or payload['pageId'] != self._selected or self._pages.get(self._selected) is not page
                            or observed.get('state') != 'expired' or control is None
                            or not login_qr.same_source(page, self.platform, observed)
                            or payload['version'] != self._qr_version(observed, self._selected)):
                        raise HTTPException(409, '二维码已更新，请重新读取；必要时打开验证窗口')
                    # Invalidate this attempt before input: retrying an ambiguous
                    # result must first read the current platform state.
                    self._qr_epoch += 1
                    self._frame = None
                    self._input_revision += 1
                    await control.click(timeout=750)
                    if not await self._check(authorize):
                        raise HTTPException(410, '本次登录已结束，请重新连接账号')
                    return {'ok': True}
            finally:
                if control is not None:
                    with suppress(Exception):
                        await asyncio.wait_for(control.dispose(), timeout=.5)

    async def input(self, payload, *, authorize=None):
        self._start_monitor()
        async with self._lock:
            if not await self._check(authorize):
                raise HTTPException(410, "本次登录已结束，请重新连接账号")
            await self._flush_releases(authorize)
            await self._sync_pages(authorize)
            identifier, actions = self._validate(payload)
            page, generation = self._pages[identifier], self._generation
            now = time.monotonic()
            self._input_times = [stamp for stamp in self._input_times if now - stamp < 1]
            if len(self._input_times) >= 30:
                raise HTTPException(429, "操作过快，请稍后重试")
            self._input_times.append(now)
            self._input_revision += 1
            self._frame = None
            try:
                for action in actions:
                    if not await self._check(authorize):
                        raise HTTPException(410, "本次登录已结束，请重新连接账号")
                    if generation != self._generation or self._pages.get(identifier) is not page or page.is_closed():
                        raise HTTPException(409, "登录页面已更新，请等待新画面后重试")
                    kind = action["kind"]
                    if kind == "pointer":
                        await page.mouse.move(action["x"], action["y"])
                        self._pointer_positions[identifier] = (action["x"], action["y"])
                        if self._browser is not None and page not in self._cdp_targets:
                            cdp = await self._context.new_cdp_session(page)
                            try:
                                self._cdp_targets[page] = (await cdp.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
                            finally:
                                await cdp.detach()
                        if action["event"] != "move":
                            if not await self._check(authorize):
                                raise HTTPException(410, "本次登录已结束，请重新连接账号")
                            if generation != self._generation or self._pages.get(identifier) is not page or page.is_closed():
                                raise HTTPException(409, "登录页面已更新，请等待新画面后重试")
                        button = action.get("button", "left")
                        if action["event"] == "down":
                            self._pressed.setdefault(identifier, set()).add(button)
                            await page.mouse.down(button=button)
                        elif action["event"] == "up":
                            await page.mouse.up(button=button)
                            self._pressed.setdefault(identifier, set()).discard(button)
                    elif kind == "wheel":
                        await page.mouse.wheel(action["deltaX"], action["deltaY"])
                    elif kind == "key":
                        await page.keyboard.press(action["key"])
                    elif kind == "text":
                        await page.keyboard.insert_text(action["text"])
                    elif kind == "reset":
                        for button in tuple(self._pressed.get(identifier, ())):
                            if not await self._check(authorize):
                                raise HTTPException(410, "本次登录已结束，请重新连接账号")
                            if generation != self._generation or self._pages.get(identifier) is not page or page.is_closed():
                                raise HTTPException(409, "登录页面已更新，请等待新画面后重试")
                            await page.mouse.up(button=button)
                            self._pressed.get(identifier, set()).discard(button)
                    else:
                        await self._select_page(action["pageId"], authorize)
                        await self._pages[self._selected].bring_to_front()
                self._frame = None
                return {"ok": True}
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(409, "平台登录页面暂不可操作，请等待画面更新后重试") from None


class RemoteLoginRegistry:
    def __init__(self):
        self._sessions = {}
        self._closing = False

    @staticmethod
    def _active_limit():
        try:
            configured = int(os.environ.get("EASEL_REMOTE_LOGIN_MAX_ACTIVE", "4"))
        except (TypeError, ValueError):
            return 4
        return configured if 1 <= configured <= 128 else 4

    def create(self, project_id, actor_id, platform, task_id, *, is_active=None, cancel=None, on_close=None):
        for key, value in list(self._sessions.items()):
            # A closed view can still own a browser whose cancellation/storage
            # flush is running. Hold its slot until all owned cleanup finishes.
            if value._closed and value._finish_task is not None and value._finish_task.done():
                self._sessions.pop(key, None)
        if not all(isinstance(value, str) and value for value in (project_id, actor_id, platform, task_id)):
            raise ValueError("登录会话缺少所属身份")
        if self._closing or len(self._sessions) >= self._active_limit():
            raise HTTPException(409, "已有登录窗口正在使用或关闭中，请先完成当前登录或等待窗口关闭后重试")
        session = RemoteLoginSession(project_id, actor_id, platform, task_id,
                                     is_active=is_active, cancel=cancel, on_close=on_close)
        self._sessions[session.id] = session
        return session

    def get(self, session_id, project_id, actor_id, platform):
        session = self._sessions.get(session_id)
        if (session is None or session.project_id != project_id
                or session.actor_id != actor_id or session.platform != platform):
            raise HTTPException(404, "登录窗口不存在或不属于当前账号")
        return session

    async def close(self):
        self._closing = True
        sessions = list(self._sessions.values())
        try:
            await asyncio.gather(*(session.stop() for session in sessions), return_exceptions=True)
            await asyncio.gather(*(session._finish_task for session in sessions
                                   if session._finish_task is not None), return_exceptions=True)
            for session in sessions:
                if self._sessions.get(session.id) is session:
                    self._sessions.pop(session.id, None)
        finally:
            self._closing = False


registry = RemoteLoginRegistry()
