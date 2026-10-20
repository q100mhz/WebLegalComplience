"""Технические проверки защищённости передачи данных."""

from ..models import Finding, SiteData
from .common import form_is_pd, short


def check_https(site: SiteData) -> Finding:
    h = site.https
    insecure_forms = [
        f"{short(p.url, 80)} → {f.action}"
        for p in site.pages for f in p.forms
        if form_is_pd(f) and (f.action.startswith("http://") or (not f.action.startswith("http") and p.url.startswith("http://")))
    ]
    base = dict(
        id="https", category="Безопасность", title="Защищённое соединение (HTTPS)",
        law="152-ФЗ, ст. 19 (меры по обеспечению безопасности ПДн)",
    )
    if not h.https_ok:
        return Finding(
            **base, status="fail", severity="high", evidence=[h.cert_error or "HTTPS недоступен"] + insecure_forms[:5],
            summary="Сайт не работает по HTTPS или сертификат недействителен — данные из форм передаются без защиты.",
            recommendation="Установите действующий SSL-сертификат и настройте редирект с HTTP на HTTPS.",
        )
    problems = []
    if insecure_forms:
        problems.append("формы с ПДн отправляются по HTTP")
    if h.http_redirects_to_https is False:
        problems.append("нет редиректа с HTTP на HTTPS")
    if not h.hsts:
        problems.append("не настроен заголовок HSTS")
    if not problems:
        return Finding(**base, status="pass", severity="low", summary="HTTPS настроен, есть редирект с HTTP и HSTS.")
    return Finding(
        **base,
        status="fail" if insecure_forms else "warn",
        severity="high" if insecure_forms else "low",
        evidence=insecure_forms[:5],
        summary="HTTPS работает, но: " + "; ".join(problems) + ".",
        recommendation="Настройте постоянный (301) редирект на HTTPS и заголовок Strict-Transport-Security.",
    )
