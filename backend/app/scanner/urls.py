"""Нормализация URL и защита от SSRF (сканирование внутренних адресов)."""

import asyncio
import ipaddress
import socket
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit


class UrlError(ValueError):
    pass


def normalize_input(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise UrlError("Введите адрес сайта")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise UrlError("Поддерживаются только http и https")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        raise UrlError("Некорректный адрес сайта")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise UrlError("Некорректное доменное имя") from e
    if parts.port and parts.port not in (80, 443):
        raise UrlError("Нестандартные порты не поддерживаются")
    return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))


def site_key(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def is_same_site(url: str, host: str) -> bool:
    h = (urlsplit(url).hostname or "").lower()
    return site_key(h) == site_key(host)


def clean_link(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return None
    url, _ = urldefrag(urljoin(base, href))
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    return url


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return addr.is_global and not addr.is_multicast


_resolve_cache: dict[str, tuple[bool, str | None]] = {}


async def resolve_public(host: str) -> tuple[bool, str | None]:
    """Возвращает (адрес публичный, первый IP). Непубличные адреса сканировать нельзя."""
    host = host.lower()
    if host in _resolve_cache:
        return _resolve_cache[host]
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return False, None
        ips = list(dict.fromkeys(i[4][0] for i in infos))
    ok = bool(ips) and all(_ip_is_public(ip) for ip in ips)
    res = (ok, ips[0] if ips else None)
    if len(_resolve_cache) > 5000:
        _resolve_cache.clear()
    _resolve_cache[host] = res
    return res
