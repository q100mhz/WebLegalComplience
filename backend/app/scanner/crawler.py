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
from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..config import settings
from .checks.common import form_is_pd, form_signature
from .fastfetch import PageGone, http_page
from .services_db import match_service
from .models import Checkbox, Form, FormInput, HttpsInfo, Link, PageData, SiteData
from .urls import clean_link, is_same_site, page_key, resolve_public, strip_tracking

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
      inputs, checkboxes, index: idx,
      // фразу о согласии часто ставят сразу под формой — берём и текст следующих блоков
      text: (txt(f) + ' ' + [f.nextElementSibling, f.nextElementSibling && f.nextElementSibling.nextElementSibling]
        .filter(Boolean).map(txt).join(' ').slice(0, 400)).slice(0, 3400),
      submit: [...f.querySelectorAll('button, input[type=submit], [role=button], a[class*=btn], [class*=button]')]
        .map(b => txt(b) || b.value || '').filter(Boolean).join(' | ').slice(0, 300),
      links: [...f.querySelectorAll('a[href]')].map(a => ({ href: a.getAttribute('href') || '', text: txt(a).slice(0, 200) })),
    };
  }).filter(f => f.inputs.length > 0);
  return {
    title: document.title || '',
    lang: document.documentElement.getAttribute('lang') || '',
    // innerText пуст на некоторых сайтах (контент в теневом DOM, особые стили) — тогда берём textContent
    text: (() => {
      if (!document.body) return '';
      const t = document.body.innerText || '';
      if (t.trim().length >= 200) return t.slice(0, 300000);
      const c = document.body.cloneNode(true);
      c.querySelectorAll('script,style,noscript,template,svg').forEach(e => e.remove());
      return (c.textContent || '').replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim().slice(0, 300000) || t;
    })(),
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
            resp = await page.goto(url, wait_until="domcontentloaded",
                                   timeout=settings.page_timeout_ms * (2 if keep_html else 1))
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
        data = await _evaluate(page, EXTRACT_JS)
        if keep_html and len(data["text"]) < 300:
            # SPA и сайты с JS-проверкой браузера дорисовывают контент позже
            await page.wait_for_timeout(4000)
            data = await _evaluate(page, EXTRACT_JS)
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


async def _evaluate(page: Page, script: str):
    """page.evaluate, переживающий перенаправление (JS-редирект, проверка браузера) во время разбора."""
    try:
        return await page.evaluate(script)
    except Exception as e:  # noqa: BLE001
        if "Execution context was destroyed" not in str(e) and "navigation" not in str(e).lower():
            raise
        try:
            await page.wait_for_load_state("load", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(1000)
        return await page.evaluate(script)


# ---------- Всплывающие формы ----------
# Многие сайты показывают форму заявки только после нажатия кнопки («Оставить заявку», «Заказать звонок»):
# в коде страницы её нет, она подгружается в окно. Нажимаем такие кнопки на главной и собираем появившиеся формы.

CTA_RE = re.compile(
    r"оставить\s+(заявку|контакт)|заказать\s+(звонок|консультац|расч|обратн)|обратн\w+\s+звон|перезвон|"
    r"консультац|связаться|задать\s+вопрос|получить\s+(консультац|предложен|расч[её]т|скидк|прайс|кп)|"
    r"записаться|напишите\s+нам|отправить\s+заявку|^заявка$|бесплатн\w+\s+(консультац|замер|расч)",
    re.I,
)
CTA_JS = r"""
() => {
  const out = [], seen = new Set();
  for (const el of document.querySelectorAll('button, a, [role=button], [onclick], [class*=btn], [class*=button]')) {
    const t = (el.innerText || el.value || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 60 || seen.has(t.toLowerCase())) continue;
    const href = el.tagName === 'A' ? (el.getAttribute('href') || '') : '';
    if (href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript')) continue;  // ссылка на другую страницу
    const r = el.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) continue;
    seen.add(t.toLowerCase());
    el.setAttribute('data-wlc-cta', String(out.length));
    out.push(t);
  }
  return out;
}
"""


async def _probe_popups(context: BrowserContext, url: str, shots: Shots, known: set[str], limit: int = 3) -> list[Form]:
    page = await context.new_page()
    found: list[Form] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms * 2)
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:  # noqa: BLE001
            pass
        labels = await _evaluate(page, CTA_JS)
        targets = [(i, t) for i, t in enumerate(labels) if CTA_RE.search(t)][:limit]
        for idx, label in targets:
            try:
                await page.locator(f'[data-wlc-cta="{idx}"]').first.click(timeout=3000)
            except Exception:  # noqa: BLE001 — кнопка перекрыта, скрыта или исчезла
                continue
            await page.wait_for_timeout(1800)  # окно открывается с анимацией, форма может подгружаться
            if page.url.split("#")[0].rstrip("/") != url.split("#")[0].rstrip("/"):
                break  # кнопка увела на другую страницу — её формы найдёт обычный обход
            data = await _evaluate(page, EXTRACT_JS)
            for f in data["forms"]:
                form = Form(
                    action=f["action"], method=f["method"], inputs=[FormInput(**i) for i in f["inputs"]],
                    checkboxes=[Checkbox(**c) for c in f["checkboxes"]], text=f["text"],
                    links=[Link(**l) for l in f["links"]], submit=f["submit"], index=f["index"], popup=True,
                )
                sig = form_signature(form)
                if not form_is_pd(form) or sig in known:
                    continue
                known.add(sig)
                if name := shots.take("form:" + sig):
                    loc = page.locator(f'[data-wlc-form="{form.index}"]').first
                    await _screenshot(loc.screenshot(path=str(shots.dir / name), type="jpeg", quality=70, timeout=3000),
                                      form, name)
                form.text = f"[Всплывающее окно «{label}»] " + form.text
                found.append(form)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
    except Exception as e:  # noqa: BLE001 — поиск всплывающих форм не должен ронять проверку
        log.info("popup probe failed for %s: %s", url, str(e).splitlines()[0][:200])
    finally:
        await page.close()
    return found


async def _screenshot(coro, target, name: str) -> None:
    try:
        await coro
        target.screenshot = name
    except Exception:  # noqa: BLE001 — скрытая форма, анимация и т.п.
        pass


async def crawl(
    start_url: str,
    progress: ProgressCb | None = None,
    shots_dir: Path | None = None,
    on_quick: Callable[[SiteData], Awaitable[None]] | None = None,
    max_pages: int | None = None,
) -> SiteData:
    """on_quick вызывается один раз, когда собраны главная и первые юридически значимые страницы
    (settings.quick_pages страниц или settings.quick_seconds секунд) — для предварительного отчёта."""
    max_pages = max_pages or settings.max_pages
    max_depth = settings.max_depth + (2 if max_pages > 100 else 0)  # глубокой проверке — больше уровней ссылок
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
            if dead := _dead_site(home):
                raise BlockedError(f"Сайт не работает: {dead}. Проверка соответствия законодательству не проводится.",
                                   diag={**_diag(home), "screenshot": home.screenshot, "dead_site": dead})
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
            # всплывающие формы ищем параллельно с обходом — cookies «до согласия» уже зафиксированы
            popup_task = asyncio.create_task(
                _probe_popups(context, home.url, shots, {form_signature(f) for f in home.forms}))
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
            seen_pages = {page_key(home.url), page_key(start_url)}
            queue: list[tuple[int, int, int, str]] = []
            counter = 0

            def enqueue(p: PageData, depth: int) -> None:
                nonlocal counter
                for l in p.links:
                    u = clean_link(p.url, l.href)
                    if not u or not is_same_site(u, host):
                        continue
                    u = strip_tracking(u)
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
                    pk = page_key(u)
                    if pk in seen_pages:  # та же страница с другими параметрами — только если останется лимит
                        prio = depth + 10
                    seen_pages.add(pk)
                    counter += 1
                    heapq.heappush(queue, (prio, depth, counter, u))

            enqueue(home, 1)
            quick_sent = False
            in_flight: dict[asyncio.Task, int] = {}
            while (queue or in_flight) and len(site.pages) < max_pages:
                limit = settings.parallel_tabs + (HTTP_PARALLEL if mode == "hybrid" else 0)
                while queue and len(in_flight) < limit and len(site.pages) + len(in_flight) < max_pages:
                    _, depth, _, url = heapq.heappop(queue)
                    if depth <= max_depth:
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
                    if not is_same_site(pd.url, host) or len(site.pages) >= max_pages:
                        continue
                    site.pages.append(pd)
                    if progress:
                        await progress(len(site.pages), pd.url)
                    enqueue(pd, depth + 1)
                    if on_quick and not quick_sent and (
                        len(site.pages) >= settings.quick_pages or time.monotonic() - t0 >= settings.quick_seconds
                    ):
                        quick_sent = True
                        await on_quick(site)
            for task in in_flight:
                task.cancel()
            site.truncated = len(site.pages) >= max_pages and bool(queue or in_flight)
            await http.aclose()
            inner = time.monotonic() - t_home - site.timing["home_s"]
            site.timing.update(
                inner_s=round(inner, 1),
                avg_page_s=round(inner / max(len(site.pages) - 1, 1), 2),
                pages=len(site.pages),
                errors=len(site.errors),
            )
            try:
                popups = await asyncio.wait_for(popup_task, timeout=45)
            except Exception:  # noqa: BLE001
                popups = []
            home.forms.extend(popups)
            site.stats["popup_forms"] = len(popups)
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


DEAD_SITE: list[tuple[str, str]] = [
    (r"домен\s+\S+\s+прода[её]тся|доменное\s+имя\s+\S*\s*прода[её]тся|домен\s+прода[её]тся|domain\s+(is\s+)?for\s+sale|"
     r"buy\s+this\s+domain|припаркован|domain\s+parking|sigmaparking", "домен выставлен на продажу (заглушка регистратора)"),
    (r"сайт\s+заблокирован.{0,80}оплат|аккаунт\s+(заблокирован|приостановлен)|account\s+suspended|"
     r"срок\s+(действия\s+)?(хостинга|услуги)\s+(истёк|истек|закончился)", "хостинг заблокирован или не оплачен"),
    (r"(сайт|запрошенный\s+сайт)\s+отсутствует\s+на\s+(нашем\s+)?хостинге|домен\s+не\s+(прилинкован|привязан|"
     r"настроен|делегирован)|site\s+not\s+found|no\s+such\s+app|default\s+web\s+page|welcome\s+to\s+nginx|"
     r"it\s+works!|index\s+of\s+/", "на домене нет сайта (заглушка хостинга)"),
    (r"сайт\s+(находится\s+)?в\s+разработке|сайт\s+скоро\s+(откроется|появится)|under\s+construction|coming\s+soon",
     "сайт в разработке (заглушка)"),
]


def _dead_site(p: PageData) -> str | None:
    """Заглушка вместо сайта: домен продаётся, хостинг не оплачен, сайт удалён или в разработке.
    Проверяем только короткие страницы — на настоящем сайте такие фразы встречаются в тексте."""
    head = f"{p.title}\n{p.text[:3000]}"
    if len(p.text) > 8000 or len([l for l in p.links if l.href and not l.href.startswith(("#", "javascript"))]) > 400:
        return None
    for rx, reason in DEAD_SITE:
        if re.search(rx, head, re.I):
            return reason
    return None


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
