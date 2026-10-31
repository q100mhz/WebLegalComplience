from pathlib import Path

import pytest

from app.rkn import PortalUnavailable, _inn_ok, card_url, parse_search

FIX = Path(__file__).parent / "fixtures"


def test_parse_real_portal_result():
    page = (FIX / "rkn_search_7707083893.html").read_text(encoding="utf-8")
    recs = parse_search(page, "7707083893")
    assert len(recs) == 1
    r = recs[0]
    assert r["reg_number"] == "11-0187199"
    assert r["name"] == 'Публичное акционерное общество "Сбербанк России"'
    assert r["reg_date"] == "24.03.2011" and r["start_date"] == "26.07.1991"
    assert "229" in r["basis"]
    assert r["url"] == card_url("11-0187199")


def test_other_inn_rows_are_ignored():
    page = (FIX / "rkn_search_7707083893.html").read_text(encoding="utf-8")
    assert parse_search(page, "7736207543") == []


def test_empty_result_table_means_not_found():
    page = """<html><body><table id="ResList1"><thead><tr><td>Регистр. номер</td><td>Наименование</td>
    <td>Основание</td><td>Дата</td><td>Дата</td></tr></thead></table></body></html>"""
    assert parse_search(page, "7707083893") == []


def test_search_form_without_results_is_not_a_negative_answer():
    # Страница без таблицы результатов (например, сменилась разметка) — «не знаем», а не «не найден»
    page = (FIX / "rkn_operators_list.html").read_text(encoding="utf-8")
    with pytest.raises(PortalUnavailable):
        parse_search(page, "7707083893")


def test_captcha_is_unavailable():
    with pytest.raises(PortalUnavailable):
        parse_search("<html><body>Введите код с картинки (captcha)</body></html>", "7707083893")


def test_inn_checksum():
    assert _inn_ok("7707083893") and not _inn_ok("7707083890")
