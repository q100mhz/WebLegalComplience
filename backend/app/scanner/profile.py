"""Профиль сайта: тип бизнеса, платформа, функциональные блоки и назначение форм.

Профиль определяет, какие отраслевые проверки применимы, и показывается в отчёте —
так пользователь видит, что сервис «понял» его сайт.
"""

import re
from collections import Counter

from .checks.common import all_text, field_label, form_is_pd, form_signature, input_is_pd, is_service_form, page_forms
from .models import Form, SiteData
from .services_db import match_service, match_tracking_cookie

# Тип сайта: (название, регулярка, минимум разных совпадений)
SITE_TYPES: dict[str, tuple[str, str, int]] = {
    "shop": ("Интернет-магазин", r"в\s+корзину|корзина|оформить\s+заказ|купить|каталог\s+товаров|доставка\s+товар|артикул", 3),
    "services": ("Услуги", r"услуг[аи]|оставить\s+заявку|заказать\s+звонок|консультаци|прайс|стоимость\s+работ|выезд\s+специалиста", 3),
    "education": ("Обучение / онлайн-школа", r"онлайн-курс|курс[аы]?\s|обучени|онлайн-школ|вебинар|урок[иов]?\b|наставник|образовательн|репетитор|тренинг|программа\s+курса|домашн\w+\s+задани|выпускник", 5),
    "medical": ("Медицина", r"клиник|медицинск|врач|при[её]м\s+врача|стоматолог|лечени|диагностик|анализ[ыов]|пациент|узи\b|мрт\b|медцентр", 4),
    "finance": ("Финансы", r"кредит|займ|микрозайм|ставка\s+от|инвестиц|брокер|страхован|ипотек|процент\w*\s+годовых|вклад", 4),
    "realestate": ("Недвижимость", r"новостройк|квартир[аы]|застройщик|жилой\s+комплекс|планировк|ипотек", 4),
    "saas": ("Онлайн-сервис / SaaS", r"тариф|бесплатный\s+период|попробовать\s+бесплатно|личный\s+кабинет|\bapi\b|интеграци|регистрация|"
             r"конструктор\w*|создать\s+сайт|no-?code|платформ[аеуы]\b|облачн\w+\s+сервис|начать\s+бесплатно|подключить\s+бесплатно", 4),
    "food": ("Еда / рестораны", r"доставка\s+еды|меню|пицц|суши|роллы|ресторан|кафе|бронирование\s+стол", 3),
    "travel": ("Туризм / отели", r"\bбронирован|\bномер\w*\s+(в\s+)?(отел|гостиниц)|\bотел[ьяеию]\w*|\bгостиниц|\bтур(ы|ов)?\b|\bтурагент|\bпутешеств|\bзаселени", 4),
}

# Отрасли, от которых зависят юридические требования (лицензии, обязательные предупреждения).
# Определяются только по тому, как сайт называет себя — заголовок главной и её первые строки. Слова из глубины сайта
# («Карьера: обучение сотрудников», кейсы «CRM для клиники», раздел «Инвесторам») отраслью сайт не делают.
INDUSTRIES: list[tuple[str, str, str]] = [
    # порядок важен: ветеринария раньше медицины («ветеринарная клиника» — не медицина)
    ("veterinary", "Ветеринария", r"ветеринар|зоо\w*\s*(клиник|госпитал)|клиник\w*\s+для\s+животных"),
    ("medical", "Медицина",
     r"стоматологи[яиюей](?![а-яё])|стоматологическ\w+\s+(клиник|центр|кабинет|поликлиник)|(?<![а-яё])клиник|медицинск\w+\s+(центр|клиник|услуг|организац|лаборатор)|медцентр|поликлиник|"
     r"больниц|госпитал|косметолог|(?<![а-яё])врач|гинеколог|офтальмолог|офтальмолог|диагностическ\w+\s+центр|"
     r"лаборатори\w*\s+(анализ|диагност)|центр\s+(здоровья|медицин|реабилитац)|медицин\w+\s+центр"),
    ("education", "Обучение",
     r"автошкол|онлайн-школ|(?<![а-яё])(?<!для )(?<!в )(?<!к )школ[аыу](?![а-яё])|(?<![а-яё])курс[ыов]?(?![а-яё])|обучени|академи|"
     r"образовательн|учебн\w+\s+центр|(?<![а-яё])дпо(?![а-яё])|университет|колледж|техникум|лицей|гимнази|"
     r"детск\w+\s+сад|репетитор|тренинг"),
    ("finance", "Финансы",
     r"банк(?![а-яё])|микрофинанс|(?<![а-яё])мфо(?![а-яё])|микрозайм|займ\w*\s+(онлайн|до\s+зарплат|под\s+залог|на\s+карту)|"
     r"страхов\w+\s+(компани|группа|дом|услуг)|брокер|инвестиционн\w+\s+компани|лизинг|ломбард|"
     r"кредитн\w+\s+(организац|кооператив|карт)|(?<![а-яё])ипотек"),
    ("media", "СМИ / медиа",
     r"новост(?!ро)|журнал|газет|(?<![а-яё])сми(?![а-яё])|медиа|информационн\w+\s+(агентств|портал)|телеканал|радио(?![а-яё])"),
]
REGULATED = {code for code, _, _ in INDUSTRIES}


def detect_industry(site: SiteData) -> dict | None:
    home = site.home
    if not home:
        return None
    # Только то, как сайт называет себя: заголовок страницы и H1. Мета-описание и начало текста не берём —
    # там бывает «CRM для клиник» у IT-компании или меню «Решения → Медицина».
    self_description = f"{home.title}\n{home.h1}"
    for code, name, rx in INDUSTRIES:
        m = re.search(rx, self_description, re.I)
        if m:
            return {"code": code, "name": name, "signal": m.group(0)}
    return None


# Сигналы товаров с ограничениями дистанционной продажи
RESTRICTED = {
    "Алкоголь": r"\b(вин[оа]|водк[аи]|коньяк[аиу]?|виски|ликё?е?р[аы]?|шампанское|пиво|пива|алкоголь)\b",
    "Табак и никотин": r"\b(сигарет\w*|вейп\w*|под-систем\w*|pod-систем\w*|жидкост\w+\s+для\s+(вейп|электрон)\w*|снюс\w*|никотин\w*|табак\w*)\b",
    "Рецептурные лекарства": r"рецептурн\w+\s+(препарат|лекарств)",
}

CART_NEAR_RE = re.compile(r"в\s+корзину|добавить\s+в\s+корзин|купить\s+онлайн|оформить\s+заказ", re.I)
ALCO_PRODUCT_RE = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|градус)|\b\d(?:[.,]\d{1,3})?\s?л\b|\b\d{3,4}\s?мл\b|"
                             r"крепост|выдержк|объ[её]м", re.I)
PRICE_CTX_RE = re.compile(r"\d[\d\s]{0,9}(?:[.,]\d{1,2})?\s?(₽|руб)|в\s+корзину|купить|заказать", re.I)

# Платформы: (название, признак в HTML/скриптах/заголовках, иностранная?)
PLATFORMS: list[tuple[str, str, bool]] = [
    ("Tilda", r"tildacdn\.|tilda\.ws|data-tilda|t-records", False),
    ("1С-Битрикс", r"/bitrix/(js|templates|cache)|bx-core|BX\.setJSList", False),
    ("WordPress", r"wp-content/|wp-includes/|wp-json", False),
    ("Joomla", r"/media/jui/|joomla", False),
    ("Drupal", r"drupal-settings-json|/sites/default/files/", False),
    ("OpenCart", r"catalog/view/theme|route=common/home", False),
    ("MODX", r"modx|/assets/components/", False),
    ("InSales", r"insales\.ru|assets\.insales", False),
    ("Craftum", r"craftum", False),
    ("Nethouse", r"nethouse\.ru", False),
    ("uKit / uCoz", r"ukit\.com|ucoz\.(ru|net)", False),
    ("Taplink", r"taplink\.(cc|ru|at)", False),
    ("Wix", r"wixstatic\.com|static\.parastorage\.com|x-wix-request-id|_wixCIDX", True),
    ("Shopify", r"cdn\.shopify\.com|myshopify\.com|shopify\.theme", True),
    ("Squarespace", r"squarespace\.com|static1\.squarespace", True),
    ("Webflow", r"webflow\.(com|io)|data-wf-page|assets\.website-files\.com", True),
    ("Framer", r"framerusercontent\.com|framer\.(com|website)", True),
    ("Weebly", r"weebly\.com|editmysite\.com", True),
    ("Ecwid", r"app\.ecwid\.com|ecwid\.com/script", True),
    ("Notion", r"notion\.site|notion-static", True),
    ("Readymag", r"readymag\.com", True),
    ("HubSpot CMS", r"hs-sites\.com|hubspotusercontent", True),
    ("GitHub Pages", r"server: github\.com", True),
    ("Netlify", r"server: netlify|x-nf-request-id", True),
    ("Vercel", r"server: vercel|x-vercel-id", True),
]

FORM_KINDS: list[tuple[str, str, str]] = [
    # (код, название, регулярка по тексту формы и кнопке)
    ("login", "Вход", r"\bвойти\b|\bвход\b|log\s?in|sign\s?in|забыли\s+пароль"),
    ("registration", "Регистрация", r"регистрац|зарегистр|создать\s+(аккаунт|профиль)|sign\s?up"),
    ("payment", "Оплата", r"оплатить|номер\s+карты|card\s*number|cvv|cvc"),
    ("order", "Оформление заказа", r"оформить\s+заказ|подтвердить\s+заказ|адрес\s+доставки|checkout|купить\s+в\s+один\s+клик"),
    ("newsletter", "Подписка на рассылку", r"подпис(аться|ка)\s*(на)?\s*(рассылк|новост)?|рассылк|newsletter|subscribe"),
    ("review", "Отзыв / комментарий", r"оставить\s+(отзыв|комментарий)|ваш\s+(отзыв|комментарий)|написать\s+отзыв"),
    ("booking", "Запись / бронирование", r"записаться|запись\s+на|забронировать|бронирован"),
    ("callback", "Заявка / обратный звонок", r"заявк|перезвон|обратный\s+звонок|заказать\s+звонок|консультац|получить\s+(расч|предлож|прайс)"),
    ("question", "Вопрос / обратная связь", r"задать\s+вопрос|ваш\s+вопрос|сообщение|обратная\s+связь|напишите\s+нам|связаться"),
]

# Специальные категории — ТОЛЬКО по ст. 10, 11 152-ФЗ: здоровье, раса и национальность, политические, религиозные и
# философские убеждения, интимная жизнь, судимость, биометрия. Паспорт, СНИЛС, ИНН — обычные ПДн, не специальные.
SPECIAL_RE = re.compile(
    r"диагноз|заболеван|жалоб\w*\s+на\s+(здоровь|самочувств)|состояни\w+\s+здоровья|медицинск\w+\s+(карт|документ|"
    r"заключени|анамнез)|группа\s+крови|аллерги|инвалидност|беременн|хроническ|анамнез|национальност|расов|"
    r"вероисповедан|религиозн|политическ\w+\s+(взгляд|убежден)|судимост|биометр|отпечат\w+\s+пальц",
    re.I,
)
CARD_RE = re.compile(r"cc-?number|card-?number|cardnumber|\bpan\b|номер\s+карты|cvv|cvc", re.I)

REG_NAV_RE = re.compile(r"(регистрация|зарегистрироваться|личный\s+кабинет|войти\s+в\s+(личный\s+)?кабинет|sign\s?up|"
                        r"создать\s+(аккаунт|профиль))$", re.I)

FEATURES: dict[str, tuple[str, str]] = {
    "registration": ("Регистрация и личный кабинет", r"регистрац|зарегистр|личный\s+кабинет|sign\s?up"),
    # Платная подписка с автопродлением (сервисы, онлайн-курсы, доставка по подписке). «Ежемесячная оплата» счетов
    # ЖКХ, связи или кредита подпиской не является.
    "subscription": ("Подписка с автопродлением", r"автопродлен|автоматическ\w+\s+(продлени|продлева|списани)|"
                     r"продлева\w*\s+автоматически|рекуррентн\w+\s+(платеж|списани)"),
    # Только персональные рекомендации (на основе данных о пользователе) — ст. 10.2-2 149-ФЗ;
    # «Похожие товары» и «С этим товаром покупают» не персонализированы и под закон не подпадают
    "recommendations": ("Персональные рекомендации", r"вам\s+может\s+понравиться|персональн\w+\s+рекомендац|рекомендации\s+для\s+вас|"
                        r"подобрали\s+для\s+вас|на\s+основе\s+ваших\s+(просмотров|покупок|интересов)|специально\s+для\s+вас\s+подобрал"),
    "reviews": ("Отзывы пользователей", r"\bотзыв(ы|ов)\b"),
    "online_payment": ("Онлайн-оплата", r"оплатить\s+онлайн|оплата\s+картой\s+на\s+сайте|онлайн-оплат"),
    # БАД как товар: «БАД» рядом с ценой. Упоминание в новостях или у дистрибьютора лекарств — не продажа БАД
    "supplements": ("Продажа БАД", r"(?:\bбад\b|биологически\s+активн\w+\s+добавк)[^\n]{0,120}\d[\d\s]{0,7}\s?(₽|руб)|"
                    r"\d[\d\s]{0,7}\s?(₽|руб)[^\n]{0,120}(?:\bбад\b|биологически\s+активн\w+\s+добавк)"),
}
PAYMENT_HOSTS = {
    "yookassa.ru": "ЮKassa", "yoomoney.ru": "ЮMoney", "cloudpayments.ru": "CloudPayments",
    "tinkoff.ru": "Т-Касса", "tbank.ru": "Т-Касса", "robokassa": "Robokassa", "payanyway.ru": "PayAnyWay",
    "stripe.com": "Stripe (иностр.)", "paypal.com": "PayPal (иностр.)", "paddle.com": "Paddle (иностр.)",
}


def classify_form(f: Form, page_url: str = "") -> str:
    text = f"{f.submit} {f.text[:600]}"
    names = " ".join(f"{i.type} {i.name} {i.placeholder}" for i in f.inputs).lower()
    if any(i.type == "password" for i in f.inputs):
        return "registration" if re.search(FORM_KINDS[1][2], text, re.I) else "login"
    if not form_is_pd(f):
        return "search"
    if is_service_form(f, page_url):
        return "service"
    if CARD_RE.search(names):
        return "payment"
    for code, _, rx in FORM_KINDS:
        if code in ("login", "registration"):
            continue
        if re.search(rx, text, re.I):
            # одна строка e-mail + «подписаться» — рассылка; в иных формах «подписка» вторична
            if code == "newsletter" and len([i for i in f.inputs if input_is_pd(i)]) > 2:
                continue
            return code
    if f.inputs and all(i.type == "search" or re.match(r"^(q|s|search|query)$", i.name or "", re.I) for i in f.inputs):
        return "search"
    return "other" if form_is_pd(f) else "search"


FORM_KIND_NAMES = {code: name for code, name, _ in FORM_KINDS} | {
    "search": "Поиск", "other": "Прочая форма", "service": "Служебная (восстановление пароля, управление подпиской)",
}


def build_profile(site: SiteData) -> dict:
    text = all_text(site)
    titles = " ".join(p.title for p in site.pages)
    hay = f"{titles}\n{text}\n" + " ".join(b for p in site.pages for b in p.buttons)

    # Тип бизнеса определяет то, что сайт говорит о себе на главной, в заголовках и меню.
    # Глубокие страницы (статьи, FAQ, шаблоны) только подтверждают: иначе блог про путешествия
    # или конструктор сайтов с шаблонами «турагентства» превращается в «Туризм».
    home_page = site.home
    primary = "\n".join([home_page.text[:15000] if home_page else "", titles] +
                        [l.text for l in (home_page.links if home_page else [])])
    types = []
    for code, (name, rx, need) in SITE_TYPES.items():
        hits = Counter(re.sub(r"\s+", " ", m.group(0).lower()).strip() for m in re.finditer(rx, hay, re.I))
        primary_hits = {re.sub(r"\s+", " ", m.group(0).lower()).strip() for m in re.finditer(rx, primary, re.I)}
        if len(hits) >= need and len(primary_hits) >= 2:
            types.append({"code": code, "name": name,
                          "score": sum(min(v, 5) for v in hits.values()) + 5 * len(primary_hits),
                          "signals": [k for k, _ in hits.most_common(5)]})
    types.sort(key=lambda t: -t["score"])
    # Регулируемые отрасли в профиле — только если сайт сам так себя называет (см. INDUSTRIES)
    industry = detect_industry(site)
    types = [t for t in types if t["code"] not in REGULATED]
    if industry:
        types.insert(0, {"code": industry["code"], "name": industry["name"], "score": 1000,
                         "signals": [f"заголовок/начало главной: «{industry['signal']}»"]})

    home = site.home
    blob = ""
    if home:
        blob = home.html[:400_000] + " " + " ".join(home.scripts) + " " + home.generator + " " + \
            " ".join(f"{k}: {v}" for k, v in home.headers.items())
    platform = next(({"name": n, "foreign": f} for n, rx, f in PLATFORMS if re.search(rx, blob, re.I)), None)
    if not platform and home and home.generator:
        platform = {"name": home.generator[:60], "foreign": False}

    features = {code: name for code, (name, rx) in FEATURES.items() if code != "registration" and re.search(rx, hay, re.I)}
    # Регистрация — по навигации («Регистрация», «Личный кабинет», «Войти») или полю пароля,
    # а не по любому слову «регистрация» в тексте («адрес регистрации», «регистрация сделок»)
    nav = [l.text for p in site.pages for l in p.links] + [b for p in site.pages for b in p.buttons]
    if any(REG_NAV_RE.match(t.strip()) for t in nav if len(t.strip()) < 40) or any(
        i.type == "password" for p in site.pages for f in p.forms for i in f.inputs
    ):
        features["registration"] = FEATURES["registration"][0]
    payments = sorted({name for u in site.all_request_urls() | {l.href for _, l in site.all_links()}
                       for host, name in PAYMENT_HOSTS.items() if host in u})
    if payments:
        features["online_payment"] = FEATURES["online_payment"][0]
    chat = sorted({s.name for u in site.all_request_urls() if (s := match_service(u)) and s.category == "chat"})

    forms: list[dict] = []
    seen = set()
    for p in site.pages:
        for f in page_forms(p):
            kind = classify_form(f, p.url)
            key = (kind, form_signature(f), tuple(c.label[:60] for c in f.checkboxes))
            if kind == "search" or key in seen:
                continue
            seen.add(key)
            forms.append({
                "kind": kind, "kind_name": FORM_KIND_NAMES.get(kind, kind), "page": p.url,
                "fields": [field_label(i) for i in f.inputs][:8],
                "pd": form_is_pd(f),
                "special": bool(SPECIAL_RE.search(f.text + " " + " ".join(i.name + " " + i.placeholder for i in f.inputs))),
                "card": bool(CARD_RE.search(" ".join(f"{i.name} {i.placeholder} {i.autocomplete}" for i in f.inputs))),
                "service": kind in ("login", "service"),
                "consent_checkbox": any(re.search(r"согла|персональн", c.label, re.I) for c in f.checkboxes),
                "ad_consent_checkbox": any(re.search(r"реклам|рассылк|маркетинг|новост", c.label, re.I) for c in f.checkboxes),
                "screenshot": f.screenshot,
                "popup": f.popup,
            })

    # Товар — только упоминание рядом с ценой или кнопкой покупки (не правила, не статьи о запретах)
    restricted = {}
    for name, rx in RESTRICTED.items():
        found = set()
        for p in site.pages:
            for m in re.finditer(rx, p.text, re.I):
                window = p.text[max(0, m.start() - 150):m.end() + 150]
                before = p.text[max(0, m.start() - 250):m.start()]
                # ингредиент в составе («…ванилин, коньяк.») — не товар
                if re.search(r"состав|ингредиент|пропитк|начинк", before, re.I) or p.text[max(0, m.start() - 2):m.start()] == ", ":
                    continue
                if not PRICE_CTX_RE.search(window) or not CART_NEAR_RE.search(window):
                    continue  # продаётся онлайн — значит, рядом кнопка «В корзину» (у сетей магазинов алкоголь только офлайн)
                if name == "Алкоголь" and not ALCO_PRODUCT_RE.search(window):
                    continue  # у алкогольного товара указывают объём или крепость
                found.add(m.group(0).lower())
        if found:
            restricted[name] = sorted(found)[:5]

    return {
        "industry": industry,
        "types": types[:4],
        "primary_type": types[0]["name"] if types else "Информационный сайт",
        "platform": platform,
        "features": features,
        "payments": payments,
        "chat": chat,
        "forms": forms[:40],
        "restricted": restricted,
        "pages_count": len(site.pages),
        "owner": extract_owner(site),
        "services": _services(site),
        "tracking_cookies": sorted({c["name"] for c in site.initial_cookies if match_tracking_cookie(c["name"])}),
    }



def _services(site: SiteData) -> list[dict]:
    found = {}
    for u in site.all_request_urls():
        sv = match_service(u)
        if sv:
            found[sv.name] = {"name": sv.name, "category": sv.category, "foreign": sv.foreign}
    return sorted(found.values(), key=lambda x: (not x["foreign"], x["category"], x["name"]))


ORG_RE = re.compile(
    r"((?:ООО|АО|ПАО|ЗАО|НАО|ОАО|АНО|Общество\s+с\s+ограниченной\s+ответственностью|Акционерное\s+общество)"
    r"\s*[«\"“„]\s*[^»\"”\n]{2,80}?[»\"”])"
    r"|((?:ИП|Индивидуальный\s+предприниматель)\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ](?:[а-яё]+|\.)){1,2}\.?)",
)
ADDRESS_RE = re.compile(r"(?:юридический\s+адрес|адрес(?:\s+организации)?|местонахождени[ея])\s*[:—-]\s*([^\n]{10,160})", re.I)
PHONE_RE = re.compile(r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]?\d{2}[\s-]?\d{2}")


ADDRESS_END_RE = re.compile(r"\s(?:в\s+порядке|в\s+соответствии|тел[.:\s]|телефон|e-?mail|эл\.|почт|ИНН|ОГРН|КПП|режим|"
                            r"пн|понедельник|ежедневно|график)", re.I)


def _clean_address(raw: str) -> str:
    """Адрес до первого постороннего фрагмента («в порядке…», «тел.», «ИНН»…)."""
    m = ADDRESS_END_RE.search(raw)
    return (raw[:m.start()] if m else raw).strip(" .,;:—-")[:150]


def extract_owner(site: SiteData) -> dict:
    """Реквизиты владельца со страниц сайта — для документов. Ищем рядом с ИНН, затем в подвале главной."""
    from .checks.common import EMAIL_RE, INN_RE, OGRN_RE, digits

    text = all_text(site)
    inn = INN_RE.search(text)
    windows = []
    if inn:
        windows.append(text[max(0, inn.start() - 600):inn.end() + 600])
    if site.home:
        windows.append(site.home.text[-3000:])
    windows.append(text)

    def first(rx, group=0):
        for w in windows:
            m = rx.search(w)
            if m:
                return next((g for g in m.groups() if g), m.group(0)) if m.groups() else m.group(group)
        return ""

    ogrn = OGRN_RE.search(text)
    email = first(EMAIL_RE)
    return {
        "name": re.sub(r"\s+", " ", first(ORG_RE)).strip(),
        "inn": digits(inn.group(1)) if inn else "",
        "ogrn": digits(ogrn.group(2)) if ogrn else "",
        "address": _clean_address(first(ADDRESS_RE)),
        "email": email if not email.lower().endswith((".png", ".jpg", ".svg", ".webp")) else "",
        "phone": re.sub(r"\s+", " ", first(PHONE_RE)).strip(),
    }
