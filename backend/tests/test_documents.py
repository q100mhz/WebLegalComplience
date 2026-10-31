import io
import zipfile

from app.documents import documents_for, to_docx, to_markdown
from app.documents.howto import howto
from app.scanner.report import build_report
from tests.test_checks import good_site


def report_for(site):
    site.pages[0].text += "\\nООО «Ромашка», ИНН 7707083893. Юридический адрес: 117997, г. Москва, ул. Вавилова, д. 19"
    return build_report(site)


def test_documents_use_site_facts():
    site = good_site()
    site.initial_request_urls = ["https://mc.yandex.ru/metrika/tag.js", "https://www.google-analytics.com/g/collect"]
    site.initial_cookies = [{"name": "_ym_uid", "domain": ".example.ru"}]
    docs = {d.id: d for d in documents_for(report_for(site), "https://example.ru/", "2026-11-01T10:00:00")}
    assert {"privacy_policy", "consent", "checkboxes", "cookie_banner"} <= set(docs)
    policy = to_markdown(docs["privacy_policy"])
    assert "ООО «Ромашка»" in policy and "7707083893" in policy
    assert "имя" in policy and "номер телефона" in policy  # поля формы → категории данных
    assert "Яндекс Метрика" in policy and "Трансграничная передача" in policy  # GA — иностранный сервис
    assert "_ym_uid" in policy
    consent = to_markdown(docs["consent"])
    for must in ("Перечень персональных данных", "Цели обработки", "Срок действия и отзыв"):
        assert must in consent


def test_missing_facts_become_placeholders_not_inventions():
    site = good_site()
    for p in site.pages:
        p.text = "Сайт без реквизитов"
    docs = {d.id: d for d in documents_for(build_report(site), "https://example.ru/", "2026-11-01")}
    policy = to_markdown(docs["privacy_policy"])
    assert "[указать полное наименование" in policy and "[указать ИНН]" in policy
    assert "cookie_banner" not in docs  # метрик нет — баннер не предлагаем


def test_docx_is_valid_word_file():
    site = good_site()
    doc = documents_for(report_for(site), "https://example.ru/", "2026-11-01")[0]
    data = to_docx(doc)
    assert zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode().count("Политика") >= 1


def test_howto_platform_specific_and_fallback():
    assert "USER_CONSENT_IS_CHECKED" in " ".join(howto("forms_consent", "1С-Битрикс")["steps"])
    generic = howto("forms_consent", "Joomla")
    assert generic["platform"] is None and generic["steps"]
    assert howto("unknown_rule", "Tilda") is None
