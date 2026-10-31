"""Документы под конкретный сайт: политика, согласие, тексты чекбоксов, cookie-баннер и др.

Строятся из сохранённого отчёта проверки — доступны для любой завершённой проверки, в том числе пакетной.
"""

from .facts import Facts, from_report
from .render import to_docx, to_markdown
from .templates import Doc, build_documents


def documents_for(report: dict, url: str, checked_at: str) -> list[Doc]:
    return build_documents(from_report(report, url, checked_at))


__all__ = ["Doc", "Facts", "documents_for", "to_docx", "to_markdown"]
