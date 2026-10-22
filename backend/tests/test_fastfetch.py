from app.scanner.checks.common import form_is_pd
from app.scanner.fastfetch import parse_html

HTML = """<html lang="ru"><head><meta charset="windows-1251"><title>Контакты</title>
<meta name="generator" content="Bitrix">
<script src="https://mc.yandex.ru/metrika/tag.js"></script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Roboto"></head>
<body><div>Компания Ромашка</div><p>ИНН 7707083893</p>
<div hidden>скрытый текст</div><script>var x = "не текст";</script>
<form action="/send" method="post">
  <input name="name" placeholder="Ваше имя"><input type="tel" name="phone">
  <input type="hidden" name="csrf">
  <label><input type="checkbox" checked> Согласен на обработку персональных данных</label>
  <a href="/privacy/">политика</a><button type="submit">Отправить</button>
</form>
<a href="/about/">О компании</a></body></html>""".encode("cp1251")


def test_parse_html_extracts_like_browser():
    p = parse_html("https://x.ru/contacts/", 200, HTML, None, {})
    assert p.title == "Контакты" and p.lang == "ru" and p.generator == "Bitrix"
    assert "Компания Ромашка" in p.text and "ИНН 7707083893" in p.text
    assert "скрытый текст" not in p.text and "не текст" not in p.text
    assert "https://mc.yandex.ru/metrika/tag.js" in p.request_urls
    assert any("fonts.googleapis.com" in u for u in p.request_urls)
    assert len(p.forms) == 1
    f = p.forms[0]
    assert [i.name for i in f.inputs] == ["name", "phone"] and form_is_pd(f)
    assert f.checkboxes[0].checked and "персональных" in f.checkboxes[0].label
    assert f.submit == "Отправить" and f.links[0].href == "/privacy/"
    assert {l.href for l in p.links} >= {"/privacy/", "/about/"}
    assert p.via == "http"
