from collections.abc import Callable

from ..models import Finding, SiteData
from . import content, pdn, technical

CHECKS: list[Callable[[SiteData], Finding | None]] = [
    pdn.check_privacy_policy,
    pdn.check_policy_content,
    pdn.check_forms_consent,
    pdn.check_separate_consent,
    pdn.check_cookies,
    pdn.check_foreign_services,
    pdn.check_hosting,
    pdn.check_rkn_registry,
    content.check_owner_info,
    content.check_foreign_auth,
    content.check_meta_links,
    content.check_ad_marking,
    content.check_ecommerce,
    content.check_age_rating,
    content.check_language,
    technical.check_https,
]
