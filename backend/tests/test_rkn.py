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


CARD = """<html><body><table class="TblList">
<tr><td>Регистрационный номер</td><td>11-0187199</td></tr>
<tr><td>Наименование оператора</td><td>ООО «Ромашка»</td></tr>
<tr><td>Цель обработки персональных данных</td><td>обработка заявок; заключение договоров</td></tr>
<tr><td>Категории персональных данных</td><td>фамилия, имя, отчество; адрес электронной почты</td></tr>
<tr><td>Осуществление трансграничной передачи</td><td>нет</td></tr>
<tr><td>Сведения о местонахождении базы данных</td><td>Россия, г. Москва</td></tr>
</table></body></html>"""


def test_parse_card_and_summary():
    from app.rkn import card_summary, parse_card
    s = card_summary(parse_card(CARD))
    assert s["transborder"] is False and s["db_russia"] is True
    assert "адрес электронной почты" in s["categories_text"]


def test_unparseable_card_is_unavailable_not_a_violation():
    from app.rkn import parse_card
    with pytest.raises(PortalUnavailable):
        parse_card("<html><body><p>Сервис временно недоступен</p></body></html>")


def _site_with_card(card, services):
    from tests.test_checks import good_site
    site = good_site()
    site.profile = {"rkn": {"checked": True, "matches": {"7707083893": [
        {"reg_number": "11-0187199", "url": "https://pd.rkn.gov.ru/x", "card": card}]}}}
    site.initial_request_urls = services
    return site


def test_card_cross_check():
    from app.rkn import card_summary, parse_card
    from app.scanner.report import build_report
    by_id = lambda r: {f["id"]: f for f in r["findings"]}  # noqa: E731
    card = card_summary(parse_card(CARD))
    # Google Analytics на сайте, а в уведомлении «трансграничной передачи нет» → нарушение
    f = by_id(build_report(_site_with_card(card, ["https://www.google-analytics.com/g/collect"])))["rkn_notification"]
    assert f["status"] == "fail" and "Google Analytics" in f["summary"]
    # форма собирает телефон, а в уведомлении его нет → риск
    f = by_id(build_report(_site_with_card(card, [])))["rkn_notification"]
    assert f["status"] == "warn" and "номер телефона" in f["summary"]
    # всё совпадает
    full = {**card, "categories_text": card["categories_text"] + "; номер телефона; имя"}
    assert by_id(build_report(_site_with_card(full, [])))["rkn_notification"]["status"] == "pass"
    # карточку не загрузили → только «не удалось сверить»
    assert by_id(build_report(_site_with_card(None, [])))["rkn_notification"]["status"] == "info"


def test_real_portal_card():
    """Настоящая карточка оператора с pd.rkn.gov.ru (ПАО Сбербанк, 11-0187199)."""
    from app.rkn import card_summary, parse_card
    s = card_summary(parse_card((FIX / "rkn_card_11-0187199.html").read_text(encoding="utf-8")))
    assert s["transborder"] is True and s["transborder_text"] == "Да"
    assert s["db_russia"] is True and s["db_location"] == "Россия"
    for cat in ("фамилия", "телефон", "электронной почты"):
        assert cat in s["categories_text"]
    assert "Организация, обеспечение и регулирование трудовых" in s["purposes_text"]


def test_individual_entrepreneur_row_without_inn_label():
    """ИП с 12-значным ИНН: портал может не выводить «ИНН:» в строке — запись всё равно его."""
    page = """<html><body><table id="ResList1"><thead><tr><td>Регистр. номер</td><td>Наименование</td>
    <td>Основание</td><td>Дата</td><td>Дата</td></tr></thead>
    <tr><td>55-19-001234</td><td><a href="?id=55-19-001234">Индивидуальный предприниматель Иванов Иван Иванович</a></td>
    <td>Приказ № 1 от 01.02.2019</td><td>15.01.2019</td><td>10.01.2019</td></tr></table></body></html>"""
    recs = parse_search(page, "550204020120")
    assert len(recs) == 1 and recs[0]["reg_number"] == "55-19-001234"
    assert recs[0]["name"].startswith("Индивидуальный предприниматель")
    # строка с ДРУГИМ ИНН — не наша
    other = page.replace("Иванович</a>", "Иванович</a><br/>ИНН: 550204020199")
    assert parse_search(other, "550204020120") == []
