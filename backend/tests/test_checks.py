import pytest

from app.scanner.models import Checkbox, Form, FormInput, HttpsInfo, Link, PageData, SiteData
from app.scanner.report import build_report
from app.scanner.urls import UrlError, normalize_input

POLICY_TEXT = (
    "Политика в отношении обработки персональных данных. Оператор ООО «Ромашка», ИНН 7707083893. "
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
        "ИНН 7707083893 ОГРН 1027700132195. 12+ " * 5,
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
    assert f["foreign_services"]["status"] == "warn"  # GTM без других счётчиков — риск, а не нарушение
    assert "Google Tag Manager" in f["foreign_services"]["summary"]
    assert f["cookies"]["status"] == "fail"
    site.pages[0].request_urls.append("https://www.google-analytics.com/g/collect")
    assert by_id(build_report(site))["foreign_services"]["status"] == "fail"


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


def test_invalid_inn_detected():
    site = good_site()
    site.pages[0].text = site.pages[0].text.replace("7707083893", "7707083890")
    assert by_id(build_report(site))["inn_valid"]["status"] == "fail"


def test_newsletter_without_ad_consent():
    site = good_site()
    site.pages[0].forms.append(Form(
        action="/sub", method="post", inputs=[FormInput("email", "email", "Ваш e-mail")],
        checkboxes=[], text="Подпишитесь на рассылку", links=[], submit="Подписаться",
    ))
    assert by_id(build_report(site))["newsletter_consent"]["status"] == "fail"


def test_foreign_platform_with_forms():
    site = good_site()
    site.pages[0].html = '<script src="https://static.parastorage.com/x.js"></script>'
    r = build_report(site)
    assert r["profile"]["platform"]["name"] == "Wix"
    assert by_id(r)["foreign_platform"]["status"] == "fail"


def test_medical_site_profile_and_checks():
    site = good_site()
    site.pages[0].text += " Клиника. Приём врача-стоматолога, лечение, диагностика, анализы для пациентов."
    r = build_report(site)
    assert any(t["code"] == "medical" for t in r["profile"]["types"])
    f = by_id(r)
    assert f["medical_info"]["status"] == "fail"
    assert f["medical_ads"]["status"] == "warn"


def test_rkn_registry_lookup():
    site = good_site()
    site.profile = {"rkn": {"loaded": True, "updated": "2026-10-01", "matches": {"7707083893": []}}}
    assert by_id(build_report(site))["rkn_registry"]["status"] == "fail"
    site = good_site()
    site.profile = {"rkn": {"loaded": True, "matches": {"7707083893": [{"reg_number": "77-14-000123"}]}}}
    assert by_id(build_report(site))["rkn_registry"]["status"] == "pass"


def test_penalties_come_from_registry():
    site = good_site()
    site.https = HttpsInfo(https_ok=True, http_redirects_to_https=True, hsts=True)
    site.server_country = "DE"
    assert "ч. 8, 9 ст. 13.11" in by_id(build_report(site))["hosting"]["penalty"]


def test_blocked_page_detection():
    from app.scanner.crawler import _looks_blocked
    assert _looks_blocked(PageData(url="https://x.ru/", status=200, text=""))
    assert _looks_blocked(PageData(url="https://x.ru/", status=200, text="Checking your browser before accessing", links=[Link("/", "")] * 5))
    assert _looks_blocked(PageData(url="https://x.ru/", status=403, text="Forbidden"))
    assert not _looks_blocked(good_site().pages[0])


@pytest.mark.parametrize("name,expected", [
    ("microphone", False), ("hotel", False), ("speaker", False),
    ("user_phone", True), ("firstName", True), ("email", True), ("Ваше имя", True), ("form[phone]", True),
])
def test_pd_input_detection(name, expected):
    from app.scanner.checks.common import input_is_pd
    assert input_is_pd(FormInput("text", name)) is expected


@pytest.mark.parametrize("text,expected", [
    ("Меню\n12+\nКонтакты", "12+"),
    ("© 2026 ООО Ромашка. 0+", "0+"),
    ("16+ © Ромашка", "16+"),
    ("Как добавить предупреждение о возрастном ограничении (например, 18+)?", None),
    ("Скидка 10+ процентов", None),
])
def test_age_mark_must_stand_alone(text, expected):
    from app.scanner.checks.content import check_age_rating
    site = good_site()
    for p in site.pages:
        p.text = text
    f = check_age_rating(site)
    assert (f.status == "pass") == (expected is not None)
    if expected:
        assert f.evidence == [expected]


def test_service_forms_do_not_need_consent():
    site = good_site()
    site.pages[0].forms = [
        Form(action="/auth", method="post", inputs=[FormInput("text", "USER_EMAIL", "E-mail")], checkboxes=[],
             text="Забыли пароль? Введите e-mail", links=[], submit="Выслать"),
        Form(action="/sub", method="post", inputs=[FormInput("text", "sf_EMAIL", "")], checkboxes=[],
             text="Изменить подписку. Если Вы уже подписаны на рассылку, введите e-mail", links=[],
             submit="Выслать код подтверждения"),
    ]
    f = by_id(build_report(site))
    assert f["forms_consent"]["status"] == "info"  # форм сбора ПДн новых людей нет
    assert "newsletter_consent" not in f


def test_bitrix_consent_popup_counts_as_separate_document():
    site = good_site()
    for p in site.pages:
        p.links = [l for l in p.links if "soglas" not in l.href]
    site.pages[0].forms[0].links = []
    assert by_id(build_report(site))["separate_consent"]["status"] == "fail"
    site.pages[0].consent_popup = True
    assert by_id(build_report(site))["separate_consent"]["status"] == "pass"


def test_travel_not_detected_from_word_hotel_in_verbs():
    from app.scanner.profile import build_profile
    site = good_site()
    site.pages[0].text = "Мы хотели бы рассказать. Вы хотели узнать номер телефона? Тур-нир. " * 5
    assert not any(t["code"] == "travel" for t in build_profile(site)["types"])
