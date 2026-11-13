#!/usr/bin/env python3
"""App Store Connect API の小さな道具（標準ライブラリ＋openssl だけ。外部の依存なし）。

使い方（環境変数 ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH が要る）:
  asc.py distribute --bundle-id com.enludus.daily.001 --build-number 202611031324 [--metadata docs/app-store.json]
      ビルドの処理が終わるのを待ち、内部テストのグループ（無ければ作る。全ビルドを自動で配信）に入れ、
      チームのユーザーをテスターに加え、「テストの内容」を書く。
  asc.py distribute --bundle-id ... --build-number latest
      いちばん新しいビルドで同じことをする。
  asc.py metadata --bundle-id ... --metadata docs/app-store.json [--version 1.0.0] [--attach-latest-build]
      アプリ名・サブタイトル・プライバシーポリシー URL・カテゴリ・説明・キーワード・サポート URL・宣伝文を入れる。
      "en-US": {...} のような、ほかの言語のかたまりがあれば、その言語の名前・サブタイトル・説明なども入れる（2026-11-10）。
      --attach-latest-build で、提出用のバージョンに最新の処理済みビルドを選ぶ（提出はしない）。

しないこと: 審査への提出、App プライバシーの申告（API がない）、価格・配信地域・年齢区分の変更。
鍵の中身・トークンは出力しない。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.appstoreconnect.apple.com"
EDITABLE_VERSION_STATES = {
    "PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED", "INVALID_BINARY",
}
LIVE_INFO_STATES = {"READY_FOR_DISTRIBUTION", "READY_FOR_SALE", "REPLACED_WITH_NEW_INFO"}


class AscError(Exception):
    pass


def describe_error(x: dict) -> str:
    """Apple のエラー 1 つを 1 行に。「associated errors を見よ」のときは、その中身（足りないもの）も並べる"""
    text = f"{x.get('code', '')}: {x.get('detail') or x.get('title', '')}"
    assoc = (x.get("meta") or {}).get("associatedErrors") or {}
    parts = []
    for where, items in assoc.items():
        for a in items or []:
            parts.append(f"{where} {a.get('code', '')}: {a.get('detail') or a.get('title', '')}".strip())
    return text + (f"（理由: {' / '.join(parts)}）" if parts else "")


# ---------- JWT（ES256） ----------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def der_to_raw(der: bytes, size: int = 32) -> bytes:
    """ECDSA の DER 署名（SEQUENCE{INTEGER r, INTEGER s}）を JWS の r||s にする。"""
    def read_len(buf, i):
        first = buf[i]
        if first < 0x80:
            return first, i + 1
        n = first & 0x7F
        return int.from_bytes(buf[i + 1:i + 1 + n], "big"), i + 1 + n

    if not der or der[0] != 0x30:
        raise AscError("署名の形が違います（SEQUENCE でない）")
    _, i = read_len(der, 1)
    parts = []
    for _ in range(2):
        if der[i] != 0x02:
            raise AscError("署名の形が違います（INTEGER でない）")
        length, i = read_len(der, i + 1)
        value = der[i:i + length].lstrip(b"\x00")
        if len(value) > size:
            raise AscError("署名の値が長すぎます")
        parts.append(value.rjust(size, b"\x00"))
        i += length
    return parts[0] + parts[1]


def make_token(key_id: str, issuer_id: str, key_path: str, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {"iss": issuer_id, "iat": now, "exp": now + 15 * 60, "aud": "appstoreconnect-v1"}
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}.{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_path],
        input=signing_input.encode(), capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise AscError("鍵で署名できませんでした（ASC_P8 の中身を確かめてください）")
    return f"{signing_input}.{b64url(der_to_raw(result.stdout))}"


# ---------- HTTP ----------

class Client:
    def __init__(self, token_factory, opener=urllib.request.urlopen):
        self._token_factory = token_factory
        self._token = None
        self._token_at = 0.0
        self._open = opener

    def _auth(self) -> str:
        if self._token is None or time.time() - self._token_at > 10 * 60:
            self._token = self._token_factory()
            self._token_at = time.time()
        return self._token

    def request(self, method: str, path: str, body: dict | None = None, ok_conflict: bool = False):
        url = path if path.startswith("http") else API + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._auth()}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._open(req, timeout=60) as res:
                raw = res.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if ok_conflict and e.code == 409:
                return None
            detail = ""
            try:
                errors = json.loads(e.read()).get("errors", [])
                detail = "; ".join(describe_error(x) for x in errors)
            except Exception:  # noqa: BLE001 - 本文が JSON でないとき
                pass
            raise AscError(f"{method} {urllib.parse.urlsplit(url).path} → HTTP {e.code} {detail}".strip()) from None

    def get(self, path: str, **params):
        query = urllib.parse.urlencode({k.replace("__", "[").replace("_E", "]"): v for k, v in params.items()})
        return self.request("GET", f"{path}?{query}" if query else path)

    def get_all(self, path: str, **params):
        page = self.get(path, **params)
        items = list(page.get("data", []))
        while page.get("links", {}).get("next"):
            page = self.request("GET", page["links"]["next"])
            items.extend(page.get("data", []))
        return items


def q(name: str) -> str:
    """filter[bundleId] のような名前を get() のキーワードにする: q('filter[bundleId]')"""
    return name.replace("[", "__").replace("]", "_E")


# ---------- 選ぶ（テストしやすいよう純粋な関数にする） ----------

def pick_app_info(infos: list[dict]) -> dict:
    if not infos:
        raise AscError("appInfos がありません")
    for info in infos:
        attrs = info.get("attributes", {})
        state = attrs.get("state") or attrs.get("appStoreState")
        if state not in LIVE_INFO_STATES:
            return info
    return infos[0]


def pick_editable_version(versions: list[dict]) -> dict | None:
    for version in versions:
        attrs = version.get("attributes", {})
        state = attrs.get("appVersionState") or attrs.get("appStoreState")
        if state in EDITABLE_VERSION_STATES or (state or "").startswith("PREPARE_FOR_SUBMISSION"):
            return version
    return None


def find_localization(items: list[dict], locale: str) -> dict | None:
    want = locale.lower()
    for item in items:
        if item.get("attributes", {}).get("locale", "").lower() == want:
            return item
    return None


LIMITS = {"name": 30, "subtitle": 30, "keywords": 100, "promotionalText": 170, "description": 4000, "whatToTest": 4000}
LOCALE_KEY = __import__("re").compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


def other_locales(meta: dict) -> dict:
    """app-store.json の "en-US": {...} のような、ほかの言語の情報"""
    return {k: v for k, v in meta.items() if isinstance(v, dict) and LOCALE_KEY.match(k) and k != meta.get("locale")}


def load_metadata(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    for where, block in [("", meta)] + [(f"{k}.", v) for k, v in other_locales(meta).items()]:
        for key, limit in LIMITS.items():
            if key in block and block[key] is not None and len(block[key]) > limit:
                raise AscError(f"{path} の {where}{key} が {limit} 文字を超えています（{len(block[key])}）")
    if not meta.get("locale"):
        raise AscError(f"{path} に locale がありません")
    return meta


def compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "")}


# ---------- 操作 ----------

def find_app(client: Client, bundle_id: str) -> dict:
    apps = client.get("/v1/apps", **{q("filter[bundleId]"): bundle_id, "limit": 2}).get("data", [])
    apps = [a for a in apps if a.get("attributes", {}).get("bundleId") == bundle_id]
    if not apps:
        raise AscError(f"App Store Connect に {bundle_id} の App がありません（App の追加は本人が行う）")
    return apps[0]


def wait_for_build(client: Client, app_id: str, build_number: str, wait_minutes: int, sleep=time.sleep) -> dict:
    deadline = time.time() + wait_minutes * 60
    while True:
        params = {q("filter[app]"): app_id, "limit": 1, "sort": "-uploadedDate"}
        if build_number != "latest":
            params[q("filter[version]")] = build_number
        builds = client.get("/v1/builds", **params).get("data", [])
        if builds:
            build = builds[0]
            state = build["attributes"].get("processingState")
            print(f"build {build['attributes'].get('version')}: {state}")
            if state == "VALID":
                return build
            if state in ("FAILED", "INVALID"):
                raise AscError(f"ビルドの処理が {state} になりました（App Store Connect のメールを確かめてください）")
        else:
            print("build: まだ見つかりません")
        if time.time() > deadline:
            raise AscError(f"{wait_minutes} 分待ってもビルドの処理が終わりませんでした")
        sleep(30)


def ensure_internal_group(client: Client, app_id: str, name: str) -> dict:
    groups = client.get_all(f"/v1/apps/{app_id}/betaGroups", limit=200)
    for group in groups:
        attrs = group.get("attributes", {})
        if attrs.get("name") == name and attrs.get("isInternalGroup"):
            return group
    print(f"内部テストのグループ「{name}」を作ります（全ビルドを自動で配信）")
    created = client.request("POST", "/v1/betaGroups", {"data": {
        "type": "betaGroups",
        "attributes": {"name": name, "isInternalGroup": True, "hasAccessToAllBuilds": True},
        "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
    }})
    return created["data"]


def ensure_team_testers(client: Client, group_id: str) -> list[str]:
    """チームのユーザー（App Store Connect のユーザー）を内部テスターに加える。加えた人数の報告用にメールを返さない（個人情報）。"""
    current = {t.get("attributes", {}).get("email", "").lower()
               for t in client.get_all(f"/v1/betaGroups/{group_id}/betaTesters", limit=200)}
    added = []
    for user in client.get_all("/v1/users", limit=200):
        attrs = user.get("attributes", {})
        email = (attrs.get("username") or "").strip()
        if not email or "@" not in email or email.lower() in current:
            continue
        existing = client.get("/v1/betaTesters", **{q("filter[email]"): email, "limit": 1}).get("data", [])
        if existing:
            client.request("POST", f"/v1/betaGroups/{group_id}/relationships/betaTesters",
                           {"data": [{"type": "betaTesters", "id": existing[0]["id"]}]}, ok_conflict=True)
        else:
            client.request("POST", "/v1/betaTesters", {"data": {
                "type": "betaTesters",
                "attributes": compact({"email": email, "firstName": attrs.get("firstName"), "lastName": attrs.get("lastName")}),
                "relationships": {"betaGroups": {"data": [{"type": "betaGroups", "id": group_id}]}},
            }}, ok_conflict=True)
        added.append(user["id"])
    return added


def set_what_to_test(client: Client, build_id: str, locale: str, text: str) -> None:
    items = client.get_all(f"/v1/builds/{build_id}/betaBuildLocalizations", limit=50)
    item = find_localization(items, locale)
    if item:
        client.request("PATCH", f"/v1/betaBuildLocalizations/{item['id']}", {"data": {
            "type": "betaBuildLocalizations", "id": item["id"], "attributes": {"whatsNew": text}}})
    else:
        client.request("POST", "/v1/betaBuildLocalizations", {"data": {
            "type": "betaBuildLocalizations",
            "attributes": {"locale": locale, "whatsNew": text},
            "relationships": {"build": {"data": {"type": "builds", "id": build_id}}},
        }})


def distribute(client: Client, args) -> list[str]:
    app = find_app(client, args.bundle_id)
    build = wait_for_build(client, app["id"], args.build_number, args.wait_minutes)
    report = [f"ビルド {build['attributes'].get('version')} の処理が終わった（VALID）"]
    if build["attributes"].get("usesNonExemptEncryption") is None:
        # Info.plist の ITSAppUsesNonExemptEncryption = NO と同じ答え（標準の暗号だけ）
        client.request("PATCH", f"/v1/builds/{build['id']}", {"data": {
            "type": "builds", "id": build["id"], "attributes": {"usesNonExemptEncryption": False}}})
        report.append("輸出コンプライアンス：標準の暗号だけ（NO）と答えた")
    if args.metadata:
        meta = load_metadata(args.metadata)
        if meta.get("whatToTest"):
            set_what_to_test(client, build["id"], meta["locale"], meta["whatToTest"])
            report.append("テストの内容を書いた")
    group = ensure_internal_group(client, app["id"], args.group_name)
    added = ensure_team_testers(client, group["id"])
    report.append(f"内部テストのグループ「{args.group_name}」（テスターを {len(added)} 人追加）")
    if not group.get("attributes", {}).get("hasAccessToAllBuilds"):
        client.request("POST", f"/v1/betaGroups/{group['id']}/relationships/builds",
                       {"data": [{"type": "builds", "id": build["id"]}]}, ok_conflict=True)
        report.append("ビルドをグループに入れた")
    else:
        report.append("グループは全ビルドを自動で配信する設定")
    return report


def metadata(client: Client, args) -> list[str]:
    meta = load_metadata(args.metadata)
    locale = meta["locale"]
    app = find_app(client, args.bundle_id)
    app_id = app["id"]
    report = []
    primary = app.get("attributes", {}).get("primaryLocale")
    if primary and primary.lower() != locale.lower():
        report.append(f"注意：App の主言語は {primary}。{locale} の情報だけ入れた（主言語の情報は本人が確かめる）")

    info = pick_app_info(client.get_all(f"/v1/apps/{app_id}/appInfos", limit=10))
    relationships = {}
    for key, rel in (("primaryCategory", "primaryCategory"), ("secondaryCategory", "secondaryCategory")):
        if meta.get(key):
            relationships[rel] = {"data": {"type": "appCategories", "id": meta[key]}}
    if relationships:
        client.request("PATCH", f"/v1/appInfos/{info['id']}", {"data": {
            "type": "appInfos", "id": info["id"], "relationships": relationships}})
        report.append("カテゴリを入れた")

    info_locs = client.get_all(f"/v1/appInfos/{info['id']}/appInfoLocalizations", limit=50)

    def put_info(loc_code: str, block: dict) -> None:
        attrs = compact({"name": block.get("name"), "subtitle": block.get("subtitle"),
                         "privacyPolicyUrl": block.get("privacyPolicyUrl") or meta.get("privacyPolicyUrl")})
        loc = find_localization(info_locs, loc_code)
        if loc:
            client.request("PATCH", f"/v1/appInfoLocalizations/{loc['id']}", {"data": {
                "type": "appInfoLocalizations", "id": loc["id"], "attributes": attrs}})
        else:
            client.request("POST", "/v1/appInfoLocalizations", {"data": {
                "type": "appInfoLocalizations", "attributes": {"locale": loc_code, **attrs},
                "relationships": {"appInfo": {"data": {"type": "appInfos", "id": info["id"]}}}}})

    put_info(locale, meta)
    report.append("アプリ名・サブタイトル・プライバシーポリシー URL を入れた")
    for loc_code, block in other_locales(meta).items():
        put_info(loc_code, block)
        report.append(f"{loc_code}：アプリ名・サブタイトルを入れた")

    versions = client.get_all(f"/v1/apps/{app_id}/appStoreVersions", **{q("filter[platform]"): "IOS", "limit": 20})
    version = pick_editable_version(versions)
    if version is None:
        created = client.request("POST", "/v1/appStoreVersions", {"data": {
            "type": "appStoreVersions", "attributes": {"platform": "IOS", "versionString": args.version},
            "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}}})
        version = created["data"]
        report.append(f"提出用のバージョン {args.version} を作った")

    vlocs = client.get_all(f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations", limit=50)

    def put_version(loc_code: str, block: dict) -> None:
        attrs = compact({
            "description": block.get("description"), "keywords": block.get("keywords"),
            "supportUrl": block.get("supportUrl") or meta.get("supportUrl"),
            "marketingUrl": block.get("marketingUrl") or meta.get("marketingUrl"),
            "promotionalText": block.get("promotionalText"),
            # アップデートの「このバージョンの新機能」（はじめての版には入れられないので、書いたときだけ）
            "whatsNew": block.get("whatsNew"),
        })
        vloc = find_localization(vlocs, loc_code)
        if vloc:
            client.request("PATCH", f"/v1/appStoreVersionLocalizations/{vloc['id']}", {"data": {
                "type": "appStoreVersionLocalizations", "id": vloc["id"], "attributes": attrs}})
        else:
            client.request("POST", "/v1/appStoreVersionLocalizations", {"data": {
                "type": "appStoreVersionLocalizations", "attributes": {"locale": loc_code, **attrs},
                "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": version["id"]}}}}})

    put_version(locale, meta)
    report.append("説明・キーワード・サポート URL・宣伝文を入れた")
    for loc_code, block in other_locales(meta).items():
        if block.get("description"):
            put_version(loc_code, block)
            report.append(f"{loc_code}：説明・キーワード・宣伝文を入れた")

    if args.attach_latest_build:
        builds = client.get("/v1/builds", **{q("filter[app]"): app_id, q("filter[processingState]"): "VALID",
                                              "sort": "-uploadedDate", "limit": 1}).get("data", [])
        if builds:
            client.request("PATCH", f"/v1/appStoreVersions/{version['id']}/relationships/build",
                           {"data": {"type": "builds", "id": builds[0]["id"]}})
            report.append(f"提出用のバージョンにビルド {builds[0]['attributes'].get('version')} を選んだ（提出はしていない）")
        else:
            report.append("処理済みのビルドがないので、ビルドは選んでいない")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("distribute")
    d.add_argument("--bundle-id", required=True)
    d.add_argument("--build-number", required=True)
    d.add_argument("--group-name", default="daily-internal")
    d.add_argument("--metadata", default="")
    d.add_argument("--wait-minutes", type=int, default=45)
    m = sub.add_parser("metadata")
    m.add_argument("--bundle-id", required=True)
    m.add_argument("--metadata", required=True)
    m.add_argument("--version", default="1.0.0")
    m.add_argument("--attach-latest-build", action="store_true")
    args = p.parse_args(argv)

    key_id, issuer, key_path = (os.environ.get(k, "") for k in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_PATH"))
    if not (key_id and issuer and key_path and os.path.exists(key_path)):
        print("::error::ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH が要ります", file=sys.stderr)
        return 2
    client = Client(lambda: make_token(key_id, issuer, key_path))
    try:
        report = distribute(client, args) if args.command == "distribute" else metadata(client, args)
    except AscError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [f"### App Store Connect（{args.command}）"] + [f"- {r}" for r in report]
    print("\n".join(lines))
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
