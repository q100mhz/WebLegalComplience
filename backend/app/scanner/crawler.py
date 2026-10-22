"""Обход сайта: главная — браузером (трекеры, cookies до согласия), остальное — HTTP или браузером."""

import asyncio
import hashlib
import heapq
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Browser, BrowserContext, Route, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..config import settings
from .checks.common import form_is_pd, form_signature
from .fastfetch import PageGone, http_page
from .services_db import match_service
from .models import Checkbox, Form, FormInput, HttpsInfo, Link, PageData, SiteData
from .urls import clean_link, is_same_site, resolve_public

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, str], Awaitable[None]]
HTTP_PARALLEL = 6  # одновременных HTTP-загрузок в гибридном режиме


DOC_EXT = (".pdf", ".doc", ".docx", ".rtf", ".odt")
SKIP_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".zip", ".rar", ".7z",
    ".mp4", ".mp3", ".avi", ".mov", ".xls", ".xlsx", ".csv", ".xml", ".json", ".css", ".js",
)
# Страницы, которые важны для проверки — обходим их в первую очередь
PRIORITY_RE = re.compile(
    r"polit|privacy|konfid|personal|pdn|soglas|consent|agree|oferta|offer|terms|usloviy|"
    r"cookie|kontakt|contact|rekvizit|about|o-nas|o-kompan|dostavk|delivery|oplat|payment|"
    r"vozvrat|return|garant|"
    r"политик|конфиденц|персональн|согласи|оферт|соглашени|услови|cookie|куки|контакт|"
    r"реквизит|о компании|о нас|доставк|оплат|возврат|гарант|правов",
    re.I,
)

EXTRACT_JS = r"""
() => {
  const txt = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
  const links = [...document.querySelectorAll('a[href]')].slice(0, 2000).map(a => ({
    href: a.getAttribute('href') || '', text: txt(a).slice(0, 200)
  }));
  const labelFor = (inp) => {
    let t = '';
    if (inp.id) { const l = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`); if (l) t = txt(l); }
    if (!t) { const l = inp.closest('label'); if (l) t = txt(l); }
    if (!t && inp.parentElement) t = txt(inp.parentElement);
    return t.slice(0, 500);
  };
  // Формы: настоящие <form> и "виртуальные" блоки с полями без тега form
  const containers = new Set(document.querySelectorAll('form'));
  document.querySelectorAll('input, textarea').forEach(inp => {
    if (!inp.closest('form')) {
      const c = inp.closest('div,section,aside,footer') || inp.parentElement;
      if (c) containers.add(c);
    }
  });
  const forms = [...containers].slice(0, 50).map((f, idx) => {
    try { f.setAttribute('data-wlc-form', String(idx)); } catch (e) {}
    const inputs = [...f.querySelectorAll('input, textarea, select')]
      .filter(i => !['hidden', 'submit', 'button', 'checkbox', 'radio', 'image', 'reset'].includes((i.type || '').toLowerCase()))
      .map(i => ({
        type: (i.type || i.tagName).toLowerCase(), name: (i.name || i.id || '').slice(0, 100),
        placeholder: (i.placeholder || '').slice(0, 100), autocomplete: (i.autocomplete || '').slice(0, 50),
      }));
    const checkboxes = [...f.querySelectorAll('input[type=checkbox]')].map(c => ({
      checked: c.checked || c.hasAttribute('checked'), label: labelFor(c),
    }));
    return {
      action: f.getAttribute && f.getAttribute('action') || '', method: (f.getAttribute && f.getAttribute('method') || 'get').toLowerCase(),
      inputs, checkboxes, text: txt(f).slice(0, 3000), index: idx,
      submit: [...f.querySelectorAll('button, input[type=submit], [role=button], a[class*=btn], [class*=button]')]
        .map(b => txt(b) || b.value || '').filter(Boolean).join(' | ').slice(0, 300),
      links: [...f.querySelectorAll('a[href]')].map(a => ({ href: a.getAttribute('href') || '', text: txt(a).slice(0, 200) })),
    };
  }).filter(f => f.inputs.length > 0);
  return {
    title: document.title || '',
    lang: document.documentElement.getAttribute('lang') || '',
    text: (document.body ? document.body.innerText : '').slice(0, 300000),
    links, forms,
    generator: (document.querySelector('meta[name=generator]') || {}).content || '',
    consentPopup: !!document.querySelector('[data-bx-user-consent], .main-user-consent-request, [class*="main-user-consent-request"]'),
    scripts: [...document.querySelectorAll('script[src]')].map(s => s.src).slice(0, 200),
    iframes: [...document.querySelectorAll('iframe[src]')].map(i => i.src).slice(0, 100),
    buttons: [...document.querySelectorAll('button, a[role=button], input[type=submit], [class*=btn]')]
      .map(b => txt(b) || b.value || '').filter(Boolean).slice(0, 300),
  };
}
"""


async def _check_https(host: str, info: HttpsInfo) -> None:
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, headers={"User-Agent": settings.user_agent}) as c:
        try:
            r = await c.get(f"https://{host}/")
            info.https_ok = True
            # идём по редиректам вручную, чтобы взять заголовки конечной страницы
            for _ in range(5):
                if r.is_redirect and r.headers.get("location"):
                    nxt = httpx.URL(str(r.url)).join(r.headers["location"])
                    if nxt.scheme != "https" or not (await resolve_public(nxt.host))[0]:
                        break
                    r = await c.get(nxt)
                else:
                    break
            h = {k.lower() for k in r.headers.keys()}
            info.hsts = "strict-transport-security" in h
            info.security_headers = {
                name: name in h
                for name in ("strict-transport-security", "content-security-policy", "x-content-type-options", "x-frame-options")
            }
        except httpx.ConnectError as e:
            info.https_ok = False
            info.cert_error = str(e)[:300] if "certificate" in str(e).lower() or "ssl" in str(e).lower() else "HTTPS недоступен"
        except httpx.HTTPError as e:
            info.cert_error = str(e)[:300] or e.__class__.__name__
        try:
            r = await c.get(f"http://{host}/")
            loc = r.headers.get("location", "")
            info.http_redirects_to_https = r.is_redirect and loc.startswith("https://")
        except httpx.HTTPError:
            info.http_redirects_to_https = None


async def _geoip(ip: str) -> str | None:
    """Страна по IP через открытый RIPEstat (без ключа)."""
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://stat.ripe.net/data/maxmind-geo-lite/data.json", params={"resource": ip})
            for res in r.json().get("data", {}).get("located_resources", []):
                for loc in res.get("locations", []):
                    if loc.get("country"):
                        return loc["country"]
    except Exception:  # noqa: BLE001 — геолокация не критична
        log.info("geoip failed for %s", ip)
    return None


async def _guard_route(route: Route) -> None:
    """Блокируем запросы браузера к внутренним адресам (SSRF) и тяжёлые медиа."""
    req = route.request
    if req.resource_type in ("media", "image", "font"):
        await route.abort()
        return
    host = urlsplit(req.url).hostname
    if req.url.startswith(("data:", "blob:")):
        await route.continue_()
        return
    if not host or not (await resolve_public(host))[0]:
        await route.abort()
        return
    await route.continue_()


async def _inner_route(route: Route) -> None:
    """Внутренние страницы: трекеры и реклама не рисуют контент — не грузим их.
    Сам запрос при этом уже зафиксирован событием request и попадёт в отчёт."""
    svc = match_service(route.request.url)
    if svc and svc.category in ("analytics", "ads"):
        await route.abort()
        return
    await _guard_route(route)


class Shots:
    """Бюджет скриншотов на одно сканирование (доказательства для полного отчёта)."""

    def __init__(self, directory: Path | None, limit: int = 15):
        self.dir = directory
        self.left = limit
        self.seen: set[str] = set()
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def take(self, key: str) -> str | None:
        if not self.dir or self.left <= 0 or key in self.seen:
            return None
        self.seen.add(key)
        self.left -= 1
        return f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.jpg"


async def _visit(context: BrowserContext, url: str, shots: Shots, *, keep_html: bool = False) -> PageData:
    page = await context.new_page()
    if not keep_html:
        await page.route("**/*", _inner_route)
    requests: list[str] = []
    responses: list = []
    page.on("request", lambda r: requests.append(r.url))
    page.on("response", lambda r: responses.append(r) if r.request.is_navigation_request() and r.frame == page.main_frame else None)
    try:
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
        except PlaywrightTimeout:
            # Тяжёлые сайты могут не дождаться DOMContentLoaded — анализируем то, что успело загрузиться
            if not responses:
                raise
            resp = responses[-1]
        # Главная — полное ожидание: фиксируем трекеры и cookies до согласия и отложенные баннеры.
        # Внутренние страницы — короткое: нам нужны тексты, ссылки и формы, а не аналитика.
        try:
            await page.wait_for_load_state("networkidle", timeout=4000 if keep_html else 1500)
        except Exception:  # noqa: BLE001 — сайты с вечными long-poll запросами
            pass
        await page.wait_for_timeout(1000 if keep_html else 300)
        data = await page.evaluate(EXTRACT_JS)
        if keep_html and len(data["text"]) < 300:
            # SPA и сайты с JS-проверкой браузера дорисовывают контент позже
            await page.wait_for_timeout(4000)
            data = await page.evaluate(EXTRACT_JS)
        headers = {}
        if resp:
            try:
                headers = {k.lower(): v[:300] for k, v in (await resp.all_headers()).items()}
            except Exception:  # noqa: BLE001
                headers = {}
        pd = PageData(
            url=page.url,
            status=resp.status if resp else None,
            title=data["title"],
            lang=data["lang"],
            text=data["text"],
            links=[Link(**l) for l in data["links"]],
            forms=[
                Form(
                    action=f["action"], method=f["method"],
                    inputs=[FormInput(**i) for i in f["inputs"]],
                    checkboxes=[Checkbox(**c) for c in f["checkboxes"]],
                    text=f["text"], links=[Link(**l) for l in f["links"]],
                    submit=f["submit"], index=f["index"],
                )
                for f in data["forms"]
            ],
            iframes=data["iframes"],
            buttons=data["buttons"],
            request_urls=list(dict.fromkeys(requests)),
            generator=data["generator"][:200],
            consent_popup=data["consentPopup"],
            scripts=data["scripts"],
            headers=headers,
        )
        if keep_html:
            pd.html = (await page.content())[:400_000]
            if name := shots.take("page:" + pd.url):
                await _screenshot(page.screenshot(path=str(shots.dir / name), type="jpeg", quality=70), pd, name)
        for f in pd.forms:
            if form_is_pd(f) and (name := shots.take("form:" + form_signature(f))):
                loc = page.locator(f'[data-wlc-form="{f.index}"]').first
                await _screenshot(loc.screenshot(path=str(shots.dir / name), type="jpeg", quality=70, timeout=3000), f, name)
        return pd
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"{url}: {str(e).splitlines()[0][:200]}") from e
    finally:
        await page.close()


async def _screenshot(coro, target, name: str) -> None:
    try:
        await coro
        target.screenshot = name
    except Exception:  # noqa: BLE001 — скрытая форма, анимация и т.п.
        pass


async def crawl(start_url: str, progress: ProgressCb | None = None, shots_dir: Path | None = None) -> SiteData:
    host = urlsplit(start_url).hostname or ""
    ok, ip = await resolve_public(host)
    if not ok:
        raise ValueError("Сайт недоступен или указывает на внутренний адрес")

    site = SiteData(start_url=start_url, host=host, server_ip=ip)
    shots = Shots(shots_dir)
    t0 = time.monotonic()
    await asyncio.gather(_check_https(host, site.https), _set_geo(site))

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(
                user_agent=settings.user_agent, locale="ru-RU", ignore_https_errors=True,
                viewport={"width": 1366, "height": 900},
            )
            await context.route("**/*", _guard_route)

            # 1. Главная в "чистом" контексте — фиксируем cookies и запросы до согласия пользователя
            t_home = time.monotonic()
            home = await _visit(context, start_url, shots, keep_html=True)
            if _looks_blocked(home):
                # JS-проверки (DDoS-Guard, Qrator, антибот Битрикса) ставят cookie и пускают со второго захода
                log.info("home looks blocked, retrying: %s", _diag(home))
                await asyncio.sleep(6)
                home = await _visit(context, start_url, shots, keep_html=True)
            site.timing["home_s"] = round(time.monotonic() - t_home, 1)
            if _looks_blocked(home):
                diag = _diag(home)
                log.warning("blocked: %s", diag)
                raise BlockedError(
                    "Сайт не отдал содержимое для проверки: сработала защита от ботов или доступ ограничен. "
                    "Чтобы не выдавать недостоверное заключение, проверка остановлена.",
                    diag={**diag, "screenshot": home.screenshot},
                )
            site.pages.append(home)
            site.initial_request_urls = list(home.request_urls)
            site.initial_cookies = [
                {"name": c["name"], "domain": c["domain"]} for c in await context.cookies()
            ]
            if not site.https.https_ok and home.url.startswith("https://") and not _is_cert_error(site.https.cert_error):
                # httpx мог не дождаться ответа, но браузер открыл сайт по HTTPS
                site.https.https_ok, site.https.cert_error = True, None
            host = urlsplit(home.url).hostname or host  # после редиректа (например, на www)
            site.host = host
            if progress:
                await progress(1, home.url)

            # 2. Режим: если HTML без браузера содержит почти тот же текст и ссылки — внутренние страницы
            #    грузим HTTP-запросом (в разы быстрее), иначе сайт собирается JavaScript-ом и нужен браузер
            http = httpx.AsyncClient(timeout=10, verify=False, follow_redirects=False,
                                     headers={"User-Agent": settings.user_agent, "Accept-Language": "ru-RU,ru;q=0.9"})
            mode = "browser"
            try:
                if not settings.fast_fetch:
                    raise ValueError("fast fetch disabled")
                raw_home = await http_page(http, home.url)
                if raw_home and len(raw_home.text) >= 0.6 * len(home.text) and len(raw_home.links) >= 0.6 * len(home.links):
                    mode = "hybrid"
            except (httpx.HTTPError, PageGone, ValueError):
                pass
            site.stats = {"mode": mode, "browser": 1, "http": 0}
            browser_slots = asyncio.Semaphore(settings.parallel_tabs)
            rendered_forms = {form_signature(f) for f in home.forms if form_is_pd(f)}

            def needs_browser(p: PageData) -> bool:
                if len(p.text) < 200 or _looks_blocked(p):
                    return True
                # новая форма сбора ПДн — снимаем в браузере для скриншота-доказательства
                return shots.left > 0 and any(form_is_pd(f) and form_signature(f) not in rendered_forms for f in p.forms)

            async def fetch(url: str) -> PageData:
                if mode == "hybrid":
                    try:
                        p = await http_page(http, url)
                    except PageGone as e:
                        raise RuntimeError(str(e)) from e
                    except (httpx.HTTPError, ValueError):
                        p = None
                    if p and not needs_browser(p):
                        site.stats["http"] += 1
                        return p
                async with browser_slots:
                    p = await _visit(context, url, shots)
                site.stats["browser"] += 1
                rendered_forms.update(form_signature(f) for f in p.forms if form_is_pd(f))
                return p

            # 3. Обход внутренних страниц с приоритетом юридически значимых
            seen = {home.url.rstrip("/"), start_url.rstrip("/")}
            queue: list[tuple[int, int, int, str]] = []
            counter = 0

            def enqueue(p: PageData, depth: int) -> None:
                nonlocal counter
                for l in p.links:
                    u = clean_link(p.url, l.href)
                    if not u or not is_same_site(u, host):
                        continue
                    key = u.rstrip("/")
                    if key in seen:
                        continue
                    path = urlsplit(u).path.lower()
                    if path.endswith(DOC_EXT):
                        if all(d.href != u for d in site.document_links):
                            site.document_links.append(Link(href=u, text=l.text))
                        seen.add(key)
                        continue
                    if path.endswith(SKIP_EXT):
                        continue
                    seen.add(key)
                    prio = 0 if PRIORITY_RE.search(path + " " + l.text) else depth
                    counter += 1
                    heapq.heappush(queue, (prio, depth, counter, u))

            enqueue(home, 1)
            in_flight: dict[asyncio.Task, int] = {}
            while (queue or in_flight) and len(site.pages) < settings.max_pages:
                limit = settings.parallel_tabs + (HTTP_PARALLEL if mode == "hybrid" else 0)
                while queue and len(in_flight) < limit and len(site.pages) + len(in_flight) < settings.max_pages:
                    _, depth, _, url = heapq.heappop(queue)
                    if depth <= settings.max_depth:
                        in_flight[asyncio.create_task(fetch(url))] = depth
                if not in_flight:
                    break
                done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    depth = in_flight.pop(task)
                    try:
                        pd = task.result()
                    except RuntimeError as e:
                        site.errors.append(str(e))
                        continue
                    if not is_same_site(pd.url, host) or len(site.pages) >= settings.max_pages:
                        continue
                    site.pages.append(pd)
                    if progress:
                        await progress(len(site.pages), pd.url)
                    enqueue(pd, depth + 1)
            for task in in_flight:
                task.cancel()
            site.truncated = len(site.pages) >= settings.max_pages and bool(queue or in_flight)
            await http.aclose()
            inner = time.monotonic() - t_home - site.timing["home_s"]
            site.timing.update(
                inner_s=round(inner, 1),
                avg_page_s=round(inner / max(len(site.pages) - 1, 1), 2),
                pages=len(site.pages),
                errors=len(site.errors),
            )
            await context.close()
        finally:
            await browser.close()
    site.timing["total_s"] = round(time.monotonic() - t0, 1)
    return site


class BlockedError(RuntimeError):
    def __init__(self, msg: str, diag: dict | None = None):
        super().__init__(msg)
        self.diag = diag or {}


def _diag(p: PageData) -> dict:
    return {"url": p.url, "status": p.status, "title": p.title[:150], "text_len": len(p.text),
            "links": len(p.links), "text": p.text[:400]}


BLOCK_RE = re.compile(
    r"checking your browser|just a moment|ddos-guard|access denied|attention required|verify you are human|"
    r"подтвердите,?\s+что\s+вы\s+не\s+робот|проверка\s+браузера|доступ\s+(запрещ|ограничен)|captcha|cloudflare ray id",
    re.I,
)


def _looks_blocked(p: PageData) -> bool:
    if p.status in (401, 403, 429, 503) and len(p.text) < 2000:
        return True
    if len(p.text.strip()) < 80 and len(p.links) < 3:
        return True
    return len(p.text) < 1500 and bool(BLOCK_RE.search(p.text + " " + p.title))


def _is_cert_error(err: str | None) -> bool:
    return bool(err) and any(w in err.lower() for w in ("certificate", "ssl", "tls"))


async def _set_geo(site: SiteData) -> None:
    if site.server_ip:
        site.server_country = await _geoip(site.server_ip)
