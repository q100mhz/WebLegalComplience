from dataclasses import asdict, dataclass, field
from typing import Literal

Status = Literal["fail", "warn", "pass", "info"]
Severity = Literal["high", "medium", "low"]


@dataclass
class Link:
    href: str
    text: str


@dataclass
class FormInput:
    type: str
    name: str
    placeholder: str = ""
    autocomplete: str = ""


@dataclass
class Checkbox:
    checked: bool
    label: str


@dataclass
class Form:
    action: str
    method: str
    inputs: list[FormInput]
    checkboxes: list[Checkbox]
    text: str
    links: list[Link]
    submit: str = ""
    index: int = -1
    screenshot: str | None = None


@dataclass
class PageData:
    url: str
    status: int | None
    title: str = ""
    lang: str = ""
    text: str = ""
    links: list[Link] = field(default_factory=list)
    forms: list[Form] = field(default_factory=list)
    iframes: list[str] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    request_urls: list[str] = field(default_factory=list)
    generator: str = ""
    scripts: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    html: str = ""  # сохраняется только для главной — нужен для определения CMS
    screenshot: str | None = None


@dataclass
class HttpsInfo:
    https_ok: bool = False
    cert_error: str | None = None
    http_redirects_to_https: bool | None = None
    hsts: bool = False
    security_headers: dict[str, bool] = field(default_factory=dict)


@dataclass
class SiteData:
    start_url: str
    host: str
    pages: list[PageData] = field(default_factory=list)
    # Состояние до любого взаимодействия с сайтом (первый заход на главную)
    initial_cookies: list[dict] = field(default_factory=list)
    initial_request_urls: list[str] = field(default_factory=list)
    document_links: list[Link] = field(default_factory=list)
    https: HttpsInfo = field(default_factory=HttpsInfo)
    server_ip: str | None = None
    server_country: str | None = None
    errors: list[str] = field(default_factory=list)
    profile: dict = field(default_factory=dict)

    @property
    def home(self) -> PageData | None:
        return self.pages[0] if self.pages else None

    def all_links(self) -> list[tuple[PageData, Link]]:
        return [(p, l) for p in self.pages for l in p.links]

    def all_request_urls(self) -> set[str]:
        urls = set(self.initial_request_urls)
        for p in self.pages:
            urls.update(p.request_urls)
        return urls


@dataclass
class Finding:
    id: str
    category: str
    title: str
    status: Status
    law: str
    summary: str
    severity: Severity = "medium"
    recommendation: str = ""
    penalty: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
