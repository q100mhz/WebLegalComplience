"""Проверки, зависящие от профиля сайта: формы по назначению, платформа, отраслевые требования."""

import re

from ..models import Finding, SiteData
from ..profile import has_type
from .common import INN_RE, OGRN_RE, all_text, form_is_pd, link_is_policy, short, unique_pd_forms


def _links_text(site: SiteData) -> str:
    return " ".join(f"{l.text} {l.href}" for _, l in site.all_links()) + " " + " ".join(
        f"{d.text} {d.href}" for d in site.document_links
    )


def check_policy_on_form_pages(site: SiteData) -> Finding | None:
    pages = {p.url: p for p in site.pages if any(form_is_pd(f) for f in p.forms)}
    if not pages or not any(link_is_policy(l) for _, l in site.all_links()):
        return None
    missing = [u for u, p in pages.items() if not any(link_is_policy(l) for l in p.links)]
    base = dict(id="policy_on_form_pages", category="Персональные данные",
                title="Ссылка на политику на страницах с формами", law="152-ФЗ, ч. 2 ст. 18.1")
    if not missing:
        return Finding(**base, status="pass", severity="low",
                       summary="На всех страницах с формами сбора ПДн есть ссылка на политику.")
    return Finding(
        **base, status="fail", severity="medium", evidence=[short(u) for u in missing[:10]],
        summary=f"На {len(missing)} стр. с формами сбора ПДн нет ссылки на политику обработки ПДн.",
        recommendation="Политика должна быть доступна на каждой странице, где собираются данные: добавьте ссылку "
        "в футер и рядом с кнопкой отправки формы.",
    )


def check_newsletter_consent(site: SiteData) -> Finding | None:
    forms = [f for f in site.profile.get("forms", []) if f["kind"] == "newsletter"]
    combined = [
        (p.url, c.label) for p in site.pages for f in p.forms for c in f.checkboxes
        if re.search(r"персональн", c.label, re.I) and re.search(r"реклам|рассылк|маркетинг", c.label, re.I)
    ]
    if not forms and not combined:
        return None
    base = dict(id="newsletter_consent", category="Реклама", title="Согласие на получение рекламных рассылок",
                law="38-ФЗ, ч. 1 ст. 18")
    no_consent = [f for f in forms if not f["ad_consent_checkbox"]]
    if no_consent:
        return Finding(
            **base, status="fail", severity="medium",
            evidence=[f"{short(f['page'], 90)} — поля: {', '.join(f['fields'])}" for f in no_consent[:8]],
            summary=f"Форм подписки на рассылку без согласия на получение рекламы: {len(no_consent)}.",
            recommendation="Добавьте отдельный необязательный чекбокс «Согласен получать рекламные и информационные "
            "рассылки». Без такого согласия рассылка считается распространённой без согласия адресата.",
        )
    if combined:
        return Finding(
            **base, status="warn", severity="medium", evidence=[f"{short(u, 80)}: «{l[:100]}»" for u, l in combined[:5]],
            summary="Согласие на рекламу объединено с согласием на обработку ПДн в одном чекбоксе.",
            recommendation="Разделите согласия: обязательный чекбокс на обработку ПДн и отдельный необязательный — на рекламу.",
        )
    return Finding(**base, status="pass", severity="low", summary="Формы подписки содержат отдельное согласие на рекламу.")


def check_special_categories(site: SiteData) -> Finding | None:
    forms = [f for f in site.profile.get("forms", []) if f["special"]]
    if not forms:
        return None
    return Finding(
        id="special_categories", category="Персональные данные",
        title="Сбор чувствительных данных (здоровье, паспорт, СНИЛС)", law="152-ФЗ, ч. 4 ст. 9, ст. 10, 11",
        status="warn", severity="high",
        evidence=[f"{short(f['page'], 90)} — {f['kind_name']}: {', '.join(f['fields'])}" for f in forms[:8]],
        summary="Формы запрашивают сведения о здоровье или документы. Для специальных категорий ПДн требуется "
        "согласие в письменной форме и повышенный уровень защиты.",
        recommendation="Не собирайте такие данные через простую форму; используйте защищённый личный кабинет, "
        "письменное (или ЭП) согласие и укажите эти категории в политике и уведомлении РКН.",
    )


def check_payment_cards(site: SiteData) -> Finding | None:
    forms = [f for f in site.profile.get("forms", []) if f["card"]]
    if not forms:
        return None
    return Finding(
        id="payment_cards", category="Безопасность", title="Ввод данных банковской карты на сайте",
        law="152-ФЗ, ст. 19; PCI DSS", status="warn", severity="medium",
        evidence=[short(f["page"]) for f in forms[:5]],
        summary="Реквизиты карты вводятся в форму на самом сайте, а не на странице платёжного провайдера.",
        recommendation="Используйте платёжную форму или виджет эквайера (ЮKassa, CloudPayments, Т-Касса): "
        "данные карты не должны проходить через ваш сервер.",
    )


def check_foreign_platform(site: SiteData) -> Finding | None:
    platform = site.profile.get("platform")
    base = dict(id="foreign_platform", category="Персональные данные", title="Платформа сайта",
                law="152-ФЗ, ч. 5 ст. 18 (локализация)")
    if not platform:
        return None
    if not platform["foreign"]:
        return Finding(**base, status="pass", severity="low", evidence=[platform["name"]],
                       summary=f"Сайт работает на платформе {platform['name']}.")
    pd_forms = unique_pd_forms(site)
    return Finding(
        **base, status="fail" if pd_forms else "warn", severity="high" if pd_forms else "medium",
        evidence=[platform["name"]],
        summary=f"Сайт размещён на иностранной платформе {platform['name']}. "
        + ("Данные из форм сохраняются в базах за пределами РФ — это нарушение требования о локализации."
           if pd_forms else "Если на сайте появятся формы или регистрация, данные будут храниться за рубежом."),
        recommendation="Перенесите сайт на российский хостинг или конструктор (Tilda, Craftum, InSales, 1С-Битрикс и др.) "
        "либо собирайте заявки через российский сервис с хранением данных в РФ.",
    )


def _inn_ok(inn: str) -> bool:
    d = [int(c) for c in inn]
    def ctrl(coefs):
        return sum(c * x for c, x in zip(coefs, d)) % 11 % 10
    if len(d) == 10:
        return ctrl([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(d) == 12:
        return ctrl([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10] and ctrl([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11]
    return False


def _ogrn_ok(ogrn: str) -> bool:
    if len(ogrn) == 13:
        return int(ogrn[:12]) % 11 % 10 == int(ogrn[12])
    if len(ogrn) == 15:
        return int(ogrn[:14]) % 13 % 10 == int(ogrn[14])
    return False


def check_inn_valid(site: SiteData) -> Finding | None:
    text = all_text(site)
    inns = sorted({m.group(1) for m in INN_RE.finditer(text)})
    ogrns = sorted({m.group(2) for m in OGRN_RE.finditer(text)})
    if not inns and not ogrns:
        return None
    bad = [f"ИНН {x}" for x in inns if not _inn_ok(x)] + [f"ОГРН {x}" for x in ogrns if not _ogrn_ok(x)]
    base = dict(id="inn_valid", category="Информация о владельце", title="Корректность ИНН и ОГРН",
                law="ЗоЗПП, ст. 9; 149-ФЗ, ч. 2 ст. 10")
    if bad:
        return Finding(**base, status="fail", severity="medium", evidence=bad[:5],
                       summary="Реквизиты не проходят проверку контрольной суммы — вероятна опечатка.",
                       recommendation="Сверьте ИНН и ОГРН с выпиской ЕГРЮЛ/ЕГРИП и исправьте на сайте.")
    return Finding(**base, status="pass", severity="low", evidence=[f"ИНН {x}" for x in inns[:3]] + [f"ОГРН {x}" for x in ogrns[:3]],
                   summary="ИНН и ОГРН указаны корректно (контрольные суммы сходятся).")


def check_restricted_goods(site: SiteData) -> Finding | None:
    restricted = site.profile.get("restricted") or {}
    if not restricted or not has_type(site, "shop"):
        return None
    return Finding(
        id="restricted_goods", category="Отраслевые требования", title="Товары с запретом дистанционной продажи",
        law="171-ФЗ, ст. 16; 15-ФЗ, ст. 19", status="warn", severity="high",
        evidence=[f"{k}: {', '.join(v)}" for k, v in restricted.items()],
        summary="Сайт похож на интернет-магазин, и на нём упоминаются: " + ", ".join(restricted) + ". "
        "Дистанционная продажа алкоголя, табачной и никотинсодержащей продукции в РФ запрещена "
        "(за исключениями, прямо установленными законом).",
        recommendation="Убедитесь, что такие товары не продаются через сайт: допускается только информирование без "
        "возможности заказа и оплаты онлайн.",
    )


def check_education(site: SiteData) -> Finding | None:
    if not has_type(site, "education"):
        return None
    hay = all_text(site) + " " + _links_text(site)
    base = dict(id="education_info", category="Отраслевые требования", title="Образовательная деятельность",
                law="273-ФЗ, ст. 29, 91; приказ Рособрнадзора № 1493")
    if re.search(r"сведения\s+об\s+образовательной\s+организаци|sveden", hay, re.I):
        return Finding(**base, status="pass", severity="low",
                       summary="Найден раздел «Сведения об образовательной организации».")
    if re.search(r"лицензи\w*\s+(на\s+)?(осуществление\s+)?образовательн", hay, re.I):
        return Finding(**base, status="warn", severity="low",
                       summary="Указана образовательная лицензия, но нет раздела «Сведения об образовательной организации».",
                       recommendation="Лицензиаты обязаны вести специальный раздел сайта по утверждённой структуре.")
    if re.search(r"не\s+является\s+образовательн|информационно-консультационн", hay, re.I):
        return Finding(**base, status="info", severity="low",
                       summary="Сайт указывает, что услуги не являются образовательной деятельностью.")
    return Finding(
        **base, status="warn", severity="medium",
        summary="Сайт предлагает обучение, но не найдены ни сведения о лицензии, ни оговорка о характере услуг.",
        recommendation="Если выдаются документы об образовании или квалификации — нужна лицензия и раздел "
        "«Сведения об образовательной организации». Если нет — укажите в оферте, что услуги являются "
        "информационно-консультационными и документы об образовании не выдаются.",
    )


def check_medical(site: SiteData) -> list[Finding]:
    if not has_type(site, "medical"):
        return []
    text = all_text(site)
    res = []
    licensed = re.search(r"лицензи\w*.{0,40}(№|n\b|номер)?\s*[лl]?[0о]?\d{2,}|Л0\d{2}-", text, re.I)
    res.append(Finding(
        id="medical_info", category="Отраслевые требования", title="Сведения о медицинской лицензии",
        law="323-ФЗ, ст. 79; ПП РФ № 736",
        status="pass" if licensed else "fail", severity="low" if licensed else "medium",
        summary="Сведения о медицинской лицензии найдены." if licensed else
        "Сайт оказывает медицинские услуги, но номер лицензии не найден.",
        recommendation="" if licensed else "Разместите номер и дату лицензии, перечень работ (услуг), сведения о "
        "врачах и порядок оказания платных услуг.",
    ))
    warned = re.search(r"противопоказани.{0,80}(консультац|специалист)", text, re.I | re.S)
    res.append(Finding(
        id="medical_ads", category="Реклама", title="Предупреждение о противопоказаниях", law="38-ФЗ, ч. 7 ст. 24",
        status="pass" if warned else "warn", severity="low" if warned else "medium",
        summary="Предупреждение о противопоказаниях размещено." if warned else
        "Не найдено предупреждение «Имеются противопоказания. Необходима консультация специалиста».",
        recommendation="" if warned else "Добавьте предупреждение в рекламные блоки и описание услуг "
        "(не менее 5% площади рекламы).",
    ))
    return res


def check_supplements(site: SiteData) -> Finding | None:
    if "supplements" not in site.profile.get("features", {}):
        return None
    ok = re.search(r"не\s+является\s+лекарственн", all_text(site), re.I)
    return Finding(
        id="supplements_ads", category="Реклама", title="Реклама БАД", law="38-ФЗ, ст. 25",
        status="pass" if ok else "warn", severity="low" if ok else "medium",
        summary="Пометка «не является лекарственным средством» найдена." if ok else
        "На сайте есть БАД, но не найдена пометка «Не является лекарственным средством».",
        recommendation="" if ok else "Добавьте пометку к описанию и рекламе каждого БАД.",
    )


def check_finance(site: SiteData) -> Finding | None:
    if not has_type(site, "finance"):
        return None
    text = all_text(site)
    missing = []
    if re.search(r"кредит|займ", text, re.I) and not re.search(r"полн\w+\s+стоимост\w+\s+(кредит|займ)|\bпск\b", text, re.I):
        missing.append("полная стоимость кредита (займа)")
    if not re.search(r"лицензи|банк\s+россии|реестр\w*\s+(мфо|микрофинанс|цб)|цб\s+рф", text, re.I):
        missing.append("сведения о лицензии / записи в реестре Банка России")
    base = dict(id="finance_info", category="Реклама", title="Обязательная информация о финансовых услугах",
                law="38-ФЗ, ст. 28; 353-ФЗ, ст. 6")
    if not missing:
        return Finding(**base, status="pass", severity="low", summary="Обязательная информация о финансовой услуге найдена.")
    return Finding(**base, status="warn", severity="medium", summary="Не найдено: " + "; ".join(missing) + ".",
                   recommendation="Укажите наименование лица, оказывающего услугу, сведения о лицензии и все условия, "
                   "влияющие на стоимость (ставку, ПСК).")


def check_recommendation_tech(site: SiteData) -> Finding | None:
    if "recommendations" not in site.profile.get("features", {}):
        return None
    ok = re.search(r"рекомендательн\w+\s+технолог", all_text(site) + " " + _links_text(site), re.I)
    return Finding(
        id="recommendation_tech", category="Информация и технологии", title="Правила рекомендательных технологий",
        law="149-ФЗ, ст. 10.2-2", status="pass" if ok else "warn", severity="low" if ok else "medium",
        summary="Правила применения рекомендательных технологий опубликованы." if ok else
        "Сайт показывает персональные рекомендации, но правила применения рекомендательных технологий не найдены.",
        recommendation="" if ok else "Опубликуйте правила: какие данные используются, как формируются рекомендации, "
        "контакты владельца. Ссылка должна быть доступна со страниц с рекомендациями.",
    )


def check_user_terms(site: SiteData) -> Finding | None:
    if "registration" not in site.profile.get("features", {}):
        return None
    ok = re.search(r"пользовательск\w+\s+соглашени|условия\s+(использования|пользования)|правила\s+сервиса|terms|оферт|oferta",
                   _links_text(site), re.I)
    return Finding(
        id="user_terms", category="Защита прав потребителей", title="Пользовательское соглашение",
        law="ГК РФ, ст. 428, 437", status="pass" if ok else "warn", severity="low",
        summary="Условия использования сервиса опубликованы." if ok else
        "На сайте есть регистрация, но не найдено пользовательское соглашение или оферта.",
        recommendation="" if ok else "Опубликуйте пользовательское соглашение и давайте на него ссылку при регистрации.",
    )


def check_subscription(site: SiteData) -> Finding | None:
    if "subscription" not in site.profile.get("features", {}):
        return None
    ok = re.search(r"отказ\w*\s+от\s+подписк|отмен\w+\s+подписк|отключ\w+\s+автопродлен|отвяза\w+\s+карт|отключ\w+\s+автоплат",
                   all_text(site), re.I)
    return Finding(
        id="subscription_terms", category="Защита прав потребителей", title="Условия подписки и автоплатежей",
        law="ЗоЗПП, ст. 10, 16, 32", status="pass" if ok else "warn", severity="low" if ok else "medium",
        summary="Описан порядок отказа от подписки / автоплатежа." if ok else
        "Есть подписка или автоплатёж, но не описано, как от них отказаться.",
        recommendation="" if ok else "Опишите стоимость, периодичность списаний и простой способ отказа "
        "(кнопка в личном кабинете, отвязка карты).",
    )
