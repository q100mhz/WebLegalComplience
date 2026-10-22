"""Быстрая загрузка страниц без браузера: HTTP-запрос + разбор HTML.

Повторяет то, что EXTRACT_JS в crawler.py достаёт из DOM, но по исходному HTML. Подходит для сайтов,
которые отдают контент сервером (Битрикс, WordPress, Tilda и большинство корпоративных сайтов).
Для SPA и страниц, где нужен скриншот формы, краулер использует браузер.
"""

import re
from urllib.parse import urljoin, urlsplit

import httpx
from lxml import html as lh

from .models import Checkbox, Form, FormInput, Link, PageData
from .urls import resolve_public

MAX_BYTES = 3_000_000
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
    "header", "footer", "nav", "aside", "main", "form", "label", "button", "dt", "dd", "blockquote", "ul", "ol", "table",
}
SKIP_INPUT_TYPES = {"hidden", "submit", "button", "checkbox", "radio", "image", "reset"}
CONSENT_POPUP_RE = re.compile(rb"data-bx-user-consent|main-user-consent-request")
META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


class PageGone(Exception):
    """Страница не существует (404/410) — браузер тут не поможет."""


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _text(el) -> str:
    return _norm(el.text_content()) if el is not None else ""


def _decode(body: bytes, header_charset: str | None) -> str:
    enc = header_charset
    if not enc:
        m = META_CHARSET_RE.search(body[:4096])
        enc = m.group(1).decode() if m else "utf-8"
    try:
        s = body.decode(enc, errors="replace")
    except LookupError:
        s = body.decode("utf-8", errors="replace")
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", s)


def _label_for(doc, inp) -> str:
    t = ""
    if inp.get("id"):
        lbl = doc.xpath("//label[@for=$id]", id=inp.get("id"))
        if lbl:
            t = _text(lbl[0])
    if not t:
        anc = next((a for a in inp.iterancestors() if a.tag == "label"), None)
        t = _text(anc)
    if not t and inp.getparent() is not None:
        t = _text(inp.getparent())
    return t[:500]


def _links(base: str, root) -> list[Link]:
    return [Link(href=a.get("href", ""), text=_text(a)[:200]) for a in root.iter("a") if a.get("href")][:2000]


def _form(doc, base: str, container, idx: int) -> Form | None:
    inputs = []
    for i in container.iter("input", "textarea", "select"):
        typ = (i.get("type") or i.tag).lower() if i.tag == "input" else i.tag
        if typ in SKIP_INPUT_TYPES:
            continue
        inputs.append(FormInput(
            type=typ, name=(i.get("name") or i.get("id") or "")[:100],
            placeholder=(i.get("placeholder") or "")[:100], autocomplete=(i.get("autocomplete") or "")[:50],
        ))
    if not inputs:
        return None
    checkboxes = [
        Checkbox(checked=c.get("checked") is not None, label=_label_for(doc, c))
        for c in container.iter("input") if (c.get("type") or "").lower() == "checkbox"
    ]
    submits = [
        _text(b) or b.get("value", "")
        for b in container.xpath(".//button | .//input[@type='submit'] | .//*[@role='button'] | .//a[contains(@class,'btn')]")
    ]
    return Form(
        action=container.get("action", "") if container.tag == "form" else "",
        method=(container.get("method") or "get").lower() if container.tag == "form" else "get",
        inputs=inputs, checkboxes=checkboxes, text=_text(container)[:3000], links=_links(base, container),
        submit=" | ".join(s for s in submits if s)[:300], index=idx,
    )


def parse_html(url: str, status: int, body: bytes, charset: str | None, headers: dict[str, str]) -> PageData:
    doc = lh.document_fromstring(_decode(body, charset) or "<html></html>")
    absu = lambda v: urljoin(url, v)  # noqa: E731

    scripts = [absu(s.get("src")) for s in doc.iter("script") if s.get("src")][:200]
    iframes = [absu(f.get("src")) for f in doc.iter("iframe") if f.get("src")][:100]
    resources = scripts + iframes + [absu(l.get("href")) for l in doc.iter("link") if l.get("href")] + [
        absu(i.get("src")) for i in doc.iter("img") if i.get("src") and not i.get("src").startswith("data:")
    ]
    generator = next((m.get("content", "") for m in doc.iter("meta") if (m.get("name") or "").lower() == "generator"), "")
    title = _norm(doc.findtext(".//title"))
    lang = doc.get("lang", "")

    consent_popup = bool(CONSENT_POPUP_RE.search(body))
    # Скрипты и шаблоны не являются текстом страницы (иначе в текст формы попадает HTML-разметка шаблонов)
    for el in doc.xpath("//script | //style | //noscript | //template | //svg"):
        el.drop_tree()

    # Формы: <form> и «виртуальные» блоки с полями без тега form (как в EXTRACT_JS)
    containers = list(doc.iter("form"))
    for inp in doc.iter("input", "textarea"):
        if not any(a.tag == "form" for a in inp.iterancestors()):
            c = next((a for a in inp.iterancestors() if a.tag in ("div", "section", "aside", "footer")), inp.getparent())
            if c is not None and c not in containers:
                containers.append(c)
    forms = [f for idx, c in enumerate(containers[:50]) if (f := _form(doc, url, c, idx))]

    buttons = [
        _text(b) or b.get("value", "")
        for b in doc.xpath("//button | //a[@role='button'] | //input[@type='submit'] | //*[contains(@class,'btn')]")
    ]
    links = _links(url, doc)

    # Видимый текст: без скриптов, стилей и явно скрытых элементов; блоки разделяем переводами строк
    for el in doc.xpath("//*[@hidden] | //*[contains(translate(@style,' ',''),'display:none')]"):
        el.drop_tree()
    for el in doc.iter():
        if isinstance(el.tag, str) and el.tag in BLOCK_TAGS:
            el.tail = "\n" + (el.tail or "")
    body_el = doc.find("body")
    raw = (body_el if body_el is not None else doc).text_content()
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", raw)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()[:300_000]

    return PageData(
        url=url, status=status, title=title, lang=lang, text=text, links=links, forms=forms,
        iframes=iframes, buttons=[b for b in buttons if b][:300], request_urls=list(dict.fromkeys(resources)),
        generator=generator[:200], scripts=scripts, headers=headers, via="http", consent_popup=consent_popup,
    )


async def http_page(client: httpx.AsyncClient, url: str) -> PageData | None:
    """Страница через HTTP. None — нужен браузер (блокировка, не-HTML, ошибка сервера)."""
    for _ in range(6):
        host = urlsplit(url).hostname
        if not host or not (await resolve_public(host))[0]:
            return None
        async with client.stream("GET", url) as r:
            if r.is_redirect:
                loc = r.headers.get("location")
                if not loc:
                    return None
                url = str(r.url.join(loc))
                continue
            if r.status_code in (404, 410):
                raise PageGone(f"{url}: HTTP {r.status_code}")
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                return None
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    break
            headers = {k.lower(): v[:300] for k, v in r.headers.items()}
            return parse_html(str(r.url), r.status_code, bytes(body), r.charset_encoding, headers)
    return None
