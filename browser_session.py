"""Use a real Chrome session for Cloudflare-protected wenku8 pages."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from playwright.sync_api import Browser, Page, Playwright, Response, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass
class BrowserResponse:
    """Small requests.Response-compatible object used by the existing downloader."""

    status_code: int
    content: bytes
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    apparent_encoding: str = 'utf-8'

    @property
    def text(self) -> str:
        return self.content.decode(self.apparent_encoding, errors='replace')


def find_chrome() -> Path:
    configured = os.environ.get('WENKU8_CHROME_PATH')
    candidates = [
        Path(configured) if configured else None,
        Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
        Path(r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'),
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe',
        Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
        Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RuntimeError('未找到 Chrome 或 Edge。请安装浏览器，或设置 WENKU8_CHROME_PATH。')


def reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class BrowserSession:
    """A requests-like session backed by a visible, persistent Chrome profile."""

    RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}

    def __init__(self, challenge_timeout: int = 180, max_retries: int = 5,
                 retry_base_delay: int = 8):
        self.headers: Dict[str, str] = {}
        self.challenge_timeout = challenge_timeout
        self.max_retries = max(1, max_retries)
        self.retry_base_delay = max(0, retry_base_delay)
        self.profile_dir = PROJECT_ROOT / '.browser-profile'
        self.cache_dir = PROJECT_ROOT / '.cache' / 'chrome'
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._chrome_process: Optional[subprocess.Popen] = None
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._start()

    def _start(self) -> None:
        chrome_path = find_chrome()
        port = reserve_local_port()
        command = [
            str(chrome_path),
            f'--remote-debugging-port={port}',
            '--remote-debugging-address=127.0.0.1',
            '--remote-allow-origins=*',
            f'--user-data-dir={self.profile_dir}',
            f'--disk-cache-dir={self.cache_dir}',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-breakpad',
            '--disable-crash-reporter',
            'about:blank',
        ]
        self._chrome_process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        endpoint = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 20
        self._playwright = sync_playwright().start()
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            if self._chrome_process.poll() is not None:
                break
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(endpoint)
                break
            except Exception as exc:  # Chrome needs a short startup window.
                last_error = exc
                time.sleep(0.25)

        if self._browser is None:
            self.close()
            raise RuntimeError(f'无法连接到 Chrome 调试会话: {last_error or "Chrome 已退出"}')

        contexts = self._browser.contexts
        context = contexts[0] if contexts else self._browser.new_context()
        pages = context.pages
        self._page = pages[0] if pages else context.new_page()

    @staticmethod
    def _is_challenge(page: Page) -> bool:
        try:
            title = page.title().lower()
            url = page.url.lower()
            body = page.locator('body').inner_text(timeout=1500).lower()
        except Exception:
            return False
        markers = (
            'just a moment',
            '正在进行安全验证',
            'performing security verification',
            'verify you are human',
        )
        return (
            any(marker in title or marker in body for marker in markers)
            or '/cdn-cgi/challenge-platform/' in url
        )

    def _wait_for_challenge(self, page: Page) -> bool:
        if not self._is_challenge(page):
            return False

        print('\n检测到 Cloudflare 验证，请在打开的浏览器窗口中完成验证。')
        print(f'最多等待 {self.challenge_timeout} 秒，验证成功后会自动继续。\n')
        page.bring_to_front()
        deadline = time.monotonic() + self.challenge_timeout
        while time.monotonic() < deadline:
            if page.is_closed():
                raise RuntimeError('Chrome 页面已关闭，无法完成网站验证。')
            if not self._is_challenge(page):
                try:
                    page.wait_for_load_state('domcontentloaded', timeout=10000)
                except Exception:
                    pass
                return True
            time.sleep(1)
        raise RuntimeError('Cloudflare 验证超时。请重新运行，并在浏览器窗口中完成验证。')

    def get(self, url: str, timeout: int = 60, **kwargs) -> BrowserResponse:
        last_response: Optional[BrowserResponse] = None
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = self._get_once(url, timeout=timeout, **kwargs)
                if response.status_code not in self.RETRYABLE_STATUS:
                    return response
                last_response = response
                reason = f'HTTP {response.status_code}'
            except Exception as exc:
                message = str(exc)
                if any(marker in message for marker in ('页面已关闭', '会话尚未启动', 'Chrome 会话')):
                    raise
                last_error = exc
                reason = message

            if attempt >= self.max_retries - 1:
                break

            delay = min(self.retry_base_delay * (2 ** attempt), 45)
            if last_response is not None and last_response.status_code == 429:
                delay = max(delay, 15)
            print(f'访问暂时失败（{reason}），{delay} 秒后自动重试 '
                  f'({attempt + 2}/{self.max_retries})...')
            time.sleep(delay)

        if last_response is not None:
            return last_response
        raise RuntimeError(f'请求失败（已重试 {self.max_retries} 次）: {last_error}') from last_error

    def _get_once(self, url: str, timeout: int = 60, **_kwargs) -> BrowserResponse:
        if self._page is None:
            raise RuntimeError('Chrome 会话尚未启动。')

        response: Optional[Response]
        try:
            response = self._page.goto(
                url,
                wait_until='domcontentloaded',
                timeout=max(timeout, 1) * 1000,
            )
        except Exception as exc:
            if not self._is_challenge(self._page):
                raise RuntimeError(f'浏览器访问失败: {url}: {exc}') from exc
            response = None

        challenged = self._wait_for_challenge(self._page)
        if challenged:
            response = self._page.reload(
                wait_until='domcontentloaded',
                timeout=max(timeout, 1) * 1000,
            )

        if response is None:
            raise RuntimeError(f'浏览器未收到响应: {url}')

        headers = response.all_headers()
        content_type = headers.get('content-type', '').lower()
        if content_type.startswith('text/') or 'html' in content_type or 'xml' in content_type:
            content = self._page.content().encode('utf-8')
            encoding = 'utf-8'
        else:
            content = response.body()
            encoding = 'utf-8'

        return BrowserResponse(
            status_code=response.status,
            content=content,
            url=self._page.url,
            headers=headers,
            apparent_encoding=encoding,
        )

    def close(self) -> None:
        browser, playwright, process = self._browser, self._playwright, self._chrome_process
        self._page = None
        self._browser = None
        self._playwright = None
        self._chrome_process = None

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                pass
