"""Факты для документов — из сохранённого отчёта проверки (без повторного сканирования).

Чего на сайте не нашлось, остаётся полем «[указать …]»: документ не должен утверждать то,
чего сервис не видел.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Подписи полей форм → категории персональных данных
FIELD_CATEGORIES: list[tuple[str, str]] = [
    (r"фамил|surname|last.?name|фио|fio", "фамилия, имя, отчество"),
    (r"(?<![a-zа-яё])(имя|name|first.?name)", "имя"),
    (r"e-?mail|почт|mail", "адрес электронной почты"),
    (r"тел|phone", "номер телефона"),
    (r"адрес|address|город|city|улиц|индекс|postcode|zip", "адрес (для доставки)"),
    (r"рожд|birth|возраст", "дата рождения"),
    (r"паспорт|passport", "паспортные данные"),
    (r"снилс|snils", "СНИЛС"),
    (r"(?<![a-zа-яё])инн(?![a-zа-яё])|(?<![a-z])inn(?![a-z])", "ИНН"),
    (r"компан|организац|company|org", "наименование организации и должность"),
    (r"коммент|сообщен|вопрос|comment|message|text", "сведения, указанные пользователем в сообщении"),
]

PURPOSES: dict[str, tuple[str, str, str]] = {
    # вид формы → (цель, правовое основание, срок)
    "callback": ("обработка заявок и обратная связь с пользователем", "согласие субъекта (п. 1 ч. 1 ст. 6 152-ФЗ)",
                 "до достижения цели обработки, но не более 1 года с момента заявки"),
    "question": ("ответы на обращения и вопросы пользователей", "согласие субъекта (п. 1 ч. 1 ст. 6 152-ФЗ)",
                 "до достижения цели обработки, но не более 1 года с момента обращения"),
    "order": ("заключение и исполнение договора купли-продажи, доставка товаров",
              "исполнение договора, стороной которого является субъект (п. 5 ч. 1 ст. 6 152-ФЗ)",
              "в течение срока действия договора и 5 лет после его исполнения (налоговый и бухгалтерский учёт)"),
    "payment": ("приём оплаты за товары и услуги", "исполнение договора (п. 5 ч. 1 ст. 6 152-ФЗ)",
                "в течение срока действия договора и 5 лет после его исполнения"),
    "registration": ("регистрация на сайте и предоставление доступа к личному кабинету",
                     "исполнение пользовательского соглашения (п. 5 ч. 1 ст. 6 152-ФЗ)",
                     "до удаления учётной записи пользователем или отзыва согласия"),
    "booking": ("запись на приём (бронирование) и напоминания о записи", "согласие субъекта (п. 1 ч. 1 ст. 6 152-ФЗ)",
                "до достижения цели обработки, но не более 1 года"),
    "review": ("публикация отзывов и комментариев", "согласие субъекта (п. 1 ч. 1 ст. 6 152-ФЗ)",
               "до удаления отзыва или отзыва согласия"),
    "newsletter": ("направление информационных и рекламных рассылок",
                   "отдельное согласие субъекта (ст. 9 152-ФЗ, ч. 1 ст. 18 38-ФЗ «О рекламе»)",
                   "до отзыва согласия (отписки от рассылки)"),
    "other": ("обработка обращений, поступивших через формы сайта", "согласие субъекта (п. 1 ч. 1 ст. 6 152-ФЗ)",
              "до достижения цели обработки, но не более 1 года"),
}

CATEGORY_RU = {
    "analytics": "веб-аналитика", "ads": "реклама", "chat": "онлайн-чат / CRM", "captcha": "защита от спама (капча)",
    "fonts": "шрифты", "video": "видео", "maps": "карты", "cdn": "загрузка библиотек", "payment": "приём платежей",
}


def blank(what: str) -> str:
    return f"[указать {what}]"


@dataclass
class Facts:
    site: str
    host: str
    checked_at: str
    platform: str
    operator_name: str
    inn: str
    ogrn: str
    address: str
    email: str
    phone: str
    purposes: list[dict] = field(default_factory=list)  # {kind, purpose, basis, term, categories}
    categories: list[str] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)
    foreign_services: list[str] = field(default_factory=list)
    analytics: list[str] = field(default_factory=list)
    tracking_cookies: list[str] = field(default_factory=list)
    payments: list[str] = field(default_factory=list)
    has_newsletter: bool = False
    has_registration: bool = False
    has_recommendations: bool = False
    special_categories: bool = False
    is_ip: bool = False  # индивидуальный предприниматель

    @property
    def operator_line(self) -> str:
        parts = [self.operator_name, f"ИНН {self.inn}" if self.inn else "", f"ОГРН {self.ogrn}" if self.ogrn else ""]
        return ", ".join(p for p in parts if p)


def categories_for(fields: list[str]) -> list[str]:
    cats: list[str] = []
    for f in fields:
        for rx, cat in FIELD_CATEGORIES:
            if re.search(rx, f, re.I) and cat not in cats:
                cats.append(cat)
                break
    if "фамилия, имя, отчество" in cats and "имя" in cats:
        cats.remove("имя")
    return cats


def from_report(report: dict, url: str, checked_at: str) -> Facts:
    p = report.get("profile") or {}
    owner = p.get("owner") or {}
    name = owner.get("name_official") or owner.get("name") or ""
    host = urlsplit(url).hostname or url

    purposes: dict[str, dict] = {}
    all_cats: list[str] = []
    for f in p.get("forms") or []:
        if not f.get("pd") or f.get("service") or f["kind"] in ("login", "search", "service"):
            continue
        kind = f["kind"] if f["kind"] in PURPOSES else "other"
        cats = categories_for(f.get("fields") or [])
        entry = purposes.setdefault(kind, {"kind": kind, "categories": []})
        for c in cats:
            if c not in entry["categories"]:
                entry["categories"].append(c)
            if c not in all_cats:
                all_cats.append(c)
    if "фамилия, имя, отчество" in all_cats and "имя" in all_cats:
        all_cats.remove("имя")
    if not purposes:  # форм не нашли — оставляем общую цель, чтобы документ не был пустым
        purposes["other"] = {"kind": "other", "categories": []}
    plist = []
    for kind, e in purposes.items():
        purpose, basis, term = PURPOSES[kind]
        plist.append({**e, "purpose": purpose, "basis": basis, "term": term,
                      "categories": e["categories"] or [blank("перечень данных, которые собирает форма")]})

    services = p.get("services") or []
    payments = p.get("payments") or []
    platform = (p.get("platform") or {}).get("name") or ""
    return Facts(
        site=f"https://{host}", host=host, checked_at=checked_at[:10], platform=platform,
        operator_name=name or blank("полное наименование организации или ФИО индивидуального предпринимателя"),
        inn=owner.get("inn") or blank("ИНН"), ogrn=owner.get("ogrn") or blank("ОГРН / ОГРНИП"),
        address=owner.get("address") or blank("адрес"),
        email=owner.get("email") or blank("e-mail для обращений по персональным данным"),
        phone=owner.get("phone") or "",
        purposes=plist, categories=all_cats or [blank("перечень персональных данных")],
        services=services,
        foreign_services=[s["name"] for s in services if s.get("foreign") and s["category"] in
                          ("analytics", "ads", "chat", "captcha", "fonts", "video", "maps")],
        analytics=[s["name"] for s in services if s["category"] in ("analytics", "ads")],
        tracking_cookies=p.get("tracking_cookies") or [],
        payments=payments,
        has_newsletter="newsletter" in purposes,
        has_registration="registration" in (p.get("features") or {}),
        has_recommendations="recommendations" in (p.get("features") or {}),
        special_categories=any(f.get("special") for f in p.get("forms") or []),
        is_ip=name.upper().startswith(("ИП", "ИНДИВИДУАЛЬНЫЙ")),
    )
