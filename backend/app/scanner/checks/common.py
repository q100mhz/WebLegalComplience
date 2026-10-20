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

PD_INPUT_RE = re.compile(
    r"e-?mail|mail|phone|tel|fio|name|surname|имя|фамил|телефон|почт|адрес|address|паспорт|"
    r"birth|рожд|snils|снилс|inn|инн",
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
    return bool(PD_INPUT_RE.search(f"{i.name} {i.placeholder} {i.autocomplete}"))


def form_is_pd(f: Form) -> bool:
    return any(input_is_pd(i) for i in f.inputs)


def form_signature(f: Form) -> str:
    return "|".join(sorted(f"{i.type}:{i.name}" for i in f.inputs)) + "#" + str(len(f.checkboxes))


def unique_pd_forms(site: SiteData) -> list[tuple[PageData, Form]]:
    """Формы с ПДн без дублей (форма в шапке/футере повторяется на каждой странице)."""
    seen: set[str] = set()
    res = []
    for p in site.pages:
        for f in p.forms:
            if not form_is_pd(f):
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
