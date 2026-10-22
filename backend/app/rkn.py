"""Реестр операторов ПДн из открытых данных Роскомнадзора.

Набор «Реестр операторов, осуществляющих обработку персональных данных» публикуется на rkn.gov.ru/opendata
в виде XML (обычно в zip). Сайт РКН доступен только с российских IP — импорт работает на VPS в РФ.
Структура XML со временем меняется, поэтому парсер не привязан к точным именам тегов:
запись — любой элемент, у которого есть дочерний элемент `inn`.
"""

import asyncio
import csv
import io
import logging
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import iterparse

import httpx
from sqlalchemy import delete, insert, select

from .config import settings
from .db import RknOperator, SessionLocal, kv_get, kv_set

log = logging.getLogger(__name__)
KV_KEY = "rkn_import"
# Сайт РКН отвечает 403 на запросы с «ботовым» User-Agent — представляемся обычным браузером
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
BATCH = 5000

NAME_TAGS = ("name_full", "name", "fullname", "operator_name")
REG_TAGS = ("regn", "pd_operator_num", "reg_number", "regnum", "num")
DATE_TAGS = ("reg_date", "income_date", "startdate", "date_reg", "register_date")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


DATASET_RE = re.compile(r'href="([^"]*7705846236-[^"/]*Operator[^"/]*/?)"', re.I)
CATALOG_URL = "https://rkn.gov.ru/opendata/list.csv"
OPERATORS_ROW_RE = re.compile(r"OperatorsPD|операторов[^;,]{0,60}персональн|персональн[^;,]{0,40}операторов", re.I)
URL_RE = re.compile(r"(?:https?://[^\s;,\"']+|/opendata/[^\s;,\"']+)")
DATA_FILE_RE = re.compile(r"data-\d{8}.*\.(zip|xml)$", re.I)


def _catalog_candidates(text: str) -> list[str]:
    """Ссылки из строк каталога list.csv, относящихся к реестру операторов ПДн."""
    found = []
    for row in csv.reader(io.StringIO(text), delimiter=";" if text.count(";") > text.count(",") else ","):
        line = " ".join(row)
        if OPERATORS_ROW_RE.search(line):
            found += [str(httpx.URL("https://rkn.gov.ru/").join(u.strip())) for u in URL_RE.findall(line)]
    return list(dict.fromkeys(found))


def _as_base(url: str) -> str:
    """Адрес страницы набора: из ссылки на meta.csv или саму страницу."""
    if url.lower().endswith((".csv", ".xml", ".json")) and "/meta" in url.lower():
        return url.rsplit("/", 1)[0] + "/"
    return url.rstrip("/") + "/"


async def _dataset_pages(client: httpx.AsyncClient) -> tuple[list[str], list[str]]:
    """(страницы набора, прямые ссылки на файлы). Источники: настройка RKN_OPENDATA_URL,
    каталог rkn.gov.ru/opendata/list.csv (текущий формат РКН) и HTML-страница каталога (старый формат)."""
    pages = [settings.rkn_opendata_url.rstrip("/") + "/"]
    files: list[str] = []
    try:
        r = await client.get(CATALOG_URL)
        if r.status_code == 200:
            for u in _catalog_candidates(r.content.decode("utf-8-sig", errors="replace")):
                (files if DATA_FILE_RE.search(u) else pages).append(u if DATA_FILE_RE.search(u) else _as_base(u))
    except httpx.HTTPError:
        log.info("RKN catalog list.csv not available")
    try:
        r = await client.get("https://rkn.gov.ru/opendata/")
        pages += [str(httpx.URL("https://rkn.gov.ru/opendata/").join(h)).rstrip("/") + "/"
                  for h in DATASET_RE.findall(r.text)]
    except httpx.HTTPError:
        log.info("RKN opendata page not available")
    return list(dict.fromkeys(pages)), list(dict.fromkeys(files))


async def _find_data_url(client: httpx.AsyncClient) -> str:
    tried = []
    pages, files = await _dataset_pages(client)
    if files:
        return sorted(files)[-1]  # имя файла содержит дату — берём самый свежий
    for base in pages:
        tried.append(base)
        try:
            r = await client.get(base + "meta.csv")
            if r.status_code == 200:
                for row in csv.reader(io.StringIO(r.content.decode("utf-8-sig", errors="replace"))):
                    for cell in row:
                        if re.search(r"data-\d{8}.*\.(zip|xml)", cell):
                            return cell.strip()
            r = await client.get(base)
            if r.status_code != 200:
                continue
            links = re.findall(r'href="([^"]*data-\d{8}[^"]*\.(?:zip|xml))"', r.text)
            if links:
                return str(httpx.URL(base).join(sorted(links)[-1]))
        except httpx.HTTPError as e:
            log.info("RKN dataset page %s failed: %s", base, e)
    raise RuntimeError("Не найден файл реестра операторов ПДн. Проверены адреса: " + ", ".join(tried)
                       + ". Укажите актуальный адрес набора в RKN_OPENDATA_URL (.env).")


def _parse(xml_stream) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for _, el in iterparse(xml_stream, events=("end",)):
        children = {_local(c.tag): (c.text or "").strip() for c in el}
        inn = children.get("inn", "")
        if inn and re.fullmatch(r"\d{10}|\d{12}", inn):
            batch.append({
                "inn": inn,
                "name": next((children[t] for t in NAME_TAGS if children.get(t)), "")[:1000],
                "reg_number": next((children[t] for t in REG_TAGS if children.get(t)), "")[:64],
                "reg_date": next((children[t] for t in DATE_TAGS if children.get(t)), "")[:32],
            })
            el.clear()
            if len(batch) >= BATCH:
                yield batch
                batch = []
    if batch:
        yield batch


async def import_registry(ctx=None) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    async with SessionLocal() as db:
        await kv_set(db, KV_KEY, {**(await kv_get(db, KV_KEY)), "status": "running", "started_at": started})
        await db.commit()
    tmp_dir = Path(settings.data_dir) / "rkn"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / "registry.download"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=600), follow_redirects=True,
                                     headers=BROWSER_HEADERS) as client:
            url = await _find_data_url(client)
            log.info("downloading RKN registry %s", url)
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with path.open("wb") as fh:
                    async for chunk in r.aiter_bytes(1 << 20):
                        fh.write(chunk)
        m = re.search(r"data-(\d{4})(\d{2})(\d{2})", url)
        updated = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""

        rows = 0
        async with SessionLocal() as db:  # одна транзакция: до коммита поиск видит старые данные
            await db.execute(delete(RknOperator))
            zf = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else None
            try:
                if zf:
                    fh = zf.open(next(n for n in zf.namelist() if n.lower().endswith(".xml")))
                else:
                    fh = path.open("rb")
                with fh:
                    batches = _parse(fh)
                    # разбор XML — в отдельном потоке, чтобы не блокировать сканирования в этом воркере
                    while batch := await asyncio.to_thread(next, batches, None):
                        await db.execute(insert(RknOperator), batch)
                        rows += len(batch)
            finally:
                if zf:
                    zf.close()
            if rows == 0:
                raise RuntimeError("В файле реестра не найдено ни одной записи — вероятно, изменился формат")
            status = {"status": "ok", "rows": rows, "updated": updated, "source": url,
                      "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat()}
            await kv_set(db, KV_KEY, status)
            await db.commit()
        log.info("RKN registry imported: %s rows", rows)
        return status
    except Exception as e:
        log.exception("RKN import failed")
        async with SessionLocal() as db:
            prev = await kv_get(db, KV_KEY)
            await kv_set(db, KV_KEY, {**prev, "status": "error", "error": str(e)[:500],
                                      "finished_at": datetime.now(timezone.utc).isoformat()})
            await db.commit()
        return {"status": "error", "error": str(e)}
    finally:
        path.unlink(missing_ok=True)


async def lookup(inns: list[str]) -> dict:
    """Данные для проверки rkn_registry: загружен ли реестр и записи по найденным ИНН."""
    async with SessionLocal() as db:
        meta = await kv_get(db, KV_KEY)
        if not meta.get("rows"):
            return {"loaded": False}
        matches: dict[str, list[dict]] = {inn: [] for inn in inns}
        if inns:
            res = await db.execute(select(RknOperator).where(RknOperator.inn.in_(inns)).limit(50))
            for op in res.scalars():
                matches[op.inn].append({"name": op.name, "reg_number": op.reg_number, "reg_date": op.reg_date})
    return {"loaded": True, "updated": meta.get("updated"), "matches": matches}
