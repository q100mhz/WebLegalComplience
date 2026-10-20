from collections.abc import Callable

from ..models import Finding, SiteData
from . import content, industry, pdn, technical

Check = Callable[[SiteData], Finding | list[Finding] | None]

CHECKS: list[Check] = [
    pdn.check_privacy_policy,
    pdn.check_policy_content,
    industry.check_policy_on_form_pages,
    pdn.check_forms_consent,
    pdn.check_separate_consent,
    industry.check_special_categories,
    pdn.check_cookies,
    pdn.check_foreign_services,
    industry.check_foreign_platform,
    pdn.check_hosting,
    pdn.check_rkn_registry,
    industry.check_newsletter_consent,
    content.check_ad_marking,
    content.check_owner_info,
    industry.check_inn_valid,
    content.check_foreign_auth,
    content.check_meta_links,
    industry.check_recommendation_tech,
    content.check_age_rating,
    content.check_ecommerce,
    industry.check_subscription,
    industry.check_user_terms,
    content.check_language,
    industry.check_restricted_goods,
    industry.check_education,
    industry.check_medical,
    industry.check_supplements,
    industry.check_finance,
    technical.check_https,
    industry.check_payment_cards,
]
