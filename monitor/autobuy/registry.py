from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from . import urls
from .resolver import extract_urls, resolve

_GREETING = re.compile(r"ol[aá],\s*([^\n]+)", re.IGNORECASE)
_NOT_LOGGED = (
    "faca seu login",
    "fazer login",
    "acesse sua conta",
    "identifique-se",
    "entre ou cadastre",
    "cadastre-se",
    "entrar",
    "minha conta",
)

_KABUM_USER_SCRIPT = "() => document.body ? document.body.innerText.slice(0, 4000) : ''"
_AMAZON_USER_SCRIPT = (
    "() => (document.querySelector('#nav-link-accountList-nav-line-1') || {}).textContent || ''"
)
_TERABYTE_USER_SCRIPT = "() => document.body ? document.body.innerText.slice(0, 3000) : ''"
_PICHAU_USER_SCRIPT = "() => document.body ? document.body.innerText.slice(0, 3000) : ''"


@dataclass(frozen=True, slots=True)
class StoreSpec:
    id: str
    label: str
    owns: Callable[[str], bool]
    canonical: Callable[[str], str]
    login_url: str
    home_url: str
    user_script: str
    can_checkout: bool = True
    requires_window: bool = False
    session_cookies: tuple[str, ...] = ()
    cookie_domain: str = ""
    needs_real_chrome: bool = False

    def headless_allowed(self, preferido: bool) -> bool:
        return preferido and not self.requires_window

    def driver(self, context: Any, on_log=None, on_captcha=None):
        if self.id == "kabum":
            from .kabum import KabumDriver

            return KabumDriver(context, on_log=on_log, on_captcha=on_captcha)
        if self.id == "terabyte":
            from .terabyte import TerabyteDriver

            return TerabyteDriver(context, on_log=on_log, on_captcha=on_captcha)
        if self.id == "pichau":
            from .pichau import PichauDriver

            return PichauDriver(context, on_log=on_log, on_captcha=on_captcha)
        from .amazon import AmazonDriver

        return AmazonDriver(context, on_log=on_log, on_captcha=on_captcha)

    def logged_user(self, raw: str) -> str | None:
        from ..matching import normalize

        text = (raw or "").strip()
        if not text:
            return None
        match = _GREETING.search(text)
        if match is None:
            return None
        name = match.group(1).strip(" ,.!")
        if not name:
            return None
        limpo = normalize(name)
        if any(hint in limpo for hint in _NOT_LOGGED):
            return None
        return name[:40]


STORES: dict[str, StoreSpec] = {
    "kabum": StoreSpec(
        id="kabum",
        label="KaBuM!",
        owns=urls.kabum_owns,
        canonical=urls.kabum_canonical,
        login_url="https://www.kabum.com.br/login",
        home_url="https://www.kabum.com.br",
        user_script=_KABUM_USER_SCRIPT,
        session_cookies=("session", "id_cliente"),
        cookie_domain="kabum",
    ),
    "amazon": StoreSpec(
        id="amazon",
        label="Amazon",
        owns=urls.amazon_owns,
        canonical=urls.amazon_canonical,
        login_url=(
            "https://www.amazon.com.br/ap/signin?openid.mode=checkid_setup"
            "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
            "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
            "&openid.assoc_handle=brflex&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
            "&openid.return_to=https%3A%2F%2Fwww.amazon.com.br%2F"
        ),
        home_url="https://www.amazon.com.br",
        user_script=_AMAZON_USER_SCRIPT,
        session_cookies=("at-acbbr", "sess-at-acbbr", "x-main"),
        cookie_domain="amazon",
    ),
    "terabyte": StoreSpec(
        id="terabyte",
        label="Terabyte",
        owns=urls.terabyte_owns,
        canonical=urls.terabyte_canonical,
        login_url="https://www.terabyteshop.com.br/Identificacao.obj",
        home_url="https://www.terabyteshop.com.br",
        user_script=_TERABYTE_USER_SCRIPT,
        requires_window=True,
        session_cookies=("PHPSESSID",),
        cookie_domain="terabyte",
    ),
    "pichau": StoreSpec(
        id="pichau",
        label="Pichau",
        owns=urls.pichau_owns,
        canonical=urls.pichau_canonical,
        login_url="https://www.pichau.com.br/customer/account/login",
        home_url="https://www.pichau.com.br",
        user_script=_PICHAU_USER_SCRIPT,
        session_cookies=("PHPSESSID", "pichau_customer"),
        cookie_domain="pichau",
        needs_real_chrome=True,
    ),
}


def get(store_id: str) -> StoreSpec | None:
    return STORES.get((store_id or "").strip().lower())


def all_stores() -> list[StoreSpec]:
    return list(STORES.values())


def labels() -> dict[str, str]:
    return {spec.id: spec.label for spec in STORES.values()}


def match_url(url: str) -> StoreSpec | None:
    for spec in STORES.values():
        if spec.owns(url):
            return spec
    return None


def find_product(text: str, allowed: list[str] | None = None) -> tuple[StoreSpec, str] | None:
    found = extract_urls(text)
    permitidas = {s.strip().lower() for s in (allowed or []) if s.strip()}

    def aceita(spec: StoreSpec) -> bool:
        return not permitidas or spec.id in permitidas

    for url in found:
        spec = match_url(url)
        if spec is not None and aceita(spec):
            return spec, spec.canonical(url)
    for url in found:
        if match_url(url) is not None:
            continue
        resolved = resolve(url)
        spec = match_url(resolved)
        if spec is not None and aceita(spec):
            return spec, spec.canonical(resolved)
    return None
