"""Проверки содержимого: 149-ФЗ, 38-ФЗ, ЗоЗПП, 436-ФЗ, 53-ФЗ."""

import re
from urllib.parse import parse_qs, urlsplit

from ..models import Finding, SiteData
from ..services_db import match_service
from .common import EMAIL_RE, INN_RE, OGRN_RE, abs_link, all_text, short


def check_owner_info(site: SiteData) -> Finding:
    text = all_text(site)
    inn = INN_RE.search(text)
    ogrn = OGRN_RE.search(text)
    email = EMAIL_RE.search(text) or any(l.href.startswith("mailto:") for _, l in site.all_links())
    base = dict(
        id="owner_info", category="Информация о владельце", title="Сведения о владельце сайта",
        law="149-ФЗ, ч. 2 ст. 10; ЗоЗПП, ст. 9",
    )
    ev = [x for x in (inn and inn.group(0), ogrn and ogrn.group(0)) if x]
    if (inn or ogrn) and email:
        return Finding(**base, status="pass", severity="low", evidence=ev,
                       summary="На сайте указаны реквизиты владельца и адрес электронной почты.")
    missing = []
    if not (inn or ogrn):
        missing.append("ИНН/ОГРН и наименование владельца")
    if not email:
        missing.append("адрес электронной почты")
    return Finding(
        **base, status="fail" if len(missing) == 2 else "warn", severity="medium", evidence=ev,
        summary="Не найдено: " + ", ".join(missing) + ".",
        recommendation="Разместите в футере или на странице «Контакты» наименование владельца, ИНН/ОГРН, "
        "адрес и e-mail.",
    )


AUTH_HOSTS = ("accounts.google.com", "appleid.apple.com", "facebook.com/dialog/oauth", "facebook.com/v",
              "api.twitter.com/oauth", "twitter.com/i/oauth2", "x.com/i/oauth2", "github.com/login/oauth",
              "login.microsoftonline.com", "discord.com/oauth2", "telegram.org/js/telegram-widget")
AUTH_TEXT_RE = re.compile(
    r"(войти|вход|зарегистрироваться|продолжить|sign\s?in|log\s?in|continue)\s+(через|с\s+помощью|with)\s+"
    r"(google|apple|facebook|twitter|github|microsoft|discord)", re.I,
)


def check_foreign_auth(site: SiteData) -> Finding:
    ev: list[str] = []
    for u in site.all_request_urls() | {abs_link(p, l) for p, l in site.all_links()}:
        low = u.lower()
        if any(h in low for h in AUTH_HOSTS) and ("oauth" in low or "auth" in low or "signin" in low or "gsi" in low):
            ev.append(short(u))
    for p in site.pages:
        for b in p.buttons + [l.text for l in p.links]:
            if AUTH_TEXT_RE.search(b):
                ev.append(f"{short(p.url, 80)}: «{b[:60]}»")
    base = dict(
        id="foreign_auth", category="Информация и технологии", title="Авторизация через иностранные сервисы",
        law="149-ФЗ, ст. 10 (ред. 406-ФЗ, с 01.12.2023)",
    )
    ev = list(dict.fromkeys(ev))
    if not ev:
        return Finding(**base, status="pass", severity="low", summary="Вход через иностранные сервисы не обнаружен.")
    return Finding(
        **base, status="fail", severity="high", evidence=ev[:10],
        summary="Обнаружена авторизация через иностранные сервисы. Российских пользователей разрешено "
        "авторизовать только по номеру телефона РФ, через Госуслуги, ЕБС или российские сервисы (VK ID, Яндекс ID и др.).",
        recommendation="Уберите кнопки входа через Google/Apple/Facebook и т.п. или оставьте их только для иностранных "
        "пользователей; добавьте вход по номеру телефона, VK ID, Яндекс ID или Сбер ID.",
    )


META_DOMAINS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "threads.net", "threads.com")
META_MARK_RE = re.compile(r"экстремист|запрещ\w*\s+(в|на\s+территории)\s+(росси|рф)|meta\s+platforms", re.I)


def check_meta_links(site: SiteData) -> Finding:
    pages_with: dict[str, list[str]] = {}
    for p in site.pages:
        hits = [
            l for l in p.links
            if any((urlsplit(abs_link(p, l)).hostname or "").endswith(d) for d in META_DOMAINS)
        ]
        if hits and not META_MARK_RE.search(p.text):
            pages_with[p.url] = [short(abs_link(p, l), 80) for l in hits[:2]]
    base = dict(
        id="meta_links", category="Информация и технологии", title="Упоминание продуктов Meta",
        law="114-ФЗ «О противодействии экстремистской деятельности»; ст. 20.3 КоАП РФ",
    )
    if not pages_with:
        return Finding(**base, status="pass", severity="low",
                       summary="Ссылок на Instagram/Facebook без пометки не обнаружено.")
    ev = [f"{short(u, 80)} → {', '.join(v)}" for u, v in list(pages_with.items())[:8]]
    return Finding(
        **base, status="warn", severity="medium", evidence=ev,
        summary=f"На {len(pages_with)} стр. есть ссылки на Instagram/Facebook без пометки о том, что Meta признана "
        "экстремистской организацией и запрещена в РФ.",
        recommendation="Уберите иконки и ссылки на соцсети Meta либо добавьте сноску: «Instagram/Facebook — продукты "
        "компании Meta Platforms Inc., деятельность которой запрещена в России».",
    )


def check_ad_marking(site: SiteData) -> Finding:
    ad_networks = {svc.name for u in site.all_request_urls() if (svc := match_service(u)) and svc.category == "ads"}
    erid_links = [abs_link(p, l) for p, l in site.all_links() if "erid" in parse_qs(urlsplit(l.href).query)]
    text = all_text(site)
    marked = bool(re.search(r"\berid\b|(^|\n)\s*реклама\s*[.\n]|реклама\.\s*(ООО|ИП|АО|ПАО)", text, re.I))
    base = dict(
        id="ad_marking", category="Реклама", title="Маркировка интернет-рекламы",
        law="38-ФЗ «О рекламе», ст. 18.1",
    )
    if not ad_networks and not erid_links:
        return Finding(**base, status="info", severity="low",
                       summary="Рекламные сети и рекламные ссылки на сайте не обнаружены.")
    if erid_links or marked:
        return Finding(**base, status="pass", severity="low", evidence=[short(u) for u in erid_links[:5]],
                       summary="Найдены признаки маркировки рекламы (erid / пометка «Реклама»).")
    return Finding(
        **base, status="warn", severity="medium", evidence=sorted(ad_networks),
        summary="На сайте подключены рекламные сети, но признаков маркировки (erid, «Реклама», рекламодатель) не найдено.",
        recommendation="Каждый рекламный материал должен иметь пометку «Реклама», сведения о рекламодателе и токен erid, "
        "полученный через ОРД. Проверьте баннеры и партнёрские ссылки.",
    )


SHOP_RE = re.compile(r"в\s+корзину|оформить\s+заказ|корзина|купить|добавить\s+в\s+корзину|checkout|add\s+to\s+cart", re.I)
PRICE_RE = re.compile(r"\d[\d\s]{0,9}(?:[.,]\d{1,2})?\s?(₽|руб)", re.I)
FOREIGN_PRICE_RE = re.compile(r"(\$|€|USD|EUR)\s?\d|\d\s?(\$|€|USD|EUR)")


def check_ecommerce(site: SiteData) -> Finding | None:
    text = all_text(site)
    btns = " ".join(b for p in site.pages for b in p.buttons)
    if not (SHOP_RE.search(btns) or len(SHOP_RE.findall(text)) >= 3) or not (PRICE_RE.search(text) or FOREIGN_PRICE_RE.search(text)):
        return None
    links_text = " ".join(f"{l.text} {l.href}" for _, l in site.all_links()) + " " + " ".join(d.href for d in site.document_links)
    hay = links_text + " " + text
    req = {
        "публичная оферта / условия продажи": r"оферт|oferta|offer|публичн\w*\s+договор|услови\w*\s+продаж",
        "условия и порядок возврата товара": r"возврат|vozvrat|return",
        "условия доставки": r"доставк|dostavk|delivery|самовывоз",
        "способы оплаты": r"оплат|oplat|payment",
        "реквизиты продавца (ИНН/ОГРН)": INN_RE.pattern + "|" + OGRN_RE.pattern,
    }
    missing = [k for k, rx in req.items() if not re.search(rx, hay, re.I)]
    if FOREIGN_PRICE_RE.search(text) and not PRICE_RE.search(text):
        missing.append("цены в рублях (найдены цены только в иностранной валюте)")
    base = dict(
        id="ecommerce", category="Защита прав потребителей", title="Дистанционная торговля",
        law="ЗоЗПП, ст. 8–10, 26.1; Правила продажи товаров (ПП РФ № 2463)",
    )
    if not missing:
        return Finding(**base, status="pass", severity="low",
                       summary="Сайт похож на интернет-магазин; обязательная информация для покупателя найдена.")
    return Finding(
        **base, status="fail" if len(missing) >= 3 else "warn", severity="medium",
        summary="Сайт похож на интернет-магазин, но не найдено: " + "; ".join(missing) + ".",
        recommendation="До заключения договора покупатель должен видеть сведения о продавце, цене, условиях "
        "оплаты, доставки и возврата. Разместите эти разделы и ссылки на них в футере.",
    )


AGE_MARK = r"(?<![\d.,:+])(?:0|6|12|16|18)\s?\+(?!\d)"
# Знак засчитывается, только если стоит отдельным элементом или рядом с ©, а не внутри фразы
# («Как добавить предупреждение 18+?» — это не маркировка сайта)
AGE_RE = re.compile(rf"(?:^|\n)[ \t]*({AGE_MARK})[ \t]*(?:\n|$)|©[^\n]{{0,200}}?({AGE_MARK})|({AGE_MARK})[^\n]{{0,40}}©")


def check_age_rating(site: SiteData) -> Finding:
    found = next((next(g for g in m.groups() if g).replace(" ", "")
                  for p in site.pages for m in [AGE_RE.search(p.text)] if m), None)
    base = dict(
        id="age_rating", category="Информация и технологии", title="Возрастная маркировка",
        law="436-ФЗ «О защите детей от информации…», ст. 6, 12",
    )
    if found:
        return Finding(**base, status="pass", severity="low", evidence=[found], summary=f"Найден знак возрастной категории: {found}.")
    return Finding(
        **base, status="warn", severity="low",
        summary="Знак информационной продукции (0+, 6+, 12+, 16+, 18+) не найден. Требование обязательно для "
        "сайтов, распространяющих информационную продукцию (СМИ, медиа, развлекательный контент, игры, курсы).",
        recommendation="Если сайт содержит информационную продукцию, укажите возрастную категорию в шапке или футере.",
    )


def check_language(site: SiteData) -> Finding:
    home = site.home
    text = home.text if home else ""
    cyr = len(re.findall(r"\b[а-яё]{3,}\b", text, re.I))
    lat = len(re.findall(r"\b[a-z]{3,}\b", text, re.I))
    ratio = lat / (cyr + lat) if cyr + lat else 0
    base = dict(
        id="language", category="Защита прав потребителей", title="Использование русского языка",
        law="53-ФЗ «О государственном языке РФ» (ред. 168-ФЗ, с 01.03.2026); ЗоЗПП, ст. 8",
        evidence=[f"Доля латиницы на главной: {ratio:.0%}", f"lang=\"{home.lang if home else ''}\""],
    )
    if cyr + lat < 30:
        return Finding(**base, status="info", severity="low", summary="Недостаточно текста для оценки языка.")
    if ratio > 0.5:
        return Finding(
            **base, status="fail", severity="medium",
            summary="Основной текст сайта не на русском языке. Информация для потребителей в РФ должна "
            "предоставляться на русском языке.",
            recommendation="Сделайте русскую версию основной версией сайта для российских пользователей.",
        )
    if ratio > 0.2:
        return Finding(
            **base, status="warn", severity="low",
            summary=f"Много текста латиницей ({ratio:.0%}). С 1 марта 2026 года иностранные слова в информации "
            "для потребителей допускаются только с переводом на русский (кроме товарных знаков).",
            recommendation="Переведите навигацию, кнопки и описания на русский язык, иностранные термины дублируйте переводом.",
        )
    return Finding(**base, status="pass", severity="low", summary="Сайт преимущественно на русском языке.")
