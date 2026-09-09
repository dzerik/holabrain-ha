#!/usr/bin/env python3
"""Research utility: download Midea per-model `.lua` protocol codecs from the public cloud.

This is NOT part of the integration runtime — the HolaBrain (dollin) appliances this
integration controls speak a flat-JSON plugin dialect and need no Lua codec. The codecs are
the *standard msmart binary-frame* codecs served by the public MSmartHome cloud, kept here as
a reverse-engineering aid (protocol reference, and the binary/transparent path).

It logs in interactively to a free MSmartHome account (register in the SmartHome / MSmartHome
mobile app), lists that account's appliances, and downloads the codec for each; extra
`type:model` pairs can be given on the command line. The app-level credentials below are the
public MSmartHome app keys, embedded in every install and used only to sign the request — a
per-user access token from the interactive login is what actually authorizes the fetch. Needs
only `httpx` and `cryptography` (both already required by Home Assistant); no `lupa`
(that is only needed to *run* a codec, not to download it).

Usage:
    python3 scripts/fetch_lua_codecs.py [--out DIR] [type:model ...]
Example:
    python3 scripts/fetch_lua_codecs.py --out ./lua_codecs 0xC3:171H120F
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from datetime import UTC, datetime

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

# --- public MSmartHome app credentials (as shipped by the GPL midea_auto_cloud project) ------
APP_KEY = "ac21b9f9cbfe4ca5a88562ef25e2b768"
IOT_KEY = bytes.fromhex(format(7882822598523843940, "x")).decode()  # "meicloud"
HMAC_KEY = bytes.fromhex(format(117390035944627627450677220413733956185864939010425, "x")).decode()
LOGIN_KEY = APP_KEY
FIXED_KEY = format(13101328926877700970, "x").encode("ascii")  # AES-128 key for codec decrypt
FIXED_IV = format(16429062708050928556, "x").encode("ascii")
API_ROOT = "https://mp-prod.appsmb.com/mas/v5/app/proxy?alias="
APP_ID = "1010"
SRC = "10"
APP_VERSION = "3.0.2"

# The HolaBrain / dollin device catalogue (every appliance type and model this ecosystem
# registers), used by --all to sweep the whole catalogue for available codecs. Not every entry
# resolves — the public cloud has no codec for some types (0x13/0x33/0xB1/0xC3/0xCC/0xE4) — and
# models of a type share one codec, so a sweep yields far fewer files than pairs.
CATALOG: dict[str, list[str]] = {
    "0x13": ["22222222", "TEST0077"],
    "0x33": ["622TEST1", "622TEST2"],
    "0xAC": ["20020AC1"],
    "0xB1": ["70000B42", "711000H6"],
    "0xC3": ["17100003", "17100007", "171H120F"],
    "0xCA": ["310A056C", "310A066T", "310A06FH", "310A1609", "310A1659", "310A2140"],
    "0xCC": ["17100001"],
    "0xDB": ["38127413", "38127414", "38132940"],
    "0xE1": [
        "000000D5", "760EY09A", "760EY09B", "760EY09C", "760EY09D", "760EY09E", "760EY09F",
        "760EY09G", "760EY09H", "760EY09J", "760EY09K", "760EY09L", "760EY09M", "760EY09N",
        "760EY171", "760EY172", "760EY173", "760EY174", "760EY175", "760EY176", "760EY177",
        "760EY178", "760EY179", "760EY180", "760EY182", "760EY183", "760EY189", "760EY214",
        "760EY215", "760EY216", "760EY217", "760EY218", "760EY219", "760EY21A",
    ],
    "0xE2": [
        "51000ED8", "51015EW1", "51020ED1", "51020ED2", "51020ED8", "51020EDA", "51020EDB",
        "51020EDD", "51020EW5", "5103EFT2",
    ],
    "0xE3": ["511PCH01"],
    "0xE4": [
        "12000001", "12000011", "12088888", "17200003", "17200004", "17200022", "17200023",
        "17200024", "17200025", "1720002T", "1720002W", "1720002Z", "17200030", "17200032",
        "17200033", "17200034", "17200035", "17200036", "220L0007", "L2376001", "L3317301",
        "L3966401", "L3997701", "L4007701", "L4373601", "L4519701", "L4697701", "L4703901",
        "L4706301", "L7696801", "L7937303", "L8315701",
    ],
    "0xED": ["63200001", "63200005"],
    "0xFC": ["5710003H", "5710003L"],
}


def _aes_cbc(data: bytes, key: bytes, iv: bytes, *, encrypt: bool) -> bytes:
    ctx = Cipher(algorithms.AES(key), modes.CBC(iv))
    op = ctx.encryptor() if encrypt else ctx.decryptor()
    if encrypt:
        padder = PKCS7(128).padder()
        return op.update(padder.update(data) + padder.finalize()) + op.finalize()
    plain = op.update(data) + op.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(plain) + unpadder.finalize()


def _enc_fixed(data: bytes) -> str:
    return _aes_cbc(data, FIXED_KEY, FIXED_IV, encrypt=True).hex()


def _dec_fixed(hex_text: str) -> str:
    plain = _aes_cbc(bytes.fromhex(hex_text.strip()), FIXED_KEY, FIXED_IV, encrypt=False)
    return plain.decode("utf-8")


def _sign(body: str, random: str) -> str:
    msg = (IOT_KEY + body + random).encode()
    return hmac.new(HMAC_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _encrypt_password(login_id: str, password: str) -> str:
    inner = hashlib.sha256(password.encode("ascii")).hexdigest()
    return hashlib.sha256((login_id + inner + LOGIN_KEY).encode("ascii")).hexdigest()


def _encrypt_iam_password(login_id: str, password: str) -> str:
    first = hashlib.md5(password.encode("ascii")).hexdigest()
    second = hashlib.md5(first.encode("ascii")).hexdigest()
    return hashlib.sha256((login_id + second + LOGIN_KEY).encode("ascii")).hexdigest()


class MSmartHome:
    """Minimal MSmartHome cloud client: login + appliance list + codec download."""

    def __init__(self, client: httpx.AsyncClient, account: str, password: str) -> None:
        self._client = client
        self._account = account
        self._password = password
        self._device_id = hashlib.md5(f"Hello, {account}!".encode("ascii")).hexdigest()[:16]
        self._api_url = API_ROOT
        self._uid = ""
        self._token: str | None = None
        self._auth_base = base64.b64encode(f"{APP_KEY}:{IOT_KEY}".encode("ascii")).decode("ascii")

    def _general(self) -> dict:
        return {
            "appVersion": APP_VERSION,
            "src": SRC,
            "format": "2",
            "stamp": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
            "platformId": "1",
            "deviceId": self._device_id,
            "reqId": secrets.token_hex(16),
            "uid": self._uid,
            "clientType": "1",
            "appId": APP_ID,
        }

    async def _request(self, endpoint: str, data: dict) -> dict | None:
        body = json.dumps(data)
        random = str(int(time.time()))
        headers = {
            "content-type": "application/json; charset=utf-8",
            "secretVersion": "1",
            "sign": _sign(body, random),
            "random": random,
            "x-recipe-app": APP_ID,
            "authorization": f"Basic {self._auth_base}",
        }
        if self._token:
            headers["accesstoken"] = self._token
        if self._uid:
            headers["uid"] = self._uid
        url = self._api_url + endpoint
        r = await self._client.post(url, content=body.encode(), headers=headers, timeout=40)
        payload = r.json()
        if int(payload.get("code", -1)) == 0:
            return payload.get("data", {"message": "ok"})
        msg = payload.get('msg') or payload.get('message')
        self._last_error = f"code={payload.get('code')} msg={msg}"
        return None

    async def login(self) -> bool:
        reroute = self._general() | {"userName": self._account, "platformId": "1", "userType": "0"}
        resp = await self._request("/v1/unitcenter/router/user/name", reroute)
        if resp and (mas := resp.get("masUrl")):
            self._api_url = mas
        login_id_resp = await self._request(
            "/v1/user/login/id/get", self._general() | {"loginAccount": self._account, "type": "1"}
        )
        if not login_id_resp or not (login_id := login_id_resp.get("loginId")):
            return False
        iot_data = self._general()
        iot_data.pop("uid", None)
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        iot_data.update({
            "iampwd": _encrypt_iam_password(login_id, self._password),
            "loginAccount": self._account,
            "password": _encrypt_password(login_id, self._password),
            "stamp": stamp,
        })
        body = {
            "iotData": iot_data,
            "data": {"appKey": APP_KEY, "deviceId": self._device_id, "platform": "2"},
            "stamp": stamp,
        }
        resp = await self._request("/mj/user/login", body)
        if not resp:
            return False
        self._uid = resp["uid"]
        self._token = resp["mdata"]["accessToken"]
        return True

    async def list_appliances(self) -> list[dict]:
        resp = await self._request("/v1/appliance/user/list/get", self._general())
        return (resp or {}).get("list", []) if isinstance(resp, dict) else []

    async def download_lua(self, device_type: int, model: str, out_dir: str) -> str | None:
        # The server validates the serial length (>=17) but keys the lookup on (type, model);
        # a synthetic type-matched serial is enough for this product-level fetch.
        sn = f"0000{device_type:02X}0000{model}00000000000000"[:32]
        data = self._general() | {
            "iotAppId": APP_ID,
            "applianceMFCode": "0000",
            "applianceType": f"0x{device_type:02X}",
            "modelNumber": model,
            "applianceSn": _enc_fixed(sn.encode("ascii")),
            "version": "0",
            "encryptedType ": "2",
        }
        resp = await self._request("/v2/luaEncryption/luaGet", data)
        if not resp or not resp.get("url"):
            return None
        file_name = resp.get("fileName") or f"0x{device_type:02X}_{model}.lua"
        path = os.path.join(out_dir, file_name)
        if os.path.exists(path):
            return path
        got = await self._client.get(resp["url"], timeout=60)
        if got.status_code != 200:
            return None
        os.makedirs(out_dir, exist_ok=True)
        lua = 'local bit = require "bit"\n' + _dec_fixed(got.text).replace("\r\n", "\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(lua)
        return path


def _parse_targets(args: list[str]) -> list[tuple[int, str]]:
    out = []
    for a in args:
        if ":" not in a:
            print(f"skipping '{a}': expected type:model, e.g. 0xC3:171H120F", file=sys.stderr)
            continue
        t, model = a.split(":", 1)
        out.append((int(t, 16), model))
    return out


async def _run(out_dir: str, extra: list[tuple[int, str]], all_catalog: bool) -> int:
    account = input("MSmartHome email: ").strip()
    password = getpass.getpass("MSmartHome password: ")
    if not account or not password:
        print("email and password are required", file=sys.stderr)
        return 2
    async with httpx.AsyncClient(timeout=60) as client:
        cloud = MSmartHome(client, account, password)
        print("logging in…")
        if not await cloud.login():
            print(f"login failed: {getattr(cloud, '_last_error', '?')}", file=sys.stderr)
            return 1
        print(f"logged in as {account}")
        # Targets: the account's own appliances (by sn8), any command-line pairs, and — with
        # --all — the whole HolaBrain catalogue. Deduplicated, keeping first-seen order.
        targets: list[tuple[int, str]] = []
        for a in await cloud.list_appliances():
            try:
                dt = int(str(a.get("type")), 16)
            except (TypeError, ValueError):
                continue
            model = a.get("sn8") or a.get("modelNumber")
            if model:
                targets.append((dt, str(model)))
                print(f"  found appliance: 0x{dt:02X} {model} ({a.get('name')})")
        targets += extra
        if all_catalog:
            targets += [(int(t, 16), m) for t, models in CATALOG.items() for m in models]
        targets = list(dict.fromkeys(targets))
        if not targets:
            print("no appliances on the account and no targets given — nothing to fetch")
            return 0
        print(f"fetching codecs for {len(targets)} (type, model) pair(s)…")
        seen_files: set[str] = set()
        missing = 0
        for dt, model in targets:
            path = await cloud.download_lua(dt, model, out_dir)
            if path:
                if path not in seen_files:
                    seen_files.add(path)
                    print(f"  0x{dt:02X} {model} -> {os.path.basename(path)}")
            else:
                missing += 1
                # In a full sweep most pairs have no codec; only report misses when targeted.
                if not all_catalog:
                    err = getattr(cloud, "_last_error", "not found")
                    print(f"  0x{dt:02X} {model} -> no lua ({err})")
        print(f"\n{len(seen_files)} unique codec file(s) in {out_dir}; {missing} pair(s) had none.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Midea .lua codecs from the public MSmartHome cloud."
    )
    parser.add_argument("--out", default="./lua_codecs", help="output directory")
    parser.add_argument(
        "--all", action="store_true", help="sweep the whole HolaBrain catalogue for every codec"
    )
    parser.add_argument("targets", nargs="*", help="extra type:model pairs, e.g. 0xC3:171H120F")
    ns = parser.parse_args()
    sys.exit(asyncio.run(_run(ns.out, _parse_targets(ns.targets), ns.all)))


if __name__ == "__main__":
    main()
