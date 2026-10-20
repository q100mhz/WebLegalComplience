import pytest

from app.scanner.models import Checkbox, Form, FormInput, HttpsInfo, Link, PageData, SiteData
from app.scanner.report import build_report
from app.scanner.urls import UrlError, normalize_input

POLICY_TEXT = (
    "Политика в отношении обработки персональных данных. Оператор ООО «Ромашка», ИНН 7701234567. "
    "Цели обработки персональных данных. Правовые основания обработки. Перечень персональных данных. "
    "Категории субъектов персональных данных. Способы обработки. Сроки обработки и хранения. "
    "Порядок уничтожения. Права субъекта персональных данных. Меры по защите персональных данных. "
    "Мы используем файлы cookie и Яндекс Метрику. " * 3
)


def good_site() -> SiteData:
    form = Form(
        action="/send", method="post",
        inputs=[FormInput("text", "name", "Имя"), FormInput("tel", "phone", "Телефон")],
        checkboxes=[Checkbox(False, "Я даю согласие на обработку персональных данных")],
        text="Имя Телефон Я даю согласие на обработку персональных данных",
        links=[Link("/soglasie", "согласие на обработку персональных данных")],
    )
    footer = [Link("/privacy", "Политика конфиденциальности"), Link("/soglasie", "Согласие на обработку ПДн")]
    home = PageData(
        url="https://example.ru/", status=200, lang="ru",
        text="Главная страница компании Ромашка. Мы делаем отличные сайты для бизнеса. Контакты: info@example.ru "
        "ИНН 7701234567 ОГРН 1027700132195. 12+ " * 5,
        links=footer, forms=[form],
    )
    policy = PageData(url="https://example.ru/privacy", status=200, title="Политика конфиденциальности",
                      text=POLICY_TEXT, links=footer)
    return SiteData(
        start_url="https://example.ru/", host="example.ru", pages=[home, policy],
        https=HttpsInfo(https_ok=True, http_redirects_to_https=True, hsts=True),
        server_ip="1.2.3.4", server_country="RU",
    )


def by_id(report):
    return {f["id"]: f for f in report["findings"]}


def test_good_site_has_no_failures():
    r = build_report(good_site())
    fails = [f["id"] for f in r["findings"] if f["status"] == "fail"]
    assert fails == []
    assert r["score"] >= 85


def test_missing_policy_and_consent():
    site = good_site()
    site.pages = site.pages[:1]
    site.pages[0].links = []
    site.pages[0].forms[0].checkboxes = []
    site.pages[0].forms[0].links = []
    site.pages[0].forms[0].text = "Имя Телефон Отправить"
    f = by_id(build_report(site))
    assert f["privacy_policy"]["status"] == "fail"
    assert f["forms_consent"]["status"] == "fail"
    assert f["separate_consent"]["status"] == "fail"


def test_prechecked_checkbox_fails():
    site = good_site()
    site.pages[0].forms[0].checkboxes[0].checked = True
    assert by_id(build_report(site))["forms_consent"]["status"] == "fail"


def test_implied_consent_warns():
    site = good_site()
    site.pages[0].forms[0].checkboxes = []
    site.pages[0].forms[0].text = "Нажимая кнопку «Отправить», вы соглашаетесь с политикой"
    assert by_id(build_report(site))["forms_consent"]["status"] == "warn"


def test_foreign_trackers_and_cookies():
    site = good_site()
    site.initial_request_urls = ["https://www.googletagmanager.com/gtag/js?id=G-1", "https://fonts.googleapis.com/css"]
    site.initial_cookies = [{"name": "_ga", "domain": ".example.ru"}]
    site.pages[0].text = site.pages[0].text.replace("cookie", "")
    f = by_id(build_report(site))
    assert f["foreign_services"]["status"] == "fail"
    assert "Google Tag Manager" in f["foreign_services"]["summary"]
    assert f["cookies"]["status"] == "fail"


def test_foreign_auth_and_meta():
    site = good_site()
    site.pages[0].buttons = ["Войти через Google"]
    site.pages[0].links.append(Link("https://instagram.com/romashka", ""))
    f = by_id(build_report(site))
    assert f["foreign_auth"]["status"] == "fail"
    assert f["meta_links"]["status"] == "warn"


def test_foreign_hosting_and_no_https():
    site = good_site()
    site.server_country = "DE"
    site.https = HttpsInfo(https_ok=False, cert_error="certificate expired")
    f = by_id(build_report(site))
    assert f["hosting"]["status"] == "warn"
    assert f["https"]["status"] == "fail"


def test_shop_without_offer():
    site = good_site()
    site.pages[0].buttons = ["В корзину"]
    site.pages[0].text += " Чайник 1 990 ₽ Купить. Корзина."
    f = by_id(build_report(site))
    assert f["ecommerce"]["status"] in ("warn", "fail")


@pytest.mark.parametrize("raw,expected", [
    ("example.ru", "https://example.ru/"),
    ("HTTP://Example.RU/path", "http://example.ru/path"),
    ("кто.рф", "https://xn--j1ail.xn--p1ai/"),
])
def test_normalize(raw, expected):
    assert normalize_input(raw) == expected


@pytest.mark.parametrize("raw", ["", "ftp://x.ru", "localhost", "http://x.ru:8080"])
def test_normalize_rejects(raw):
    with pytest.raises(UrlError):
        normalize_input(raw)


def test_news_politics_link_is_not_policy():
    from app.scanner.checks.common import link_is_policy
    assert not link_is_policy(Link("https://lenta.ru/rubrics/world/politic/", "Политика"))
    assert link_is_policy(Link("/politika-konfidencialnosti/", "Документ"))
    assert link_is_policy(Link("/x", "Политика обработки персональных данных"))
