"""Acquisition-only HTTP boundary: pinned DNS, redirects, and durable budgets.

Does not govern the editorial writer or publication APIs. All collector adapters
and the evidence search executor use this boundary; subprocesses get no network.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
from pathlib import Path
import queue
import socket
import sqlite3
import ssl
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit


class BoundaryError(ValueError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, path: Path | str = ":memory:", limit=180, interval=1.0):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=5)
        self.limit, self.interval = limit, interval
        self.db.execute("CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, domain TEXT, category TEXT, reserved REAL)")
        self.db.commit()

    def reserve(self, domain, category="collection", category_limit=None):
        # Reserve BEFORE issuing a request. A crash/timeout never refunds it.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            total = self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            count = self.db.execute("SELECT COUNT(*) FROM requests WHERE category=?", (category,)).fetchone()[0]
            if total >= self.limit or category_limit is not None and count >= category_limit:
                raise BudgetExceeded("acquisition request budget exhausted")
            last = self.db.execute("SELECT MAX(reserved) FROM requests WHERE domain=?", (domain,)).fetchone()[0] or 0
            reserved = max(time.time(), last + self.interval)
            self.db.execute("INSERT INTO requests(domain,category,reserved) VALUES(?,?,?)", (domain, category, reserved))
            self.db.commit()
            return max(0, reserved - time.time())
        except BaseException:
            self.db.rollback()
            raise

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]


_budget = None


def configure_budget(path=None, limit=180, interval=1.0):
    global _budget
    if _budget is not None:
        _budget.db.close()
    _budget = Budget(path or os.environ.get("SPECTRA_ACQUISITION_BUDGET_DB", ":memory:"), limit, interval)
    return _budget


def current_budget():
    global _budget
    if _budget is None:
        configure_budget()
    return _budget


def origin(url):
    p = urlsplit(url)
    return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)


def resolve_public(url, allowed_local_origins=()):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or any(ord(c) < 33 for c in url):
        raise BoundaryError("invalid HTTP URL or embedded credentials")
    if p.fragment:
        raise BoundaryError("fetch URLs must not contain fragments")
    host = p.hostname.encode("idna").decode("ascii")
    port = p.port or (443 if p.scheme == "https" else 80)
    local = origin(url) in {origin(value) for value in allowed_local_origins}
    if not local and port not in {80, 443}:
        raise BoundaryError("public acquisition ports restricted to 80/443")
    results = queue.Queue(maxsize=1)

    def resolve():
        try:
            results.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception as exc:
            results.put(exc)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = results.get(timeout=5)
    except queue.Empty:
        raise TimeoutError("DNS resolution timed out") from None
    if isinstance(addresses, Exception):
        raise BoundaryError("DNS resolution failed") from None
    if not addresses:
        raise BoundaryError("DNS returned no addresses")
    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0].split("%")[0])
        public = ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)
        if not (ip.is_loopback if local else public):
            raise BoundaryError("DNS resolved to a forbidden address")
    return sorted(addresses, key=lambda item: item[0] != socket.AF_INET)[0]


def request_bytes(url, *, data=None, headers=None, timeout=30, max_bytes=10 * 1024 * 1024,
                  allowed_local_origins=(), category="collection", category_limit=None, max_redirects=4):
    """Return bytes and final URL. No ambient proxies, cookies, or redirect auth."""
    started = time.monotonic()
    original = origin(url)
    request_headers = {"User-Agent": "SPECTRA/0.3", "Accept-Encoding": "identity", **(headers or {})}
    for hop in range(max_redirects + 1):
        family, socktype, proto, _, address = resolve_public(url, allowed_local_origins)
        remaining = timeout - (time.monotonic() - started)
        delay = current_budget().reserve(urlsplit(url).hostname, category, category_limit)
        if delay >= remaining:
            raise BudgetExceeded("acquisition time budget exhausted")
        if delay:
            time.sleep(delay)
        remaining = timeout - (time.monotonic() - started)
        p = urlsplit(url)
        connection = (http.client.HTTPSConnection(p.hostname, p.port or 443, timeout=remaining,
                                                  context=ssl.create_default_context()) if p.scheme == "https"
                      else http.client.HTTPConnection(p.hostname, p.port or 80, timeout=remaining))

        def pinned_connect(_address, timeout=None, source_address=None):
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(timeout)
            try:
                sock.connect(address)  # Numeric sockaddr: no second DNS lookup.
                return sock
            except BaseException:
                sock.close()
                raise

        connection._create_connection = pinned_connect
        watchdog = None
        expired = threading.Event()
        try:
            connection.connect()
            connected_socket = connection.sock

            def expire():
                expired.set()
                try:
                    connected_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            watchdog = threading.Timer(max(0.01, timeout - (time.monotonic() - started)), expire)
            watchdog.daemon = True
            watchdog.start()
            path = (p.path or "/") + ("?" + p.query if p.query else "")
            connection.request("POST" if data is not None else "GET", path, body=data, headers=request_headers)
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                if data is not None:
                    raise BoundaryError("authenticated/POST redirects are not allowed")
                target = urljoin(url, response.getheader("Location") or "")
                if hop == max_redirects or target == url:
                    raise BoundaryError("redirect limit exceeded")
                if p.scheme == "https" and urlsplit(target).scheme != "https":
                    raise BoundaryError("HTTPS downgrade blocked")
                if origin(target) != original:
                    request_headers = {k: v for k, v in request_headers.items() if k.lower() not in {"authorization", "cookie", "proxy-authorization"}}
                url = target
                continue
            if response.status >= 400:
                error = HTTPError(url, response.status, response.reason, response.headers, None)
                try:
                    details = json.loads(response.read(4096)).get("error", {})
                    if isinstance(details, dict):
                        code = details.get("code") or details.get("type")
                        error.provider_code = code if isinstance(code, str) and code.replace("_", "").isalnum() else None
                except (ValueError, AttributeError):
                    pass
                raise error
            content_length = response.getheader("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise BoundaryError("response exceeds acquisition size limit")
            chunks, size = [], 0
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("acquisition response deadline exceeded")
                if connection.sock:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, max_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise BoundaryError("response exceeds acquisition size limit")
                chunks.append(chunk)
            return b"".join(chunks), url
        except (OSError, http.client.HTTPException) as exc:
            if expired.is_set() or isinstance(exc, socket.timeout):
                raise TimeoutError("acquisition request deadline exceeded") from None
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()
    raise BoundaryError("redirect limit exceeded")
