"""Снимок собранных данных проверки (SiteData) — для аудита правил и повторного прогона без сканирования.

Хранится рядом со скриншотами: <data_dir>/shots/<scan_id>/site.json.gz (удаляется вместе с ними).
"""

import dataclasses
import gzip
import json
from pathlib import Path

from .models import Checkbox, Form, FormInput, HttpsInfo, Link, PageData, SiteData

SNAPSHOT_NAME = "site.json.gz"
VERSION = 1


def save(site: SiteData, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SNAPSHOT_NAME
    data = {"version": VERSION, "site": dataclasses.asdict(site)}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return path


def _form(d: dict) -> Form:
    return Form(**{**d, "inputs": [FormInput(**i) for i in d["inputs"]],
                   "checkboxes": [Checkbox(**c) for c in d["checkboxes"]],
                   "links": [Link(**l) for l in d["links"]]})


def _page(d: dict) -> PageData:
    return PageData(**{**d, "links": [Link(**l) for l in d["links"]], "forms": [_form(f) for f in d["forms"]]})


def from_dict(d: dict) -> SiteData:
    known = {f.name for f in dataclasses.fields(SiteData)}
    d = {k: v for k, v in d.items() if k in known}
    return SiteData(**{**d, "pages": [_page(p) for p in d["pages"]],
                       "document_links": [Link(**l) for l in d["document_links"]],
                       "https": HttpsInfo(**d["https"])})


def load(path: Path | str) -> SiteData:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return from_dict(json.load(fh)["site"])
