"""Проверки по 152-ФЗ «О персональных данных»."""

import re
from collections import defaultdict

from ..models import Finding, SiteData
from ..services_db import match_service, match_tracking_cookie
from .common import (
    CONSENT_TEXT_RE,
    INN_RE,
    abs_link,
    link_is_consent,
    link_is_policy,
    policy_links,
    policy_pages,
    short,
    unique_pd_forms,
)

CAT = "Персональные данные"


def check_privacy_policy(site: SiteData) -> Finding:
    links = policy_links(site)
    pages = policy_pages(site)
    docs = [d for d in site.document_links if link_is_policy(d)]
    base = dict(
        id="privacy_policy", category=CAT, title="Политика обработки персональных данных",
        law="152-ФЗ, ч. 2 ст. 18.1",
    )
    if not links and not pages and not docs:
        return Finding(
            **base, status="fail", severity="high",
            summary="На сайте не найдена политика обработки персональных данных.",
            recommendation="Разместите политику в отношении обработки ПДн и дайте на неё ссылку в футере "
            "каждой страницы и рядом с каждой формой сбора данных.",
        )
    evidence = list(dict.fromkeys(
        [short(u) for p, l in links if (u := abs_link(p, l)).startswith("http")] + [d.href for d in docs]
    ))[:10]
    home = site.home
    if home and not any(link_is_policy(l) for l in home.links):
        return Finding(
            **base, status="warn", severity="medium", evidence=evidence,
            summary="Политика найдена, но ссылки на неё нет на главной странице.",
            recommendation="Закон требует обеспечить неограниченный доступ к политике — добавьте ссылку в футер.",
        )
    return Finding(
        **base, status="pass", severity="low", evidence=evidence,
        summary="Политика обработки персональных данных опубликована и доступна с главной страницы.",
    )


# Обязательные элементы политики (152-ФЗ, рекомендации РКН)
POLICY_ELEMENTS = {
    "сведения об операторе (наименование, ИНН/ОГРН, адрес)": r"оператор|ИНН|ОГРН",
    "цели обработки": r"цел\w*\s+(обработки|сбора)",
    "правовые основания обработки": r"правов\w*\s+основани",
    "перечень (категории) обрабатываемых ПДн": r"(перечень|категори|состав|объ[её]м)\w*.{0,60}персональн",
    "категории субъектов ПДн": r"категори\w*\s+субъект|субъект\w*\s+персональн",
    "перечень действий и способы обработки": r"способ\w*\s+обработки|перечень\s+действий|сбор.{0,40}запис.{0,40}систематизац",
    "сроки обработки и хранения": r"срок\w*\s+(обработки|хранения)",
    "порядок уничтожения ПДн": r"уничтожени",
    "права субъекта ПДн": r"прав\w*\s+субъект|субъект\w*.{0,40}вправе|имеет\s+право",
    "меры по защите ПДн": r"мер\w*.{0,60}(защит|безопасност)",
}


def check_policy_content(site: SiteData) -> Finding | None:
    pages = policy_pages(site)
    if not pages:
        if policy_links(site) or any(link_is_policy(d) for d in site.document_links):
            return Finding(
                id="policy_content", category=CAT, title="Содержание политики обработки ПДн",
                status="info", severity="low", law="152-ФЗ, ст. 18.1",
                summary="Политика размещена в виде файла или не открылась — содержание не проанализировано.",
                recommendation="Рекомендуем публиковать политику HTML-страницей: её проще индексировать и читать.",
            )
        return None
    text = max(pages, key=lambda p: len(p.text)).text
    missing = [name for name, rx in POLICY_ELEMENTS.items() if not re.search(rx, text, re.I | re.S)]
    if any(match_service(u) and match_service(u).foreign for u in site.all_request_urls()):
        if not re.search(r"трансграничн", text, re.I):
            missing.append("условия трансграничной передачи (на сайте есть иностранные сервисы)")
    if not re.search(r"cookie|куки", text, re.I):
        missing.append("сведения об использовании cookies и метрических программ")
    analytics = sorted({s.name for u in site.all_request_urls() if (s := match_service(u)) and s.category == "analytics"})
    unnamed = [a for a in analytics if not re.search(re.escape(a.split()[0].split("/")[0]), text, re.I)]
    if unnamed:
        missing.append("упоминание используемых сервисов аналитики: " + ", ".join(unnamed))
    base = dict(
        id="policy_content", category=CAT, title="Содержание политики обработки ПДн",
        law="152-ФЗ, ст. 14, 18.1; рекомендации РКН", evidence=[pages[0].url],
    )
    if not missing:
        return Finding(**base, status="pass", severity="low", summary="Политика содержит все основные обязательные разделы.")
    return Finding(
        **base,
        status="fail" if len(missing) >= 4 else "warn",
        severity="medium",
        summary=f"В политике не найдено разделов: {len(missing)}.",
        recommendation="Дополните политику: " + "; ".join(missing) + ".",
       
    )


IMPLIED_RE = re.compile(r"(нажима|отправля|оставля|заполня)\w*.{0,80}(соглаша|согласи|принима|подтвержда)", re.I | re.S)


def check_forms_consent(site: SiteData) -> Finding:
    forms = unique_pd_forms(site)
    base = dict(id="forms_consent", category=CAT, title="Согласие на обработку ПДн в формах", law="152-ФЗ, ст. 9")
    if not forms:
        return Finding(**base, status="info", severity="low", summary="Формы сбора персональных данных не обнаружены.")
    bad: dict[str, list[str]] = defaultdict(list)
    for page, f in forms:
        consent_boxes = [c for c in f.checkboxes if re.search(r"согла|персональн|политик|обработк|конфиденц", c.label, re.I)]
        fields = ", ".join(i.placeholder or i.name or i.type for i in f.inputs[:4])
        where = f"{short(page.url, 90)} — поля: {fields}"
        if any(c.checked for c in consent_boxes):
            bad["prechecked"].append(where)
        elif consent_boxes:
            if not any(link_is_policy(l) or link_is_consent(l) for l in f.links):
                bad["no_link"].append(where)
        elif IMPLIED_RE.search(f.text) or CONSENT_TEXT_RE.search(f.text):
            bad["implied"].append(where)
        else:
            bad["none"].append(where)

    if bad["none"] or bad["prechecked"]:
        parts = []
        if bad["none"]:
            parts.append(f"без какого-либо согласия: {len(bad['none'])}")
        if bad["prechecked"]:
            parts.append(f"с заранее проставленной галочкой: {len(bad['prechecked'])}")
        return Finding(
            **base, status="fail", severity="high",
            summary=f"Найдено форм с ПДн: {len(forms)}; из них {', '.join(parts)}.",
            recommendation="Добавьте в каждую форму обязательный, не отмеченный заранее чекбокс "
            "«Я даю согласие на обработку персональных данных» со ссылкой на текст согласия и политику.",
            evidence=(bad["none"] + bad["prechecked"])[:10],
        )
    if bad["implied"] or bad["no_link"]:
        return Finding(
            **base, status="warn", severity="medium",
            summary="Согласие оформлено формулировкой «нажимая кнопку, вы соглашаетесь…» или без ссылки на документ — "
            "такое согласие сложно признать конкретным, информированным и сознательным.",
            recommendation="Замените пассивную формулировку на отдельный чекбокс со ссылкой на текст согласия.",
            evidence=(bad["implied"] + bad["no_link"])[:10],
        )
    return Finding(**base, status="pass", severity="low", summary=f"Все формы с ПДн ({len(forms)}) содержат чекбокс согласия.")


def check_separate_consent(site: SiteData) -> Finding | None:
    if not unique_pd_forms(site):
        return None
    consent_links = [(p, l) for p, l in site.all_links() if link_is_consent(l)]
    consent_links += [(None, d) for d in site.document_links if link_is_consent(d)]
    base = dict(
        id="separate_consent", category=CAT, title="Согласие оформлено отдельным документом",
        law="152-ФЗ, ч. 1 ст. 9 (ред. 156-ФЗ, с 01.09.2025)",
    )
    if not consent_links:
        return Finding(
            **base, status="fail", severity="medium",
            summary="Не найден отдельный текст согласия на обработку ПДн. С 1 сентября 2025 года согласие должно "
            "оформляться отдельно от политики, оферты и иных документов.",
            recommendation="Опубликуйте отдельную страницу «Согласие на обработку персональных данных» "
            "(цели, перечень данных, действия, срок, порядок отзыва) и ссылайтесь на неё из чекбокса в формах.",
        )
    return Finding(
        **base, status="pass", severity="low",
        summary="Найден отдельный документ согласия на обработку ПДн.",
        evidence=list(dict.fromkeys(abs_link(p, l) if p else l.href for p, l in consent_links))[:5],
    )


COOKIE_BANNER_RE = re.compile(r"(cookie|куки)", re.I)


def check_cookies(site: SiteData) -> Finding:
    trackers: dict[str, set[str]] = defaultdict(set)
    for c in site.initial_cookies:
        svc = match_tracking_cookie(c["name"])
        if svc:
            trackers[svc].add(f"cookie {c['name']}")
    for u in site.initial_request_urls:
        svc = match_service(u)
        if svc and svc.category in ("analytics", "ads"):
            trackers[svc.name].add("скрипт загружен")
    home_text = site.home.text if site.home else ""
    banner = bool(COOKIE_BANNER_RE.search(home_text))
    base = dict(
        id="cookies", category=CAT, title="Cookies и метрические программы",
        law="152-ФЗ, ст. 6, 9; позиция РКН по cookies как ПДн",
    )
    evidence = [f"{name}: {', '.join(sorted(v))}" for name, v in trackers.items()]
    if not trackers:
        return Finding(**base, status="pass", severity="low",
                       summary="При первом заходе аналитические и рекламные cookies не устанавливаются.")
    if not banner:
        return Finding(
            **base, status="fail", severity="medium", evidence=evidence,
            summary=f"Сайт сразу запускает трекеры ({len(trackers)}) и не сообщает пользователю об использовании cookies.",
            recommendation="Добавьте cookie-баннер с возможностью согласиться или отказаться и загружайте метрики "
            "только после согласия. Опишите cookies в политике обработки ПДн.",
        )
    return Finding(
        **base, status="warn", severity="low", evidence=evidence,
        summary="Есть уведомление о cookies, но трекеры запускаются до того, как пользователь дал согласие.",
        recommendation="Загружайте аналитику и рекламные пиксели только после нажатия «Принять» в cookie-баннере.",
    )


HIGH_RISK = {"analytics", "ads", "chat", "captcha"}
CAT_RU = {
    "analytics": "аналитика", "ads": "реклама", "chat": "чат/CRM", "captcha": "капча",
    "fonts": "шрифты", "video": "видео", "maps": "карты", "cdn": "CDN",
}


def check_foreign_services(site: SiteData) -> Finding:
    found: dict[str, str] = {}
    for u in site.all_request_urls():
        svc = match_service(u)
        if svc and svc.foreign:
            found[svc.name] = svc.category
    base = dict(
        id="foreign_services", category=CAT, title="Иностранные сервисы и трансграничная передача",
        law="152-ФЗ, ст. 12 (трансграничная передача), ч. 5 ст. 18 (локализация)",
    )
    if not found:
        return Finding(**base, status="pass", severity="low", summary="Иностранные сервисы, собирающие данные, не обнаружены.")
    evidence = [f"{n} ({CAT_RU.get(c, c)})" for n, c in sorted(found.items(), key=lambda x: (x[1] not in HIGH_RISK, x[0]))]
    high = [n for n, c in found.items() if c in HIGH_RISK]
    if high:
        return Finding(
            **base, status="fail", severity="high", evidence=evidence,
            summary=f"Данные посетителей передаются иностранным сервисам: {', '.join(sorted(high))}. "
            "Это трансграничная передача ПДн, а первичный сбор данных идёт не в российские базы.",
            recommendation="Замените сервисы на российские аналоги (Яндекс Метрика, Yandex SmartCaptcha, Jivo и др.) "
            "или подайте в РКН уведомление о трансграничной передаче и обеспечьте локализацию первичной записи.",
        )
    base.update(id="foreign_resources", title="Иностранные ресурсы (шрифты, CDN, видео)")
    return Finding(
        **base, status="warn", severity="low", evidence=evidence,
        summary="Подключены иностранные ресурсы (шрифты, CDN, видео), которым передаётся IP-адрес посетителя.",
        recommendation="Разместите шрифты и библиотеки на своём сервере, видео — на российских платформах.",
    )


def check_hosting(site: SiteData) -> Finding:
    base = dict(id="hosting", category=CAT, title="Расположение сервера", law="152-ФЗ, ч. 5 ст. 18 (локализация)")
    ev = [f"IP {site.server_ip}, страна: {site.server_country or 'не определена'}"]
    if not site.server_country:
        return Finding(**base, status="info", severity="low", evidence=ev, summary="Не удалось определить страну сервера.")
    if site.server_country.upper() == "RU":
        return Finding(**base, status="pass", severity="low", evidence=ev, summary="Сервер сайта находится в России.")
    return Finding(
        **base, status="warn", severity="high", evidence=ev,
        summary=f"Сервер сайта находится за пределами РФ ({site.server_country}). Если данные из форм сохраняются "
        "на этом сервере, нарушено требование о локализации ПДн граждан РФ.",
        recommendation="Перенесите сайт и базы данных с ПДн на хостинг в России. Если используется CDN (например, "
        "Cloudflare), убедитесь, что исходный сервер и базы данных находятся в РФ.",
    )


def find_inns(site: SiteData) -> list[str]:
    return list(dict.fromkeys(m.group(1) for p in site.pages for m in INN_RE.finditer(p.text)))


def check_rkn_registry(site: SiteData) -> Finding:
    """Сверка с реестром операторов ПДн (открытые данные РКН, загружаются воркером ежедневно)."""
    inns = find_inns(site)
    rkn = site.profile.get("rkn") or {}
    base = dict(id="rkn_registry", category=CAT, title="Уведомление в реестре операторов ПДн", law="152-ФЗ, ст. 22")
    manual = "Проверка вручную: pd.rkn.gov.ru → «Реестр операторов»."
    if not inns:
        return Finding(**base, status="info", severity="medium",
                       summary="ИНН владельца на сайте не найден — сверить с реестром операторов ПДн невозможно.",
                       recommendation="Укажите ИНН на сайте. " + manual)
    if not rkn.get("loaded"):
        return Finding(**base, status="info", severity="medium", evidence=inns[:3],
                       summary=f"Проверьте ИНН {', '.join(inns[:3])} в реестре операторов ПДн.",
                       recommendation=manual)
    found = {inn: recs for inn, recs in (rkn.get("matches") or {}).items() if recs}
    as_of = f" (данные РКН на {rkn['updated']})" if rkn.get("updated") else ""
    if found:
        ev = [f"ИНН {inn}: рег. № {r.get('reg_number') or '—'}" + (f" от {r['reg_date']}" if r.get("reg_date") else "")
              for inn, recs in found.items() for r in recs[:1]]
        return Finding(**base, status="pass", severity="low", evidence=ev,
                       summary="Оператор найден в реестре операторов, осуществляющих обработку ПДн" + as_of + ".")
    return Finding(
        **base, status="fail", severity="medium", evidence=inns[:3],
        summary=f"ИНН {', '.join(inns[:3])} не найден в реестре операторов ПДн{as_of}. Если уведомление подано "
        "недавно, оно может ещё не попасть в открытые данные.",
        recommendation="Подайте уведомление об обработке ПДн на pd.rkn.gov.ru (или через Госуслуги). "
        "Если уведомление уже подано — укажите его номер в отчёте, мы учтём это при следующей проверке.",
    )
