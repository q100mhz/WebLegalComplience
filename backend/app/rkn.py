"""Проверка оператора в реестре операторов ПДн Роскомнадзора (152-ФЗ, ст. 22).

Источник — поиск на портале pd.rkn.gov.ru по ИНН (официальный, бесплатный, без капчи на 10.2026).
Машиночитаемой выгрузки реестра РКН не публикует: в каталоге rkn.gov.ru/opendata/list.csv его нет,
у коммерческих API (DaData и т.п.) такого признака тоже нет.

Портал не нагружаем: один запрос на ИНН, не чаще раза в REQUEST_INTERVAL секунд на процесс,
результат кешируется в таблице rkn_checks. Портал доступен только с российских IP.
"""

import asyncio
import logging
import re
import time
from datetime import timedelta

import httpx
from sqlalchemy.exc import IntegrityError
from lxml import html as lh

from .db import RknCheck, SessionLocal, kv_get, kv_set, utcnow

log = logging.getLogger(__name__)

PORTAL = "https://pd.rkn.gov.ru/operators-registry/operators-list/"
KV_KEY = "rkn_portal"
REQUEST_INTERVAL = 2.0
TTL_FOUND = timedelta(days=30)
TTL_NOT_FOUND = timedelta(days=1)
# Увеличивать при каждом изменении разбора портала: кеш, сохранённый прежней версией, перепроверится.
# 2 — ИП без подписи «ИНН» в строке результата; карточка оператора в записи.
PARSER_VERSION = 2  # уведомление могли подать — перепроверяем чаще
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
INN_IN_CELL_RE = re.compile(r"ИНН:\s*(\d{10}|\d{12})")
NOT_FOUND_RE = re.compile(r"ничего\s+не\s+найдено|не\s+найден|нет\s+данных|записей\s+не\s+обнаружено", re.I)
BLOCK_RE = re.compile(r"captcha|капч|доступ\s+(запрещ|ограничен)|too\s+many\s+requests", re.I)

_lock = asyncio.Lock()
_last_request = 0.0


class PortalUnavailable(Exception):
    """Портал не ответил, вернул капчу или незнакомую разметку — результат неизвестен."""


def card_url(reg_number: str) -> str:
    return f"{PORTAL}?id={reg_number}"


def search_url(inn: str) -> str:
    return f"{PORTAL}?act=search&name_full=&inn={inn}&regn="


def parse_search(page: str, inn: str) -> list[dict]:
    """Записи реестра для ИНН из страницы результатов поиска. Пустой список — не найден.
    PortalUnavailable — страница не похожа на результаты поиска."""
    if BLOCK_RE.search(page):
        raise PortalUnavailable("портал запросил капчу или ограничил доступ")
    doc = lh.document_fromstring(page)
    tables = doc.xpath("//table[@id='ResList1']")
    if not tables:
        if NOT_FOUND_RE.search(doc.text_content()):
            return []
        raise PortalUnavailable("на странице нет таблицы результатов — изменилась разметка портала")
    records = []
    for tr in tables[0].xpath(".//tr[td]"):
        cells = tr.xpath("./td")
        if len(cells) < 4 or tr.getparent().tag == "thead":
            continue
        txt = [re.sub(r"\s+", " ", c.text_content()).strip() for c in cells]
        row_inns = re.findall(r"(?<!\d)(\d{10}|\d{12})(?!\d)", " ".join(txt[1:2]))
        # Поиск шёл по ИНН — строка без ИНН (так портал может показывать ИП) относится к нему.
        # Если ИНН в строке есть, он должен совпадать: защита от «похожих» результатов.
        if row_inns and inn not in row_inns:
            continue
        link = cells[1].xpath(".//a")
        records.append({
            "reg_number": txt[0],
            "name": (link[0].text_content().strip() if link else re.split(r"ИНН", txt[1])[0]).strip()[:500],
            "basis": txt[2][:200],
            "reg_date": txt[3],
            "start_date": txt[4] if len(txt) > 4 else "",
            "url": card_url(txt[0]),
        })
    return records


def _inn_ok(inn: str) -> bool:
    d = [int(c) for c in inn]
    ctrl = lambda coefs: sum(c * x for c, x in zip(coefs, d)) % 11 % 10  # noqa: E731
    if len(d) == 10:
        return ctrl([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(d) == 12:
        return ctrl([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10] and ctrl([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11]
    return False


async def _get(client: httpx.AsyncClient, params: dict) -> str:
    """Запрос к порталу не чаще раза в REQUEST_INTERVAL: портал государственный, не создаём нагрузку."""
    global _last_request
    async with _lock:
        wait = REQUEST_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await client.get(PORTAL, params=params)
        except httpx.HTTPError as e:
            raise PortalUnavailable(f"портал не ответил: {type(e).__name__}") from e
        finally:
            _last_request = time.monotonic()
    if r.status_code != 200:
        raise PortalUnavailable(f"портал ответил HTTP {r.status_code}")
    return r.text


def search_debug(page: str) -> dict:
    """Что вернул портал — для разбора случаев «не найден» (видно в админке и в архиве аудита)."""
    try:
        doc = lh.document_fromstring(page)
        rows = doc.xpath("//table[@id='ResList1']//tr[td]")
        data_rows = [r for r in rows if r.getparent().tag != "thead"]
        first = re.sub(r"\s+", " ", data_rows[0].text_content()).strip()[:300] if data_rows else ""
        return {"page_len": len(page), "table": bool(doc.xpath("//table[@id='ResList1']")), "rows": len(data_rows),
                "first_row": first}
    except Exception:  # noqa: BLE001
        return {"page_len": len(page)}


async def _fetch(client: httpx.AsyncClient, inn: str) -> tuple[list[dict], dict]:
    page = await _get(client, {"act": "search", "name_full": "", "inn": inn, "regn": ""})
    records = parse_search(page, inn)
    return records, ({} if records else search_debug(page))


# ---------- Карточка оператора ----------
# Подписи полей карточки известны (Цель обработки, Категории персональных данных, Осуществление трансграничной
# передачи, Сведения о местонахождении базы данных…), но вёрстка может меняться — разбираем пары «подпись → значение»
# из любых таблиц и списков и ищем нужное по смыслу подписи.

CARD_KEYS = re.compile(r"цел|категори|трансгранич|местонахожд|баз[аы]\s+данн|правов|перечень\s+действ", re.I)


def parse_card(page: str) -> dict:
    if BLOCK_RE.search(page):
        raise PortalUnavailable("портал запросил капчу или ограничил доступ")
    doc = lh.document_fromstring(page)
    for el in doc.xpath("//script | //style"):
        el.drop_tree()
    norm = lambda t: re.sub(r"\s+", " ", t or "").strip()  # noqa: E731
    fields: list[tuple[str, str]] = []
    for tr in doc.xpath("//tr"):
        cells = tr.xpath("./td | ./th")
        if len(cells) >= 2:
            label, value = norm(cells[0].text_content()), norm(" ".join(c.text_content() for c in cells[1:]))
            if label and value and len(label) < 200:
                fields.append((label.rstrip(":"), value))
    for dt in doc.xpath("//dt"):
        dd = dt.getnext()
        if dd is not None and dd.tag == "dd":
            fields.append((norm(dt.text_content()).rstrip(":"), norm(dd.text_content())))
    text = norm(doc.text_content())
    if not any(CARD_KEYS.search(lbl) for lbl, _ in fields):
        # подписи и значения не в таблице — ищем «Подпись: значение» в тексте
        for m in re.finditer(r"((?:Цель обработки|Категории [^:]{0,60}|Осуществление трансграничной передачи|"
                             r"Сведения о местонахождении [^:]{0,40})[^:]{0,40}):\s*([^:]{1,600}?)(?=\s+[А-ЯЁ][а-яё ]{5,60}:|$)",
                             text):
            fields.append((norm(m.group(1)), norm(m.group(2))))
    if not any(CARD_KEYS.search(lbl) for lbl, _ in fields):
        raise PortalUnavailable("не удалось разобрать карточку оператора — изменилась разметка портала")
    # у крупных операторов десятки целей обработки: обрезка привела бы к ложному «категория не указана»
    return {"fields": fields[:2000], "text": text[:30000]}


def card_summary(card: dict) -> dict:
    """Что важно для сверки с сайтом: трансграничная передача, где базы данных, какие категории данных заявлены."""
    fields = card.get("fields") or []
    pick = lambda rx: [v for lbl, v in fields if re.search(rx, lbl, re.I)]  # noqa: E731
    tb = " ".join(pick(r"трансгранич"))
    transborder = None
    if tb:
        transborder = not re.search(r"\bнет\b|не\s+осуществля|отсутств", tb, re.I) and bool(
            re.search(r"\bда\b|осуществля|государств|стран", tb, re.I))
    db = " ".join(pick(r"местонахожд|баз[аы]\s+данн"))
    categories = " ".join(pick(r"категори\w*\s+(персональн|обрабатыва)|перечень\s+персональн|персональн\w+\s+данн\w*,?\s+обрабатыв"))
    purposes = " ".join(pick(r"^цел"))
    return {
        "transborder": transborder,
        "transborder_text": tb[:300],
        "db_location": db[:300],
        # «Россия» в сведениях о базах данных; иная страна без России — локализация не соблюдена
        "db_russia": bool(re.search(r"росси", db, re.I)) if db else None,
        # категории иногда расписаны внутри каждой цели — тогда ищем по описанию целей
        "categories_text": (categories or purposes)[:60000],
        "purposes_text": purposes[:4000],
    }


async def _fetch_card(client: httpx.AsyncClient, reg_number: str) -> dict:
    return card_summary(parse_card(await _get(client, {"id": reg_number})))


async def _record_status(ok: bool, error: str = "") -> None:
    async with SessionLocal() as db:
        meta = await kv_get(db, KV_KEY)
        now = utcnow().isoformat()
        meta["requests"] = meta.get("requests", 0) + 1
        if ok:
            meta.update(status="ok", last_ok=now, error="")
        else:
            meta.update(status="error", last_error=now, error=error, errors=meta.get("errors", 0) + 1)
        await kv_set(db, KV_KEY, meta)
        await db.commit()


async def check_inn(inn: str, client: httpx.AsyncClient | None = None, use_cache: bool = True) -> dict:
    """{inn, found, records, checked_at, cached} или {inn, error}."""
    async with SessionLocal() as db:
        row = await db.get(RknCheck, inn)
        if (use_cache and row and row.parser_version >= PARSER_VERSION
                and utcnow() - row.checked_at < (TTL_FOUND if row.found else TTL_NOT_FOUND)):
            return {"inn": inn, "found": row.found, "records": row.records, "checked_at": row.checked_at.isoformat(),
                    "cached": True}
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True)
    try:
        records, debug = await _fetch(client, inn)
        if debug:
            log.info("RKN: %s not found, portal returned %s", inn, debug)
        if records:  # карточка — для сверки сведений уведомления с сайтом; её сбой не отменяет «найден в реестре»
            try:
                records[0]["card"] = await _fetch_card(client, records[0]["reg_number"])
            except PortalUnavailable as e:
                records[0]["card_error"] = str(e)
    except PortalUnavailable as e:
        log.warning("RKN portal unavailable for %s: %s", inn, e)
        await _record_status(False, str(e))
        return {"inn": inn, "error": str(e)}
    finally:
        if own:
            await client.aclose()
    await _record_status(True)
    now = utcnow()
    async with SessionLocal() as db:
        row = await db.get(RknCheck, inn)
        if row:
            row.found, row.records, row.checked_at, row.parser_version = bool(records), records, now, PARSER_VERSION
        else:
            db.add(RknCheck(inn=inn, found=bool(records), records=records, checked_at=now, parser_version=PARSER_VERSION))
        try:
            await db.commit()
        except IntegrityError:  # тот же ИНН одновременно записала другая проверка — её результат не хуже
            await db.rollback()
    return {"inn": inn, "found": bool(records), "records": records, "checked_at": now.isoformat(), "cached": False,
            **({"debug": debug} if debug else {})}


async def lookup(inns: list[str]) -> dict:
    """Данные для проверки rkn_registry. Проверяем до трёх корректных ИНН, найденных на сайте."""
    inns = [i for i in dict.fromkeys(inns) if _inn_ok(i)][:3]
    if not inns:
        return {"checked": False}
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as client:
        results = [await check_inn(inn, client) for inn in inns]
    ok = [r for r in results if "error" not in r]
    return {
        "checked": bool(ok),
        "source": "pd.rkn.gov.ru",
        "matches": {r["inn"]: r["records"] for r in ok},
        "checked_at": max((r["checked_at"] for r in ok), default=None),
        "errors": {r["inn"]: r["error"] for r in results if "error" in r},
        "debug": {r["inn"]: r["debug"] for r in results if r.get("debug")},
    }
