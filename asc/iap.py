#!/usr/bin/env python3
"""App 内課金の商品を、docs/products.json のとおりに App Store Connect に作る（何度実行しても同じ結果）。

使い方（環境変数 ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH が要る）:
  iap.py products --bundle-id com.enludus.daily.002 --products docs/products.json

products.json:
  {"products": [
    {"type": "NON_CONSUMABLE", "productId": "<bundle>.pro", "referenceName": "Pro", "price": 480,
     "familySharable": false, "reviewNote": "...", "reviewScreenshot": "docs/iap/pro.png",
     "localizations": [{"locale": "ja", "name": "Pro", "description": "..."}]},
    {"type": "AUTO_RENEWABLE", "group": "Pro", "groupLocalizations": [{"locale": "ja", "name": "Pro"}],
     "productId": "<bundle>.sub.yearly", "referenceName": "Pro 年額", "period": "ONE_YEAR", "groupLevel": 1,
     "price": 2000, "localizations": [...], "reviewScreenshot": "docs/iap/paywall.png"}]}

- 価格は日本の円（base territory JPN）。ほかの国は Apple の換算（equalization）に任せる。
- 消耗型（ジェム・コイン）と非更新サブスクは作らない（サーバーで残高や期間を守れないため）。
- 作るのは「提出の準備」まで。審査への提出はしない。サブスクの価格は、まだ無いときだけ入れる（あとから変えるのは本人）。
API の形は Apple の OpenAPI 仕様（App Store Connect API 4.6）で確かめた。
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import asc  # noqa: E402
from asc import AscError, q  # noqa: E402

BASE_TERRITORY = "JPN"
ALLOWED_TYPES = {"NON_CONSUMABLE", "AUTO_RENEWABLE"}
PERIODS = {"ONE_WEEK", "ONE_MONTH", "TWO_MONTHS", "THREE_MONTHS", "SIX_MONTHS", "ONE_YEAR"}
NAME_LIMIT, DESCRIPTION_LIMIT = 30, 45


def rel(kind: str, id_: str) -> dict:
    return {"data": {"type": kind, "id": id_}}


def load_products(path: str, bundle_id: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        products = json.load(f).get("products") or []
    if not products:
        raise AscError(f"{path} に products がありません")
    for p in products:
        pid = p.get("productId", "")
        if p.get("type") not in ALLOWED_TYPES:
            raise AscError(f"{pid}: type は {sorted(ALLOWED_TYPES)} のどれか（消耗型・非更新サブスクは作らない）")
        if not pid.startswith(bundle_id + "."):
            raise AscError(f"{pid}: productId は {bundle_id}. で始める")
        if not isinstance(p.get("price"), int) or p["price"] <= 0:
            raise AscError(f"{pid}: price は円の正の整数")
        if p["type"] == "AUTO_RENEWABLE" and (p.get("period") not in PERIODS or not p.get("group")):
            raise AscError(f"{pid}: サブスクには group と period（{sorted(PERIODS)}）が要る")
        if not p.get("localizations"):
            raise AscError(f"{pid}: localizations が要る")
        for loc in p["localizations"]:
            if len(loc.get("name", "")) > NAME_LIMIT or len(loc.get("description", "")) > DESCRIPTION_LIMIT:
                raise AscError(f"{pid} {loc.get('locale')}: 名前は {NAME_LIMIT} 文字、説明は {DESCRIPTION_LIMIT} 文字まで")
    return products


def pick_price_point(points: list[dict], price: int) -> dict:
    for point in points:
        if float(point.get("attributes", {}).get("customerPrice", "nan")) == float(price):
            return point
    near = sorted({int(float(x["attributes"]["customerPrice"])) for x in points if x.get("attributes", {}).get("customerPrice")},
                  key=lambda v: abs(v - price))[:5]
    raise AscError(f"¥{price} の価格がありません。近い価格: {near}")


def get_data(client, path: str, **params):
    """1つだけの関係（availability・screenshot など）は、無いと data が null か 404 になる。どちらも None にする"""
    try:
        return client.get(path, **params).get("data")
    except AscError as e:
        if "HTTP 404" in str(e):
            return None
        raise


def all_territories(client) -> list[dict]:
    return [{"type": "territories", "id": t["id"]} for t in client.get_all("/v1/territories", limit=200)]


def sync_localizations(client, existing: list[dict], wanted: list[dict], create_path: str, kind: str, owner: dict) -> int:
    changed = 0
    for loc in wanted:
        attrs = {"name": loc["name"], "description": loc.get("description")}
        found = asc.find_localization(existing, loc["locale"])
        if found is None:
            client.request("POST", create_path, {"data": {"type": kind, "attributes": {**attrs, "locale": loc["locale"]}, "relationships": owner}})
            changed += 1
        elif any(found.get("attributes", {}).get(k) != v for k, v in attrs.items()):
            client.request("PATCH", f"{create_path}/{found['id']}", {"data": {"type": kind, "id": found["id"], "attributes": attrs}})
            changed += 1
    return changed


def upload_screenshot(client, kind: str, owner_rel: str, owner: dict, path: str) -> None:
    """予約 → 分割して PUT → uploaded と MD5 を PATCH（Apple のアップロードの手順）"""
    with open(path, "rb") as f:
        data = f.read()
    res = client.request("POST", f"/v1/{kind}", {"data": {"type": kind, "attributes": {"fileName": os.path.basename(path), "fileSize": len(data)},
                                                        "relationships": {owner_rel: owner}}})
    shot = res["data"]
    for op in shot.get("attributes", {}).get("uploadOperations") or []:
        part = data[op["offset"]:op["offset"] + op["length"]]
        req = urllib.request.Request(op["url"], data=part, method=op.get("method", "PUT"))
        for header in op.get("requestHeaders") or []:
            req.add_header(header["name"], header["value"])
        with client._open(req, timeout=120) as r:  # 署名つきの URL。API の鍵は付けない
            r.read()
    client.request("PATCH", f"/v1/{kind}/{shot['id']}", {"data": {"type": kind, "id": shot["id"],
                                                                 "attributes": {"uploaded": True, "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})


def ensure_in_app_purchase(client, app_id: str, p: dict, base_dir: str, territories) -> str:
    found = client.get(f"/v1/apps/{app_id}/inAppPurchasesV2", **{q("filter[productId]"): p["productId"], "limit": 1}).get("data", [])
    if found:
        iap_id, note = found[0]["id"], "あり"
    else:
        attrs = {"name": p.get("referenceName") or p["productId"], "productId": p["productId"], "inAppPurchaseType": "NON_CONSUMABLE",
                 "familySharable": bool(p.get("familySharable")), "reviewNote": p.get("reviewNote")}
        iap_id = client.request("POST", "/v2/inAppPurchases", {"data": {"type": "inAppPurchases", "attributes": asc.compact(attrs),
                                                                         "relationships": {"app": rel("apps", app_id)}}})["data"]["id"]
        note = "作成"
    owner = {"inAppPurchaseV2": rel("inAppPurchases", iap_id)}
    locs = client.get_all(f"/v2/inAppPurchases/{iap_id}/inAppPurchaseLocalizations", limit=50)
    changed = sync_localizations(client, locs, p["localizations"], "/v1/inAppPurchaseLocalizations", "inAppPurchaseLocalizations", owner)
    points = client.get_all(f"/v2/inAppPurchases/{iap_id}/pricePoints", **{q("filter[territory]"): BASE_TERRITORY, "limit": 200})
    point = pick_price_point(points, p["price"])
    client.request("POST", "/v1/inAppPurchasePriceSchedules", {
        "data": {"type": "inAppPurchasePriceSchedules", "relationships": {
            "inAppPurchase": rel("inAppPurchases", iap_id), "baseTerritory": rel("territories", BASE_TERRITORY),
            "manualPrices": {"data": [{"type": "inAppPurchasePrices", "id": "${price}"}]}}},
        "included": [{"type": "inAppPurchasePrices", "id": "${price}", "attributes": {"startDate": None},
                      "relationships": {"inAppPurchasePricePoint": rel("inAppPurchasePricePoints", point["id"])}}]})
    if not get_data(client, f"/v2/inAppPurchases/{iap_id}/inAppPurchaseAvailability"):
        client.request("POST", "/v1/inAppPurchaseAvailabilities", {"data": {"type": "inAppPurchaseAvailabilities", "attributes": {"availableInNewTerritories": True},
                                                                            "relationships": {"inAppPurchase": rel("inAppPurchases", iap_id), "availableTerritories": {"data": territories()}}}})
    shot = ""
    if p.get("reviewScreenshot") and not get_data(client, f"/v2/inAppPurchases/{iap_id}/appStoreReviewScreenshot"):
        upload_screenshot(client, "inAppPurchaseAppStoreReviewScreenshots", "inAppPurchaseV2", rel("inAppPurchases", iap_id), os.path.join(base_dir, p["reviewScreenshot"]))
        shot = "、審査用の画像"
    return f"{p['productId']}（買い切り・{note}）: ¥{p['price']}、表示名 {changed} 件を更新{shot}"


def ensure_group(client, app_id: str, p: dict) -> str:
    found = client.get(f"/v1/apps/{app_id}/subscriptionGroups", **{q("filter[referenceName]"): p["group"], "limit": 1}).get("data", [])
    group_id = found[0]["id"] if found else client.request("POST", "/v1/subscriptionGroups", {
        "data": {"type": "subscriptionGroups", "attributes": {"referenceName": p["group"]}, "relationships": {"app": rel("apps", app_id)}}})["data"]["id"]
    existing = client.get_all(f"/v1/subscriptionGroups/{group_id}/subscriptionGroupLocalizations", limit=50)
    for loc in p.get("groupLocalizations") or []:
        if asc.find_localization(existing, loc["locale"]) is None:
            client.request("POST", "/v1/subscriptionGroupLocalizations", {"data": {"type": "subscriptionGroupLocalizations", "attributes": asc.compact(loc),
                                                                                   "relationships": {"subscriptionGroup": rel("subscriptionGroups", group_id)}}})
    return group_id


def ensure_subscription(client, app_id: str, p: dict, base_dir: str, territories) -> str:
    group_id = ensure_group(client, app_id, p)
    found = client.get(f"/v1/subscriptionGroups/{group_id}/subscriptions", **{q("filter[productId]"): p["productId"], "limit": 1}).get("data", [])
    if found:
        sub_id, note = found[0]["id"], "あり"
    else:
        attrs = {"name": p.get("referenceName") or p["productId"], "productId": p["productId"], "subscriptionPeriod": p["period"],
                 "familySharable": bool(p.get("familySharable")), "reviewNote": p.get("reviewNote"), "groupLevel": p.get("groupLevel")}
        sub_id = client.request("POST", "/v1/subscriptions", {"data": {"type": "subscriptions", "attributes": asc.compact(attrs),
                                                                        "relationships": {"group": rel("subscriptionGroups", group_id)}}})["data"]["id"]
        note = "作成"
    owner = {"subscription": rel("subscriptions", sub_id)}
    locs = client.get_all(f"/v1/subscriptions/{sub_id}/subscriptionLocalizations", limit=50)
    changed = sync_localizations(client, locs, p["localizations"], "/v1/subscriptionLocalizations", "subscriptionLocalizations", owner)
    price_note = "価格は設定済み（変えるのは本人）"
    if not client.get(f"/v1/subscriptions/{sub_id}/prices", limit=1).get("data"):
        points = client.get_all(f"/v1/subscriptions/{sub_id}/pricePoints", **{q("filter[territory]"): BASE_TERRITORY, "limit": 200})
        point = pick_price_point(points, p["price"])
        targets = [(BASE_TERRITORY, point["id"])]
        for eq in client.get_all(f"/v1/subscriptionPricePoints/{point['id']}/equalizations", limit=200, include="territory"):
            territory = eq.get("relationships", {}).get("territory", {}).get("data", {}).get("id")
            if territory and territory != BASE_TERRITORY:
                targets.append((territory, eq["id"]))
        for territory, point_id in targets:
            client.request("POST", "/v1/subscriptionPrices", {"data": {"type": "subscriptionPrices", "attributes": {"startDate": None}, "relationships": {
                "subscription": rel("subscriptions", sub_id), "territory": rel("territories", territory),
                "subscriptionPricePoint": rel("subscriptionPricePoints", point_id)}}}, ok_conflict=True)
        price_note = f"¥{p['price']}（{len(targets)} の国と地域）"
    if not get_data(client, f"/v1/subscriptions/{sub_id}/subscriptionAvailability"):
        client.request("POST", "/v1/subscriptionAvailabilities", {"data": {"type": "subscriptionAvailabilities", "attributes": {"availableInNewTerritories": True},
                                                                           "relationships": {"subscription": rel("subscriptions", sub_id), "availableTerritories": {"data": territories()}}}})
    shot = ""
    if p.get("reviewScreenshot") and not get_data(client, f"/v1/subscriptions/{sub_id}/appStoreReviewScreenshot"):
        upload_screenshot(client, "subscriptionAppStoreReviewScreenshots", "subscription", rel("subscriptions", sub_id), os.path.join(base_dir, p["reviewScreenshot"]))
        shot = "、審査用の画像"
    return f"{p['productId']}（サブスク {p['period']}・{note}）: {price_note}、表示名 {changed} 件を更新{shot}"


def products(client, args) -> list[str]:
    items = load_products(args.products, args.bundle_id)
    app_id = asc.find_app(client, args.bundle_id)["id"]
    base_dir = args.base_dir or "."
    cache: list = []

    def territories():
        if not cache:
            cache.extend(all_territories(client))
        return cache

    report = []
    for p in items:
        fn = ensure_in_app_purchase if p["type"] == "NON_CONSUMABLE" else ensure_subscription
        report.append(fn(client, app_id, p, base_dir, territories))
    report.append("審査への提出はしていない（App 内課金は、初めてはアプリのバージョンと一緒に本人が出す）")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("products")
    pr.add_argument("--bundle-id", required=True)
    pr.add_argument("--products", default="docs/products.json")
    pr.add_argument("--base-dir", default=".")
    args = p.parse_args(argv)
    key_id, issuer, key_path = (os.environ.get(k) for k in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_PATH"))
    if not (key_id and issuer and key_path):
        print("ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH が要ります", file=sys.stderr)
        return 2
    client = asc.Client(lambda: asc.make_token(key_id, issuer, key_path))
    try:
        report = products(client, args)
    except AscError as e:
        print(f"::error::{e}")
        return 1
    lines = ["### App Store Connect（products）"] + [f"- {r}" for r in report]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
