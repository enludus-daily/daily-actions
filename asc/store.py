#!/usr/bin/env python3
"""審査への提出と、ストアのスクリーンショットのアップロード（2026-11-10 本人の許可で追加）。

使い方（環境変数 ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH が要る）:
  store.py submit --bundle-id com.enludus.daily.006 --confirm-version 1.0.0 [--products docs/products.json]
      提出用のバージョン（--confirm-version と同じ版のときだけ）を審査に出す。
      products.json の App 内課金で「提出の準備ができた」ものは、いっしょに審査に出す。
  store.py withdraw --bundle-id com.enludus.daily.006
      審査待ち・審査中のものを取り下げる（本人が「取り下げて」と言ったときだけ）。
  store.py screenshots --bundle-id ... --dir shots/ [--display-type APP_IPHONE_67] [--locale ja]
      提出用のバージョンの、その言語・その画面サイズのスクリーンショットを、dir の PNG（名前の順）に入れ替える。

提出の前に本人がすること（API がないもの）: App プライバシーの申告・年齢区分・審査用の連絡先。
初めての App 内課金は、バージョンのページの「App 内課金とサブスクリプション」で本人が付ける（API で出そうとすると STATE_ERROR になるので、そのときは飛ばしてバージョンを出す）。
足りないときは Apple のエラーをそのまま出して止まる（推測で埋めない）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import asc  # noqa: E402
from asc import AscError, q  # noqa: E402
import iap  # noqa: E402

SUBMITTABLE_REVIEW_STATES = {"READY_FOR_REVIEW", "UNRESOLVED_ISSUES"}
WITHDRAWABLE_REVIEW_STATES = {"WAITING_FOR_REVIEW", "IN_REVIEW", "UNRESOLVED_ISSUES"}
DISPLAY_TYPES = {"APP_IPHONE_67", "APP_IPHONE_65", "APP_IPHONE_61", "APP_IPHONE_55"}


def editable_version(client, app_id: str) -> dict:
    versions = client.get_all(f"/v1/apps/{app_id}/appStoreVersions", **{q("filter[platform]"): "IOS", "limit": 20})
    version = asc.pick_editable_version(versions)
    if version is None:
        raise AscError("提出用のバージョンがありません（metadata を先に実行する）")
    return version


# ---------- 提出 ----------

def submit_in_app_purchases(client, app_id: str, products_path: str, bundle_id: str) -> list[str]:
    report = []
    for p in iap.load_products(products_path, bundle_id):
        if p["type"] != "NON_CONSUMABLE":
            report.append(f"{p['productId']}：サブスクの提出はまだ作っていない（本人が App Store Connect で選ぶ）")
            continue
        found = client.get_all(f"/v1/apps/{app_id}/inAppPurchasesV2", **{q("filter[productId]"): p["productId"], "limit": 10})
        if not found:
            raise AscError(f"{p['productId']} が App Store Connect にありません（products を先に実行する）")
        item = found[0]
        state = item.get("attributes", {}).get("state")
        if state == "READY_TO_SUBMIT":
            try:
                client.request("POST", "/v1/inAppPurchaseSubmissions", {"data": {
                    "type": "inAppPurchaseSubmissions",
                    "relationships": {"inAppPurchaseV2": iap.rel("inAppPurchases", item["id"])}}})
                report.append(f"{p['productId']}：審査に出した（アプリのバージョンといっしょに見てもらう）")
            except AscError as e:
                # 初めての App 内課金は API では出せない（バージョンのページで本人が付け、バージョンといっしょに審査される）
                if "STATE_ERROR" not in str(e):
                    raise
                report.append(f"{p['productId']}：API では出せない状態（初めての App 内課金は、バージョンのページで付けたものがいっしょに審査される）")
        elif state in ("MISSING_METADATA", "DEVELOPER_ACTION_NEEDED", "REJECTED"):
            raise AscError(f"{p['productId']} は {state}。表示名・価格・審査用の画像を確かめる（products を実行し直す）")
        else:
            report.append(f"{p['productId']}：{state}（出し直さない）")
    return report


def open_review_submission(client, app_id: str) -> dict:
    existing = client.get_all("/v1/reviewSubmissions", **{q("filter[app]"): app_id, q("filter[platform]"): "IOS", "limit": 20})
    for s in existing:
        if s.get("attributes", {}).get("state") in SUBMITTABLE_REVIEW_STATES:
            return s
    created = client.request("POST", "/v1/reviewSubmissions", {"data": {
        "type": "reviewSubmissions", "attributes": {"platform": "IOS"},
        "relationships": {"app": iap.rel("apps", app_id)}}})
    return created["data"]


def submit(client, args) -> list[str]:
    app = asc.find_app(client, args.bundle_id)
    version = editable_version(client, app["id"])
    version_string = version.get("attributes", {}).get("versionString")
    if version_string != args.confirm_version:
        raise AscError(f"提出用のバージョンは {version_string}。--confirm-version が {args.confirm_version} なので出さない")
    build = iap.get_data(client, f"/v1/appStoreVersions/{version['id']}/build")
    if not build:
        raise AscError("提出用のバージョンにビルドが選ばれていません（metadata を先に実行する）")
    report = [f"バージョン {version_string}（ビルド {build.get('attributes', {}).get('version')}）"]
    if args.products and os.path.exists(args.products):
        report += submit_in_app_purchases(client, app["id"], args.products, args.bundle_id)

    sub = open_review_submission(client, app["id"])
    items = client.get_all(f"/v1/reviewSubmissions/{sub['id']}/items", limit=50)
    has_version = any((i.get("relationships", {}).get("appStoreVersion", {}).get("data") or {}).get("id") == version["id"] for i in items)
    if not has_version:
        client.request("POST", "/v1/reviewSubmissionItems", {"data": {
            "type": "reviewSubmissionItems",
            "relationships": {"reviewSubmission": iap.rel("reviewSubmissions", sub["id"]),
                              "appStoreVersion": iap.rel("appStoreVersions", version["id"])}}})
    client.request("PATCH", f"/v1/reviewSubmissions/{sub['id']}", {"data": {
        "type": "reviewSubmissions", "id": sub["id"], "attributes": {"submitted": True}}})
    report.append("審査に提出した")
    return report


# ---------- 取り下げ ----------

def withdraw(client, args) -> list[str]:
    """審査に出しているもの（審査待ち・審査中）を取り下げる。取り下げたバージョンは、また編集・提出できる"""
    app = asc.find_app(client, args.bundle_id)
    subs = client.get_all("/v1/reviewSubmissions", **{q("filter[app]"): app["id"], q("filter[platform]"): "IOS", "limit": 20})
    targets = [s for s in subs if s.get("attributes", {}).get("state") in WITHDRAWABLE_REVIEW_STATES]
    if not targets:
        states = sorted({s.get("attributes", {}).get("state") for s in subs})
        return [f"取り下げるものはない（審査の状態: {states or 'なし'}）"]
    report = []
    for s in targets:
        client.request("PATCH", f"/v1/reviewSubmissions/{s['id']}", {"data": {
            "type": "reviewSubmissions", "id": s["id"], "attributes": {"canceled": True}}})
        report.append(f"審査を取り下げた（{s.get('attributes', {}).get('state')} → 取り下げ中）")
    return report


# ---------- スクリーンショット ----------

def list_pngs(directory: str) -> list[str]:
    files = sorted(f for f in os.listdir(directory) if f.lower().endswith(".png"))
    if not files:
        raise AscError(f"{directory} に PNG がありません")
    if len(files) > 10:
        raise AscError(f"スクリーンショットは 10 枚まで（{len(files)} 枚ある）")
    return [os.path.join(directory, f) for f in files]


def screenshots(client, args) -> list[str]:
    if args.display_type not in DISPLAY_TYPES:
        raise AscError(f"display-type は {sorted(DISPLAY_TYPES)} のどれか")
    files = list_pngs(args.dir)
    app = asc.find_app(client, args.bundle_id)
    version = editable_version(client, app["id"])
    locs = client.get_all(f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations", limit=50)
    loc = asc.find_localization(locs, args.locale)
    if loc is None:
        raise AscError(f"{args.locale} の説明がありません（metadata を先に実行する）")
    sets = client.get_all(f"/v1/appStoreVersionLocalizations/{loc['id']}/appScreenshotSets", limit=50)
    shot_set = next((s for s in sets if s.get("attributes", {}).get("screenshotDisplayType") == args.display_type), None)
    if shot_set is None:
        shot_set = client.request("POST", "/v1/appScreenshotSets", {"data": {
            "type": "appScreenshotSets", "attributes": {"screenshotDisplayType": args.display_type},
            "relationships": {"appStoreVersionLocalization": iap.rel("appStoreVersionLocalizations", loc["id"])}}})["data"]
    old = client.get_all(f"/v1/appScreenshotSets/{shot_set['id']}/appScreenshots", limit=50)
    for o in old:
        client.request("DELETE", f"/v1/appScreenshots/{o['id']}")
    for path in files:
        iap.upload_screenshot(client, "appScreenshots", "appScreenshotSet", iap.rel("appScreenshotSets", shot_set["id"]), path)
    return [f"{args.locale} の {args.display_type}：{len(old)} 枚を消し、{len(files)} 枚を入れた"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--bundle-id", required=True)
    s.add_argument("--confirm-version", required=True)
    s.add_argument("--products", default="")
    w = sub.add_parser("withdraw")
    w.add_argument("--bundle-id", required=True)
    sh = sub.add_parser("screenshots")
    sh.add_argument("--bundle-id", required=True)
    sh.add_argument("--dir", required=True)
    sh.add_argument("--display-type", default="APP_IPHONE_67")
    sh.add_argument("--locale", default="ja")
    args = p.parse_args(argv)

    key_id, issuer, key_path = (os.environ.get(k, "") for k in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_PATH"))
    if not (key_id and issuer and key_path and os.path.exists(key_path)):
        print("::error::ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH が要ります", file=sys.stderr)
        return 2
    client = asc.Client(lambda: asc.make_token(key_id, issuer, key_path))
    try:
        report = {"submit": submit, "withdraw": withdraw, "screenshots": screenshots}[args.command](client, args)
    except AscError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    lines = [f"### App Store Connect（{args.command}）"] + [f"- {r}" for r in report]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
