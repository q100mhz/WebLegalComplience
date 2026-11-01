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


POLICY_SECTIONS_RE = [re.compile(rx, re.I) for rx in (
    r"цел\w*\s+(обработки|сбора)", r"правов\w*\s+основани", r"категори\w*\s+субъект", r"срок\w*\s+(обработки|хранения)",
    r"уничтожени", r"прав\w*\s+субъект", r"трансграничн", r"оператор\w*\s+(обязан|вправе|осуществляет)",
    r"152-ФЗ|О\s+персональных\s+данных",
)]


def policy_score(p: PageData) -> int:
    """Насколько страница похожа на саму политику обработки ПДн — по содержанию, а не по заголовку:
    на многих сайтах у оферты, cookie-уведомления и политики одинаковый заголовок."""
    mentions = len(re.findall(r"персональн\w+\s+данн", p.text, re.I))
    sections = sum(1 for rx in POLICY_SECTIONS_RE if rx.search(p.text))
    named = 3 if (POLICY_TEXT_RE.search(f"{p.title} {p.text[:400]}") or POLICY_HREF_RE.search(urlsplit(p.url).path)) else 0
    return sections * 10 + min(mentions, 30) + named


def policy_pages(site: SiteData) -> list[PageData]:
    """Страницы, похожие на политику обработки ПДн, — лучшая первой.
    Кандидаты: страницы, на которые ведёт ссылка «Политика…» (даже слабые — их недостатки и нужно показать),
    и страницы, похожие на политику по содержанию. Выбор — по содержанию: заголовок у оферты и политики бывает один."""
    linked = {abs_link(p, l).split("#")[0].rstrip("/") for p, l in site.all_links() if link_is_policy(l)}
    cands = []
    for p in site.pages[1:] or []:
        if len(p.text) < 800:
            continue
        is_target = p.url.split("#")[0].rstrip("/") in linked
        score = policy_score(p) + (15 if is_target else 0)
        if (is_target and re.search(r"персональн\w+\s+данн|конфиденциальн|privacy", p.text, re.I)) or (
            score >= 25 and re.search(r"персональн\w+\s+данн", p.text, re.I)
        ):
            cands.append((score, p))
    return [p for _, p in sorted(cands, key=lambda x: -x[0])]


def input_is_pd(i) -> bool:
    if i.type in ("email", "tel"):
        return True
    if i.type == "search" or SEARCH_RE.match(i.name or ""):
        return False
    hay = re.sub(r"([a-z])([A-Z])", r"\1 \2", f"{i.name} {i.placeholder} {i.autocomplete}")
    return bool(PD_INPUT_RE.search(hay))


def form_is_pd(f: Form) -> bool:
    # одно поле без кнопки отправки (не e-mail и не телефон) — это поиск или проверка адреса доставки, а не сбор данных
    if len(f.inputs) == 1 and not f.submit.strip() and f.inputs[0].type not in ("email", "tel"):
        return False
    return any(input_is_pd(i) for i in f.inputs)


def form_signature(f: Form) -> str:
    return "|".join(sorted(f"{i.type}:{i.name}" for i in f.inputs)) + "#" + str(len(f.checkboxes))


# Служебные формы для тех, кто уже дал согласие: вход, восстановление пароля, управление подпиской
SERVICE_FORM_RE = re.compile(
    r"забыли\s+пароль|восстанов\w*\s+(парол|доступ)|выслать\s+(новый\s+)?пароль|отписаться|изменить\s+подписку|"
    r"уже\s+подписаны|управлени\w*\s+подписк|forgot|reset\s+password|unsubscribe",
    re.I,
)


SERVICE_URL_RE = re.compile(r"forgot|change_?password|restore|recover|reset|unsubscribe|/login|signin|/auth(?!.*regist)", re.I)
# Кнопка «Подписаться», «Отправить заявку» и т.п. — значит, форма собирает данные новых людей
NEW_DATA_SUBMIT_RE = re.compile(r"подписат|зарегистр|регистрац|отправить|оставить|заказ|купить|записат|получить|subscribe|sign\s?up|send", re.I)


def is_service_form(f: Form, page_url: str = "") -> bool:
    if any(i.type == "password" for i in f.inputs) and not re.search(r"регистрац|зарегистр|sign\s?up", f.text + f.submit, re.I):
        return True
    if NEW_DATA_SUBMIT_RE.search(f.submit):
        return False
    return bool(SERVICE_FORM_RE.search(f"{f.submit} {f.text[:800]}") or SERVICE_URL_RE.search(page_url))


def field_label(i) -> str:
    return (i.placeholder or "").strip() or (i.name or "").strip() or i.type


def page_forms(page: PageData) -> list[Form]:
    """Формы страницы без «обёрток»: когда поля не обёрнуты в <form>, сканер видит и сами формы, и общий блок
    вокруг нескольких форм. Блок, содержащий все поля другой формы и что-то сверх того, — обёртка, а не форма."""
    def names(f: Form) -> frozenset:
        return frozenset(f"{i.type}:{i.name or i.placeholder}" for i in f.inputs)
    sets = [names(f) for f in page.forms]
    out, seen = [], set()
    for f, n in zip(page.forms, sets):
        if any(other < n for other in sets if other):  # строгое подмножество — f объемлет другую форму
            continue
        sig = form_signature(f)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(f)
    return out


def unique_pd_forms(site: SiteData) -> list[tuple[PageData, Form]]:
    """Формы, которые собирают ПДн новых людей, без дублей (форма в футере повторяется на каждой странице).
    Служебные формы (вход, восстановление пароля, управление подпиской) не считаются."""
    seen: set[str] = set()
    res = []
    for p in site.pages:
        for f in page_forms(p):
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
