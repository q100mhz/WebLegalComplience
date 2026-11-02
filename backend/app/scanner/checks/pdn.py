"""Проверки по 152-ФЗ «О персональных данных»."""

import re
from urllib.parse import urlsplit
from collections import defaultdict

from ..models import Finding, PageData, SiteData
from ..services_db import match_service, match_tracking_cookie
from .common import (
    POLICY_HEADING_RE,
    page_is_consent_doc,
    policy_score,
    CONSENT_TEXT_RE,
    INN_RE,
    digits,
    abs_link,
    link_is_consent,
    link_is_policy,
    page_links_policy,
    policy_links,
    policy_pages,
    policy_urls,
    short,
    field_label,
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
    # ссылка «Политика…» ведёт на документ согласия — политики как таковой нет
    by_url = {p.url.split("#")[0].rstrip("/"): p for p in site.pages}
    targets = {abs_link(p, l).split("#")[0].rstrip("/") for p, l in links}
    crawled = [by_url[t] for t in targets if t in by_url]
    if links and not pages and not docs and crawled and len(crawled) == len(targets) and all(map(page_is_consent_doc, crawled)):
        return Finding(
            **base, status="fail", severity="high", evidence=[short(p.url) for p in crawled][:5],
            summary="Ссылка «Политика…» ведёт на документ согласия на обработку ПДн, а не на политику. Политика — "
            "отдельный документ оператора о том, как он обрабатывает данные (ст. 18.1), согласие её не заменяет.",
            recommendation="Опубликуйте политику в отношении обработки ПДн отдельной страницей и исправьте ссылку.",
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
    if home and not page_links_policy(home, policy_urls(site)):
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
    "сведения об операторе (наименование, ИНН/ОГРН, адрес)":
        r"оператор|ИНН|ОГРН|\b(ООО|АО|ПАО|ЗАО|ОАО|ИП)\s*[«\"]|\bИП\s+[А-ЯЁ][а-яё]+|индивидуальн\w+\s+предпринимател|"
        r"обществ\w*\s+с\s+ограниченной",
    "цели обработки": r"цел\w*\s+(обработки|сбора)|цел\w*[^.]{0,30}(обработ|сбор|использ)|в\s+целях|для\s+целей",
    "правовые основания обработки":
        r"правов\w*\s+основани|основани\w*\s+(для\s+)?обработки|обработк\w*[^.]{0,60}на\s+основании",
    "перечень (категории) обрабатываемых ПДн":
        r"(перечень|категори|состав|объ[её]м)\w*.{0,60}персональн|"
        r"(обрабатыва|собира|относ|включа|получа)\w*[^.]{0,80}(фамили|\bим[яени]\b|телефон|электронн\w+\s+почт|e-?mail)",
    "категории субъектов ПДн":
        r"категори\w*\s+субъект|субъект\w*\s+персональн|пользовател\w*|посетител\w*|клиент\w*|покупател\w*",
    "перечень действий и способы обработки":
        r"способ\w*\s+обработки|перечень\s+действий|сбор[^.]{0,60}(запис|хранени|систематизац|накоплени)|"
        r"автоматизированн\w*|смешанн\w+\s+обработк",
    "сроки обработки и хранения":
        r"срок\w*\s+(обработки|хранения)|(хран|обрабатыва)\w*[^.]{0,60}(в\s+течение|до\s+достижения|до\s+момента|не\s+дольше)",
    "порядок уничтожения ПДн": r"уничтожени|уничтож|удал\w*[^.]{0,30}(персональн|данн)|обезличи",
    "права субъекта ПДн":
        r"прав\w*\s+субъект|субъект\w*.{0,40}вправе|имеет\s+право|(пользовател|клиент)\w*[^.]{0,40}(вправе|может\s+"
        r"(отозвать|потребовать))|отозвать\s+(сво[её]\s+)?согласие|отзыв\w*\s+согласи",
    "меры по защите ПДн": r"мер\w*.{0,60}(защит|безопасност)|(защит|безопасност)\w*[^.]{0,60}(персональн|данн|информац)",
}


def analytics_used(site: SiteData) -> bool:
    return any((s := match_service(u)) and s.category in ("analytics", "ads") for u in site.all_request_urls())


def check_policy_content(site: SiteData) -> Finding | None:
    pages = policy_pages(site)
    if not pages:
        by_url = {p.url.split("#")[0].rstrip("/"): p for p in site.pages}
        to_consent = lambda p, l: (t := by_url.get(abs_link(p, l).split("#")[0].rstrip("/"))) and page_is_consent_doc(t)  # noqa: E731
        if any(link_is_policy(d) for d in site.document_links) or any(not to_consent(p, l) for p, l in policy_links(site)):
            return Finding(
                id="policy_content", category=CAT, title="Содержание политики обработки ПДн",
                status="info", severity="low", law="152-ФЗ, ст. 18.1",
                summary="Политика размещена в виде файла или не открылась — содержание не проанализировано.",
                recommendation="Рекомендуем публиковать политику HTML-страницей: её проще индексировать и читать.",
            )
        return None
    best = pages[0]  # лучшая по содержанию — не самая длинная (ею может оказаться оферта)
    text = best.text
    missing = [name for name, rx in POLICY_ELEMENTS.items() if not re.search(rx, text, re.I | re.S)]
    if any(match_service(u) and match_service(u).foreign for u in site.all_request_urls()):
        if not re.search(r"трансграничн", text, re.I):
            missing.append("условия трансграничной передачи (на сайте есть иностранные сервисы)")
    if (site.initial_cookies or analytics_used(site)) and not re.search(r"cookie|куки", text, re.I):
        missing.append("сведения об использовании cookies и метрических программ")
    analytics = sorted({s.name for u in site.all_request_urls() if (s := match_service(u)) and s.category == "analytics"})
    unnamed = [a for a in analytics if not re.search(re.escape(a.split()[0].split("/")[0]), text, re.I)]
    if unnamed:
        missing.append("упоминание используемых сервисов аналитики: " + ", ".join(unnamed))
    base = dict(
        id="policy_content", category=CAT, title="Содержание политики обработки ПДн",
        law="152-ФЗ, ст. 14, 18.1; рекомендации РКН", evidence=[best.url],
    )
    doc_link = next((abs_link(best, l) for l in best.links if link_is_policy(l)
                     and DOC_EXT_RE.search(urlsplit(l.href).path)), None)
    if doc_link and len(missing) >= 5:  # страница — оглавление со ссылкой на файл политики
        return Finding(
            **{**base, "evidence": [best.url, doc_link]}, status="info", severity="low",
            summary="Политика размещена в виде файла — содержание не проанализировано.",
            recommendation="Рекомендуем публиковать политику HTML-страницей: её проще индексировать и читать.",
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


# Пассивное согласие: «Продолжая / Нажимая кнопку / Отправляя форму, вы соглашаетесь…»
IMPLIED_RE = re.compile(
    r"(продолжая|нажима\w*|отправля\w*|оставля\w*|заполня\w*|регистрируясь|авторизуясь|используя|кликая|указывая|"
    r"подтвержда\w*)[^.!?]{0,160}?(соглаша\w*|согласи\w*|принима\w*|подтвержда\w*|даёт\w*|дает\w*|даю)", re.I | re.S)
BUNDLED_RE = re.compile(r"пользовательск\w+\s+соглашени|оферт|правил\w*\s+(сайта|сервиса|пользования)|услови\w+\s+(использования|продажи)", re.I)


def near_form_text(page, form) -> str:
    """Текст страницы сразу после кнопки отправки: фразу о согласии часто ставят под формой, за её границей."""
    label = (form.submit.split("|")[0].strip() or "")[:40]
    label = re.sub(r"\s*[#.][\w-]+.*$", "", label)  # «Отправить #rec123 .t-btn» → «Отправить» (Tilda)
    if len(label) < 3:
        return ""
    for m in re.finditer(re.escape(label), page.text, re.I):
        win = page.text[m.end():m.end() + 350]
        if IMPLIED_RE.search(win) or CONSENT_TEXT_RE.search(win) or re.search(r"персональн\w+\s+данн", win, re.I):
            return win
    return ""


# «Я согласен с условиями обработки персональных данных», «…ознакомлен с политикой конфиденциальности»
FORM_CONSENT_HINT_RE = re.compile(r"согла\w*[^.!?\n]{0,60}(обработк|персональн)|политик\w*\s+(конфиденциальн|обработки)", re.I)


def check_forms_consent(site: SiteData) -> Finding:
    forms = unique_pd_forms(site)
    base = dict(id="forms_consent", category=CAT, title="Согласие на обработку ПДн в формах", law="152-ФЗ, ст. 9")
    if not forms:
        return Finding(**base, status="info", severity="low", summary="Формы сбора персональных данных не обнаружены.")
    bad: dict[str, list[str]] = defaultdict(list)  # none | prechecked | implied | bundled | no_link
    for page, f in forms:
        consent_boxes = [c for c in f.checkboxes if re.search(r"согла|персональн|политик|обработк|конфиденц", c.label, re.I)]
        fields = ", ".join(field_label(i) for i in f.inputs[:4])
        where = f"{short(page.url, 90)} — поля: {fields}"
        if any(c.checked for c in consent_boxes):
            bad["prechecked"].append(where)
        elif consent_boxes:
            if not any(link_is_policy(l) or link_is_consent(l) for l in f.links):
                bad["no_link"].append(where)
        elif not f.text.strip() and not f.submit.strip() and not near_form_text(page, f):
            bad["unknown"].append(where)  # форма не отрисовалась (скрытый дубль, всплывающее окно) — судить не по чему
        elif (m := IMPLIED_RE.search(consent_text := f.text + " " + near_form_text(page, f))) or \
                CONSENT_TEXT_RE.search(consent_text) or FORM_CONSENT_HINT_RE.search(consent_text):
            quote = consent_text[m.start():m.start() + 200].split("\n")[0].strip() if m else ""
            bad["implied"].append(where + (f" — «{quote[:160]}»" if quote else ""))
            if m and BUNDLED_RE.search(consent_text[m.start():m.start() + 260]):
                bad["bundled"].append(where)
        else:
            bad["none"].append(where)

    if len(bad["unknown"]) == len(forms):
        return Finding(**base, status="info", severity="low", evidence=bad["unknown"][:5],
                       summary="Формы сбора ПДн найдены, но не отрисовались при сканировании (скрытые или во всплывающем "
                       "окне) — наличие согласия проверить не удалось.",
                       recommendation="Проверьте вручную, что в каждой форме есть чекбокс согласия без отметки по умолчанию.")
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
        parts = []
        if bad["implied"]:
            parts.append(
                f"В {len(bad['implied'])} форм(ах) согласие выражено фразой вида «Продолжая (нажимая кнопку), вы "
                "соглашаетесь…» без отдельного действия пользователя. Закон не требует именно чекбокс, но согласие "
                "должно быть конкретным, сознательным и однозначным (ч. 1 ст. 9 152-ФЗ), а с 01.09.2025 — оформленным "
                "отдельно от иных документов. Пассивная формулировка с высокой вероятностью будет признана "
                "ненадлежащим согласием.")
        if bad["bundled"]:
            parts.append(f"В {len(bad['bundled'])} форм(ах) согласие на обработку ПДн объединено в одной фразе с "
                         "принятием пользовательского соглашения или оферты — это прямо противоречит требованию "
                         "оформлять согласие отдельно.")
        if bad["no_link"]:
            parts.append(f"В {len(bad['no_link'])} форм(ах) есть чекбокс, но нет ссылки на текст согласия или политику.")
        return Finding(
            **base, status="warn", severity="high" if bad["bundled"] else "medium",
            summary=" ".join(parts),
            recommendation="Замените фразу на отдельный обязательный чекбокс без отметки по умолчанию: «Я даю согласие на "
            "обработку персональных данных» со ссылкой на отдельный документ согласия; принятие пользовательского "
            "соглашения — отдельным чекбоксом.",
            evidence=(bad["implied"] + bad["no_link"])[:10],
        )
    return Finding(**base, status="pass", severity="low", summary=f"Все формы с ПДн ({len(forms)}) содержат чекбокс согласия.")


# «soglasie» — согласие; «soglashenie» — соглашение (пользовательское), это другой документ
CONSENT_TARGET_RE = re.compile(r"soglasi[eyj]|consent|(?<!user)(?<!user[-_])agree(?!ment[-_]?user)|pd[-_]?agree", re.I)
NOT_CONSENT_TARGET_RE = re.compile(r"polit|privacy|polic|konfid|oferta|offer|polzovatel|soglashen|terms|"
                                   r"usloviya|rules|pravila|cookie", re.I)
DOC_EXT_RE = re.compile(r"\.(pdf|docx?|rtf|odt)$", re.I)
# Волеизъявление субъекта от первого лица — признак текста согласия, а не политики
CONSENT_STATEMENT_RE = re.compile(r"(даю|да[её]т|выражаю|выражает|предоставляю|предоставляет)\s+(сво[её]\s+|оператору\s+|настоящее\s+)?согласие|"
                                  r"настоящим\s+(я\s+)?(,\s*)?(свободно|даю|выражаю|подтверждаю\s+сво[её]\s+согласие)", re.I)
# Незаполненный шаблон: «(адрес сайта)», «[наименование]», «ООО «______»»
TEMPLATE_RE = re.compile(r"\((адрес\s+сайта|название\s+компании|наименование\s+(компании|организации|оператора)|полные\s+"
                         r"реквизиты|ФИО|ИНН|укажите)[^)\d]{0,40}\)|\[(наименование|название|адрес|ФИО|ИНН|укажите)[^\]\d]{0,40}\]|"
                         r"«\s*_{3,}|_{8,}|http://\(адрес", re.I)


def _consent_target(site: SiteData, url: str) -> str:
    """Куда на самом деле ведёт ссылка «согласие»: consent | mixed | popup | policy | unknown.

    mixed — текст согласия есть, но внутри страницы политики (не отдельный документ)."""
    if not url or url.endswith("#") or url.startswith(("javascript", "#")):
        return "popup"  # текст согласия открывается во всплывающем окне — по коду страницы не проверить
    path = urlsplit(url).path
    if DOC_EXT_RE.search(path):
        return "consent"  # отдельный файл, на который ведёт ссылка «согласие»
    page = next((p for p in site.pages if p.url.split("#")[0].rstrip("/") == url.split("#")[0].rstrip("/")), None)
    if page is not None:  # страница просканирована — решает содержание, а не адрес
        heading = f"{page.title} {page.h1 or ''}".strip() or page.text[:60]
        statement = bool(CONSENT_STATEMENT_RE.search(page.text[:30000]))
        if page_is_consent_doc(page):
            return "consent"
        if statement:
            return "mixed" if POLICY_HEADING_RE.search(heading) or policy_score(page) >= 40 else "consent"
        return "policy"
    if CONSENT_TARGET_RE.search(path):
        return "consent"
    if NOT_CONSENT_TARGET_RE.search(path):
        return "policy"
    return "unknown"


def check_separate_consent(site: SiteData) -> Finding | None:
    if not unique_pd_forms(site):
        return None
    base = dict(
        id="separate_consent", category=CAT, title="Согласие оформлено отдельным документом",
        law="152-ФЗ, ч. 1 ст. 9 (ред. 156-ФЗ, с 01.09.2025)",
    )
    if any(p.consent_popup for p in site.pages):
        return Finding(
            **base, status="pass", severity="low",
            summary="Текст согласия показывается во всплывающем окне при отправке формы "
            "(компонент «Согласие пользователя» 1С-Битрикс) — это отдельный документ.",
            recommendation="Убедитесь, что галочка согласия не стоит по умолчанию: в компоненте Битрикса она включена "
            "в настройках «Отмечено по умолчанию».",
        )
    targets: dict[str, list[str]] = defaultdict(list)
    for p, l in site.all_links():
        if link_is_consent(l):
            u = abs_link(p, l) if l.href and not l.href.startswith(("#", "javascript")) else ""
            targets[_consent_target(site, u)].append(u or f"{short(p.url, 60)} (всплывающее окно)")
    for d in site.document_links:
        if link_is_consent(d):
            targets["consent"].append(d.href)
    uniq = lambda xs: list(dict.fromkeys(xs))[:5]  # noqa: E731
    templ = [u for u in targets["consent"] for p in site.pages
             if p.url.rstrip("/") == u.rstrip("/") and TEMPLATE_RE.search(p.text[:30000])]
    if templ:
        return Finding(
            **base, status="warn", severity="medium", evidence=uniq(templ),
            summary="Документ согласия есть, но это незаполненный шаблон: вместо оператора и адреса сайта остались "
            "подстановки вроде «(адрес сайта)», «(название компании)». Такое согласие не указывает, кому оно дано "
            "(п. 1 ч. 4 ст. 9).",
            recommendation="Впишите полное наименование оператора, ИНН/ОГРН, адрес и адрес сайта вместо подстановок.",
        )
    if targets["consent"]:
        return Finding(**base, status="pass", severity="low", evidence=uniq(targets["consent"]),
                       summary="Найден отдельный документ согласия на обработку ПДн.")
    if targets["mixed"]:
        return Finding(
            **base, status="warn", severity="medium", evidence=uniq(targets["mixed"]),
            summary="Текст согласия на обработку ПДн найден, но размещён внутри политики или оферты, а не отдельным "
            "документом. С 1 сентября 2025 года согласие должно оформляться отдельно от иных документов.",
            recommendation="Вынесите текст согласия на отдельную страницу «Согласие на обработку персональных данных» "
            "и ссылайтесь на неё из чекбокса в формах; политику оставьте отдельным документом.",
        )
    if targets["popup"] or targets["unknown"]:
        return Finding(
            **base, status="info", severity="low", evidence=uniq(targets["popup"] + targets["unknown"]),
            summary="Ссылка «согласие на обработку персональных данных» есть, но её содержимое открывается во всплывающем "
            "окне или недоступно для проверки — убедиться, что это отдельный документ согласия, не удалось.",
            recommendation="Проверьте, что по ссылке открывается именно согласие (цели, перечень данных, срок, порядок "
            "отзыва), а не политика или пользовательское соглашение.",
        )
    form_policy = next((abs_link(p, l) for p, f in unique_pd_forms(site) for l in f.links if link_is_policy(l)), None)
    wrong = uniq(targets["policy"])
    explain = ""
    if wrong:
        explain = (f" Ссылка «согласие на обработку персональных данных» ведёт на политику, оферту или пользовательское "
                   f"соглашение ({short(wrong[0], 90)}), а не на отдельный документ согласия.")
    elif form_policy:
        explain = f" Ссылка из формы ведёт на политику ({short(form_policy, 90)})."
    return Finding(
        **base, status="fail", severity="medium", evidence=wrong,
        summary="Не найден отдельный текст согласия на обработку ПДн. С 1 сентября 2025 года согласие должно оформляться "
        "отдельно от политики, оферты и иных документов." + explain + " Политика — документ оператора о том, как он "
        "обрабатывает данные (ст. 18.1); согласие — отдельный документ субъекта с целями, перечнем данных, сроком и "
        "порядком отзыва (ч. 4 ст. 9). Политика согласие не заменяет.",
        recommendation="Опубликуйте отдельную страницу «Согласие на обработку персональных данных» "
        "(цели, перечень данных, действия, срок, порядок отзыва) и ссылайтесь на неё из чекбокса в формах.",
    )


COOKIE_BANNER_RE = re.compile(r"(cookie|куки)", re.I)
COOKIE_DOC_LINE_RE = re.compile(r"(политик|положени|правил|соглашени|уведомлени|информаци|cookie)", re.I)


def has_cookie_banner(page: PageData | None) -> bool:
    """Уведомление о cookies на странице. Ссылка «Политика cookie» в подвале — не уведомление."""
    if page is None:
        return False
    link_texts = {re.sub(r"\s+", " ", l.text).strip().lower() for l in page.links}
    for line in page.text.split("\n"):
        if not COOKIE_BANNER_RE.search(line):
            continue
        line = re.sub(r"\s+", " ", line).strip().lower()
        if line in link_texts or (len(line) < 40 and COOKIE_DOC_LINE_RE.match(line)):
            continue
        return True
    return False


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
    banner = has_cookie_banner(site.home)
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
    if high == ["Google Tag Manager"]:
        # GTM — контейнер: сам по себе он лишь загружает другие теги. Доказанного сбора данных нет — это риск.
        return Finding(
            **base, status="warn", severity="medium", evidence=evidence,
            summary="Подключён Google Tag Manager — иностранный сервис, который передаёт Google IP-адрес посетителя "
            "и может подгружать другие иностранные счётчики.",
            recommendation="Проверьте, какие теги подключены в контейнере GTM. Лучше отказаться от GTM в пользу "
            "прямой установки российских счётчиков (Яндекс Метрика, VK Пиксель).",
        )
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
    return list(dict.fromkeys(digits(m.group(1)) for p in site.pages for m in INN_RE.finditer(p.text)))


def check_rkn_registry(site: SiteData) -> Finding:
    """Сверка с реестром операторов ПДн на портале pd.rkn.gov.ru (результат кладёт воркер в profile["rkn"])."""
    inns = find_inns(site)
    rkn = site.profile.get("rkn") or {}
    base = dict(id="rkn_registry", category=CAT, title="Уведомление в реестре операторов ПДн", law="152-ФЗ, ст. 22")
    manual = "Проверка вручную: pd.rkn.gov.ru → «Реестр операторов» → поиск по ИНН."
    if not inns:
        return Finding(**base, status="info", severity="medium",
                       summary="ИНН владельца на сайте не найден — сверить с реестром операторов ПДн невозможно.",
                       recommendation="Укажите ИНН на сайте. " + manual)
    if not rkn.get("checked"):
        return Finding(**base, status="info", severity="medium", evidence=inns[:3],
                       summary=f"Не удалось получить ответ от портала Роскомнадзора. Проверьте ИНН {', '.join(inns[:3])} "
                       "в реестре операторов ПДн вручную.",
                       recommendation=manual)
    matches = rkn.get("matches") or {}
    found = {inn: recs for inn, recs in matches.items() if recs}
    as_of = f" (pd.rkn.gov.ru, {rkn['checked_at'][:10]})" if rkn.get("checked_at") else ""
    if found:
        ev = [f"ИНН {inn}: рег. № {r['reg_number']}, уведомление от {r.get('reg_date') or '—'} — {r.get('url', '')}"
              for inn, recs in found.items() for r in recs[:1]]
        return Finding(**base, status="pass", severity="low", evidence=ev,
                       summary="Оператор найден в реестре операторов, осуществляющих обработку ПДн" + as_of + ".")
    not_found = list(matches)
    return Finding(
        **base, status="fail", severity="medium", evidence=[f"ИНН {i} — нет в реестре" for i in not_found[:3]],
        summary=f"ИНН {', '.join(not_found[:3])} не найден в реестре операторов ПДн{as_of}. Внесение в реестр занимает "
        "до 30 дней после подачи уведомления — если оно подано недавно, записи может ещё не быть.",
        recommendation="Подайте уведомление об обработке ПДн на pd.rkn.gov.ru (или через Госуслуги). "
        "Если уведомление уже подано — укажите его номер в отчёте, мы учтём это при следующей проверке.",
    )


# Категории данных из форм сайта → как они называются в уведомлении РКН
CARD_CATEGORY_RE = {
    # «контактные данные» в уведомлении покрывают и телефон, и почту
    "номер телефона": r"телефон|контактн\w+\s+(данн|информац|сведени)",
    "адрес электронной почты": r"почт|e-?mail|контактн\w+\s+(данн|информац|сведени)",
    "фамилия, имя, отчество": r"фамили|ф\.?\s?и\.?\s?о|фио",
    "имя": r"\bим[яе]|фамили",
    "адрес (для доставки)": r"адрес\w*\s+(места\s+)?(жительств|регистрац|доставк|проживан)|\bадрес\b(?!\w*\s+электронн)",
    "дата рождения": r"рожден",
    "паспортные данные": r"паспорт",
    "СНИЛС": r"снилс|страхов\w+\s+номер",
    "ИНН": r"\bинн\b|идентификационн\w+\s+номер\s+налогоплательщик",
}
METRICS_IN_CARD_RE = re.compile(r"cookie|куки|метрическ|ip-?\s?адрес|идентификатор\w*\s+(устройств|пользовател|браузер)|"
                                r"сведения\s+о\s+(действиях|посещени)|пользовательск\w+\s+данн", re.I)


def check_rkn_notification(site: SiteData) -> Finding | None:
    """Сверка карточки оператора в реестре РКН с тем, что сервис увидел на сайте:
    трансграничная передача, расположение баз данных, категории данных из форм, данные метрик."""
    from ...documents.facts import categories_for
    from .common import form_is_pd, is_service_form

    rkn = site.profile.get("rkn") or {}
    rec = next((recs[0] for recs in (rkn.get("matches") or {}).values() if recs), None)
    if not rec:
        return None  # оператора нет в реестре — об этом проверка rkn_registry
    base = dict(id="rkn_notification", category=CAT, title="Сведения в уведомлении РКН и сайт",
                law="152-ФЗ, ч. 3, 7 ст. 22")
    card = rec.get("card")
    link = rec.get("url") or ""
    if not card:
        return Finding(**base, status="info", severity="low", evidence=[link] if link else [],
                       summary="Не удалось загрузить карточку оператора в реестре — сведения уведомления не сверены.",
                       recommendation="Сверьте вручную: цели, категории данных, трансграничная передача и место баз данных "
                       "в уведомлении должны соответствовать сайту.")
    fails, warns, notes = [], [], []  # notes — рекомендации: РКН не требует явно перечислять данные метрик

    foreign = sorted({s["name"] for s in site.profile.get("services", [])
                      if s.get("foreign") and s["category"] in ("analytics", "ads", "chat", "captcha")})
    if foreign and card.get("transborder") is False:
        msg = (f"в уведомлении трансграничная передача не заявлена, а сайт передаёт данные иностранным "
               f"сервисам: {', '.join(foreign)}")
        # один Google Tag Manager — контейнер без доказанного сбора данных: риск, как и в правиле foreign_services
        (warns if foreign == ["Google Tag Manager"] else fails).append(msg)
    if card.get("db_russia") is False:
        fails.append(f"базы данных в уведомлении указаны не в России: «{card.get('db_location', '')[:150]}»")

    collected: list[str] = []
    for p in site.pages:
        for f in p.forms:
            if form_is_pd(f) and not is_service_form(f, p.url):
                for c in categories_for([i.placeholder or i.name for i in f.inputs]):
                    if c not in collected:
                        collected.append(c)
    declared = card.get("categories_text") or ""
    if declared:
        missing = [c for c in collected if c in CARD_CATEGORY_RE and not re.search(CARD_CATEGORY_RE[c], declared, re.I)]
        if missing:
            warns.append("формы сайта собирают данные, которых нет в уведомлении: " + ", ".join(missing))
        analytics = [s["name"] for s in site.profile.get("services", []) if s["category"] in ("analytics", "ads")]
        if analytics and not METRICS_IN_CARD_RE.search(declared):
            notes.append("на сайте работают метрические программы (" + ", ".join(analytics[:3]) + "), а в уведомлении "
                         "не указаны собираемые ими данные (cookies, IP-адрес, сведения о действиях на сайте)")

    evidence = ([f"Карточка оператора: {link}"] if link else []) + [
        f"Трансграничная передача в уведомлении: {card.get('transborder_text') or 'не указано'}"[:200],
        f"Базы данных: {card.get('db_location') or 'не указано'}"[:200],
    ]
    if not fails and not warns:
        if notes:
            return Finding(**base, status="info", severity="low", evidence=evidence,
                           summary="Основные сведения уведомления соответствуют сайту. Рекомендация: " + "; ".join(notes) + ".",
                           recommendation="Рассмотрите добавление в уведомление категорий данных, собираемых метриками.")
        return Finding(**base, status="pass", severity="low", evidence=evidence,
                       summary="Сведения в уведомлении РКН соответствуют тому, что найдено на сайте.")
    return Finding(
        **base, status="fail" if fails else "warn", severity="high" if fails else "medium",
        evidence=evidence + [f"• {x}" for x in fails + warns],
        summary="Сведения в уведомлении Роскомнадзора расходятся с тем, что происходит на сайте: "
        + "; ".join(fails + warns) + ".",
        recommendation="Подайте в РКН информационное письмо о внесении изменений в сведения об операторе (ч. 7 ст. 22 "
        "152-ФЗ — в течение 10 рабочих дней с даты изменений) либо приведите сайт в соответствие с уведомлением.",
    )
