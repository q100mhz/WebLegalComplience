import re
from urllib.parse import urlsplit

from ..models import Form, Link, PageData, SiteData
from ..urls import clean_link

POLICY_TEXT_RE = re.compile(
    r"политик\w*\s+(в\s+отношении\s+)?(обработки|конфиденциальн|защиты|персональн)|"
    r"конфиденциальност|privacy|обработк\w+\s+персональн|персональн\w+\s+данн",
    re.I,
)
POLICY_HREF_RE = re.compile(
    r"politik|policy|policies|privacy|konfid|confiden|personal[-_]?data|personaln|(^|[/_-])pdn([/_.-]|$)|152-?fz", re.I
)
CONSENT_TEXT_RE = re.compile(r"согласи\w*\s+(на\s+)?(обработк|получени)|consent", re.I)
CONSENT_HREF_RE = re.compile(r"soglas|consent|agreement-pd|personal-data-agree", re.I)

# Слово должно начинаться с маркера: «microphone» и «hotel» — не ПДн, «user_phone» и «firstName» — ПДн
PD_INPUT_RE = re.compile(
    r"(?<![a-zа-яё])(e-?mail|mail|phone|tel|fio|name|surname|имя|фамил|телефон|почт|адрес|address|паспорт|"
    r"birth|рожд|snils|снилс|inn|инн)",
    re.I,
)
SEARCH_RE = re.compile(r"^(q|s|search|query|text|поиск|keyword)s?$", re.I)

INN_RE = re.compile(r"ИНН\D{0,10}(\d{10}|\d{12})(?!\d)", re.I)
OGRN_RE = re.compile(r"ОГРН(ИП)?\D{0,10}(\d{13}|\d{15})(?!\d)", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def link_is_policy(link: Link) -> bool:
    return bool(POLICY_TEXT_RE.search(link.text) or POLICY_HREF_RE.search(urlsplit(link.href).path))


def link_is_consent(link: Link) -> bool:
    return bool(CONSENT_TEXT_RE.search(link.text) or CONSENT_HREF_RE.search(urlsplit(link.href).path))


def policy_links(site: SiteData) -> list[tuple[PageData, Link]]:
    return [(p, l) for p, l in site.all_links() if link_is_policy(l)]


def policy_pages(site: SiteData) -> list[PageData]:
    """Страницы, похожие на саму политику обработки ПДн."""
    res = []
    for p in site.pages[1:] or []:
        head = f"{p.url} {p.title} {p.text[:400]}"
        if (POLICY_TEXT_RE.search(head) or POLICY_HREF_RE.search(urlsplit(p.url).path)) and re.search(
            r"персональн\w+\s+данн", p.text, re.I
        ) and len(p.text) > 1500:
            res.append(p)
    return res


def input_is_pd(i) -> bool:
    if i.type in ("email", "tel"):
        return True
    if i.type == "search" or SEARCH_RE.match(i.name or ""):
        return False
    hay = re.sub(r"([a-z])([A-Z])", r"\1 \2", f"{i.name} {i.placeholder} {i.autocomplete}")
    return bool(PD_INPUT_RE.search(hay))


def form_is_pd(f: Form) -> bool:
    return any(input_is_pd(i) for i in f.inputs)


def form_signature(f: Form) -> str:
    return "|".join(sorted(f"{i.type}:{i.name}" for i in f.inputs)) + "#" + str(len(f.checkboxes))


# Служебные формы для тех, кто уже дал согласие: вход, восстановление пароля, управление подпиской
SERVICE_FORM_RE = re.compile(
    r"забыли\s+пароль|восстанов\w*\s+(парол|доступ)|выслать\s+(новый\s+)?пароль|отписаться|изменить\s+подписку|"
    r"уже\s+подписаны|управлени\w*\s+подписк|forgot|reset\s+password|unsubscribe",
    re.I,
)


SERVICE_URL_RE = re.compile(r"forgot|change_?password|restore|recover|reset|unsubscribe", re.I)
# Кнопка «Подписаться», «Отправить заявку» и т.п. — значит, форма собирает данные новых людей
NEW_DATA_SUBMIT_RE = re.compile(r"подписат|зарегистр|отправить|оставить|заказ|купить|записат|получить|subscribe|sign\s?up|send", re.I)


def is_service_form(f: Form, page_url: str = "") -> bool:
    if any(i.type == "password" for i in f.inputs) and not re.search(r"регистрац|зарегистр|sign\s?up", f.text + f.submit, re.I):
        return True
    if NEW_DATA_SUBMIT_RE.search(f.submit):
        return False
    return bool(SERVICE_FORM_RE.search(f"{f.submit} {f.text[:800]}") or SERVICE_URL_RE.search(page_url))


def field_label(i) -> str:
    return (i.placeholder or "").strip() or (i.name or "").strip() or i.type


def unique_pd_forms(site: SiteData) -> list[tuple[PageData, Form]]:
    """Формы, которые собирают ПДн новых людей, без дублей (форма в футере повторяется на каждой странице).
    Служебные формы (вход, восстановление пароля, управление подпиской) не считаются."""
    seen: set[str] = set()
    res = []
    for p in site.pages:
        for f in p.forms:
            if not form_is_pd(f) or is_service_form(f, p.url):
                continue
            sig = form_signature(f)
            if sig in seen:
                continue
            seen.add(sig)
            res.append((p, f))
    return res


def all_text(site: SiteData) -> str:
    return "\n".join(p.text for p in site.pages)


def short(url: str, n: int = 120) -> str:
    return url if len(url) <= n else url[: n - 1] + "…"


def abs_link(page: PageData, link: Link) -> str:
    return clean_link(page.url, link.href) or link.href


def has_type(site: SiteData, code: str, strong: bool = False) -> bool:
    """Определён ли тип бизнеса в профиле сайта (профиль строит scanner/profile.py).
    strong — тип основной или почти равен основному по весу: для отраслевых требований
    (лицензии и т.п.) мало того, что тема просто упоминается на сайте."""
    types = site.profile.get("types", [])
    t = next((t for t in types if t["code"] == code), None)
    if not t:
        return False
    return not strong or t["score"] >= 0.7 * types[0]["score"]
