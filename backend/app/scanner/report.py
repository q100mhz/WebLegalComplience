"""Сборка итогового заключения: запуск проверок и расчёт оценки."""

import logging

from ..registry import RULES_BY_ID, format_penalty
from .checks import CHECKS
from .models import Finding, SiteData
from .profile import build_profile

log = logging.getLogger(__name__)

PENALTY = {("fail", "high"): 15, ("fail", "medium"): 10, ("fail", "low"): 5,
           ("warn", "high"): 6, ("warn", "medium"): 4, ("warn", "low"): 2}
ORDER = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
SEV = {"high": 0, "medium": 1, "low": 2}

DISCLAIMER = (
    "Заключение сформировано автоматически по открытым данным сайта и не является юридической консультацией. "
    "Суммы штрафов ориентировочные — для принятия решений обратитесь к юристу."
)


def run_checks(site: SiteData) -> list[Finding]:
    findings = []
    for check in CHECKS:
        try:
            res = check(site)
        except Exception:  # одна сломанная проверка не должна ронять весь отчёт
            log.exception("check %s failed", check.__name__)
            continue
        for f in res if isinstance(res, list) else [res]:
            if f:
                f.penalty = format_penalty(RULES_BY_ID.get(f.id, {})) if f.status in ("fail", "warn") else ""
                findings.append(f)
    findings.sort(key=lambda f: (ORDER[f.status], SEV[f.severity]))
    return findings


def score(findings: list[Finding]) -> int:
    return max(0, 100 - sum(PENALTY.get((f.status, f.severity), 0) for f in findings))


def grade(value: int) -> str:
    if value >= 85:
        return "good"
    if value >= 60:
        return "medium"
    return "bad"


def build_report(site: SiteData) -> dict:
    """site.profile может быть частично заполнен заранее (например, данными реестра РКН)."""
    site.profile = {**build_profile(site), **site.profile}
    findings = run_checks(site)
    value = score(findings)
    counts = {s: sum(1 for f in findings if f.status == s) for s in ORDER}
    return {
        "score": value,
        "grade": grade(value),
        "counts": counts,
        "final_url": site.home.url if site.home else site.start_url,
        "profile": {k: v for k, v in site.profile.items() if k != "rkn"},
        "pages": [{"url": p.url, "title": p.title[:200], "status": p.status, "screenshot": p.screenshot,
                   "via": p.via, "text_len": len(p.text)}
                  for p in site.pages],
        "server": {"ip": site.server_ip, "country": site.server_country},
        "errors": site.errors[:20],
        "stats": site.stats,
        "timing": site.timing,
        "truncated": site.truncated,
        "findings": [f.to_dict() for f in findings],
        "disclaimer": DISCLAIMER,
    }
