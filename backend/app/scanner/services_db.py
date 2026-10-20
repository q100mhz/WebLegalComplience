"""Справочник сторонних сервисов, которые встречаются на сайтах.

category:
  analytics — аналитика/пиксели, собирают идентификаторы посетителей (ПДн по позиции РКН)
  ads       — рекламные сети
  fonts     — шрифты (передают IP-адрес посетителя)
  captcha   — капча (передаёт IP, поведение, cookies)
  chat      — онлайн-чаты и CRM-виджеты (собирают введённые данные)
  video     — встроенное видео
  maps      — карты
  cdn       — библиотеки/CDN
  auth      — сторонняя авторизация
"""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Service:
    name: str
    category: str
    foreign: bool


# Ключ — суффикс домена (или домен + префикс пути через "/")
SERVICES: dict[str, Service] = {
    # Иностранная аналитика
    "google-analytics.com": Service("Google Analytics", "analytics", True),
    "analytics.google.com": Service("Google Analytics", "analytics", True),
    "googletagmanager.com": Service("Google Tag Manager", "analytics", True),
    "connect.facebook.net": Service("Meta Pixel", "analytics", True),
    "facebook.com/tr": Service("Meta Pixel", "analytics", True),
    "hotjar.com": Service("Hotjar", "analytics", True),
    "hotjar.io": Service("Hotjar", "analytics", True),
    "clarity.ms": Service("Microsoft Clarity", "analytics", True),
    "mixpanel.com": Service("Mixpanel", "analytics", True),
    "amplitude.com": Service("Amplitude", "analytics", True),
    "segment.com": Service("Segment", "analytics", True),
    "segment.io": Service("Segment", "analytics", True),
    "analytics.tiktok.com": Service("TikTok Pixel", "analytics", True),
    "snap.licdn.com": Service("LinkedIn Insight", "analytics", True),
    "static.ads-twitter.com": Service("X (Twitter) Pixel", "analytics", True),
    "static.cloudflareinsights.com": Service("Cloudflare Web Analytics", "analytics", True),
    "fullstory.com": Service("FullStory", "analytics", True),
    "heapanalytics.com": Service("Heap", "analytics", True),
    "plausible.io": Service("Plausible", "analytics", True),
    # Иностранная реклама
    "doubleclick.net": Service("Google Ads / DoubleClick", "ads", True),
    "googlesyndication.com": Service("Google AdSense", "ads", True),
    "googleadservices.com": Service("Google Ads", "ads", True),
    "criteo.com": Service("Criteo", "ads", True),
    "criteo.net": Service("Criteo", "ads", True),
    "taboola.com": Service("Taboola", "ads", True),
    "outbrain.com": Service("Outbrain", "ads", True),
    # Шрифты
    "fonts.googleapis.com": Service("Google Fonts", "fonts", True),
    "fonts.gstatic.com": Service("Google Fonts", "fonts", True),
    "use.typekit.net": Service("Adobe Fonts", "fonts", True),
    "use.fontawesome.com": Service("Font Awesome CDN", "fonts", True),
    # Капча
    "google.com/recaptcha": Service("Google reCAPTCHA", "captcha", True),
    "gstatic.com/recaptcha": Service("Google reCAPTCHA", "captcha", True),
    "recaptcha.net": Service("Google reCAPTCHA", "captcha", True),
    "hcaptcha.com": Service("hCaptcha", "captcha", True),
    "challenges.cloudflare.com": Service("Cloudflare Turnstile", "captcha", True),
    # Чаты / CRM
    "intercom.io": Service("Intercom", "chat", True),
    "intercomcdn.com": Service("Intercom", "chat", True),
    "crisp.chat": Service("Crisp", "chat", True),
    "tawk.to": Service("Tawk.to", "chat", True),
    "drift.com": Service("Drift", "chat", True),
    "zdassets.com": Service("Zendesk", "chat", True),
    "hubspot.com": Service("HubSpot", "chat", True),
    "hs-scripts.com": Service("HubSpot", "chat", True),
    "livechatinc.com": Service("LiveChat", "chat", True),
    "tidio.co": Service("Tidio", "chat", True),
    # Видео / карты
    "youtube.com": Service("YouTube", "video", True),
    "youtube-nocookie.com": Service("YouTube", "video", True),
    "ytimg.com": Service("YouTube", "video", True),
    "vimeo.com": Service("Vimeo", "video", True),
    "maps.googleapis.com": Service("Google Maps", "maps", True),
    "maps.google.com": Service("Google Maps", "maps", True),
    # CDN
    "cdnjs.cloudflare.com": Service("cdnjs (Cloudflare)", "cdn", True),
    "cdn.jsdelivr.net": Service("jsDelivr", "cdn", True),
    "unpkg.com": Service("unpkg", "cdn", True),
    "ajax.googleapis.com": Service("Google Hosted Libraries", "cdn", True),
    # Российские сервисы (для полноты картины)
    "mc.yandex.ru": Service("Яндекс Метрика", "analytics", False),
    "mc.yandex.com": Service("Яндекс Метрика", "analytics", False),
    "top-fwz1.mail.ru": Service("Top@Mail.ru / VK Пиксель", "analytics", False),
    "vk.com/rtrg": Service("VK Реклама (пиксель)", "analytics", False),
    "an.yandex.ru": Service("Рекламная сеть Яндекса", "ads", False),
    "yandex.ru/ads": Service("Рекламная сеть Яндекса", "ads", False),
    "ads.adfox.ru": Service("ADFOX", "ads", False),
    "ad.mail.ru": Service("VK Реклама", "ads", False),
    "adriver.ru": Service("AdRiver", "ads", False),
    "smartcaptcha.yandexcloud.net": Service("Yandex SmartCaptcha", "captcha", False),
    "api-maps.yandex.ru": Service("Яндекс Карты", "maps", False),
    "jivosite.com": Service("JivoSite", "chat", False),
    "jivo.ru": Service("Jivo", "chat", False),
    "bitrix24.ru": Service("Битрикс24", "chat", False),
    "rutube.ru": Service("Rutube", "video", False),
    "vk.com/video_ext": Service("VK Видео", "video", False),
}

# Cookies аналитики/рекламы (не являются строго необходимыми)
TRACKING_COOKIES = {
    "_ga": "Google Analytics", "_gid": "Google Analytics", "_gat": "Google Analytics",
    "_gcl_au": "Google Ads", "_fbp": "Meta Pixel", "_fbc": "Meta Pixel",
    "_ym_uid": "Яндекс Метрика", "_ym_d": "Яндекс Метрика", "_ym_isad": "Яндекс Метрика",
    "_ym_visorc": "Яндекс Метрика", "tmr_lvid": "VK / Top@Mail.ru", "tmr_lvidTS": "VK / Top@Mail.ru",
    "_hjSessionUser": "Hotjar", "_hjid": "Hotjar", "_clck": "Microsoft Clarity", "_clsk": "Microsoft Clarity",
    "_ttp": "TikTok Pixel", "_uetsid": "Microsoft Ads", "_uetvid": "Microsoft Ads",
}


def match_service(url: str) -> Service | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.lower()
    for key, svc in SERVICES.items():
        dom, _, prefix = key.partition("/")
        if (host == dom or host.endswith("." + dom)) and (not prefix or path.startswith("/" + prefix)):
            return svc
    return None


def match_tracking_cookie(name: str) -> str | None:
    for prefix, svc in TRACKING_COOKIES.items():
        if name.startswith(prefix):
            return svc
    return None
