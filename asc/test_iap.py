"""iap.py のテスト（ネットワークに出ない。python3 asc/test_iap.py）"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import asc  # noqa: E402
import iap  # noqa: E402
from test_asc import Args, FakeApi as _FakeApi, FakeResponse  # noqa: E402


class FakeApi(_FakeApi):
    """署名つき URL へのアップロード（生のバイト）も受ける"""

    def __call__(self, req, timeout=0):
        if not req.full_url.startswith(asc.API):
            self.calls.append((req.get_method(), req.full_url, req.data))
            if not req.full_url.startswith("https://upload.example/"):
                raise AssertionError(f"想定外の URL: {req.full_url}")
            if req.get_header("Authorization"):
                raise AssertionError("アップロード先に API の鍵を送っている")
            return FakeResponse(b"")
        return super().__call__(req, timeout)

BUNDLE = "com.x.002"
PNG = b"\x89PNG fake screenshot bytes"


def product(**kw):
    base = {"type": "NON_CONSUMABLE", "productId": f"{BUNDLE}.pro", "referenceName": "Pro", "price": 480,
            "localizations": [{"locale": "ja", "name": "Pro", "description": "すべての型と詳しい振り返り"}]}
    base.update(kw)
    return base


def write_products(tmp, items):
    path = os.path.join(tmp, "products.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"products": items}, f, ensure_ascii=False)
    return path


def points(*prices):
    return {"data": [{"id": f"pp{p}", "attributes": {"customerPrice": f"{p}.0"}} for p in prices], "links": {}}


APP = {("GET", "/v1/apps?"): {"data": [{"id": "app1", "attributes": {"bundleId": BUNDLE}}]}}
TERRITORIES = {("GET", "/v1/territories"): {"data": [{"id": "JPN"}, {"id": "USA"}], "links": {}}}


class LoadTests(unittest.TestCase):
    def check_rejects(self, item, needle):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(asc.AscError) as ctx:
                iap.load_products(write_products(tmp, [item]), BUNDLE)
            self.assertIn(needle, str(ctx.exception))

    def test_rejects_consumable(self):
        self.check_rejects(product(type="CONSUMABLE"), "消耗型")

    def test_rejects_other_bundle_prefix(self):
        self.check_rejects(product(productId="com.other.pro"), BUNDLE)

    def test_rejects_long_display_name(self):
        self.check_rejects(product(localizations=[{"locale": "ja", "name": "あ" * 31}]), "30")

    def test_subscription_needs_period_and_group(self):
        self.check_rejects(product(type="AUTO_RENEWABLE", productId=f"{BUNDLE}.sub.yearly"), "period")

    def test_accepts_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(iap.load_products(write_products(tmp, [product()]), BUNDLE)), 1)


class PricePointTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(iap.pick_price_point(points(400, 480, 500)["data"], 480)["id"], "pp480")

    def test_missing_price_lists_near_ones(self):
        with self.assertRaises(asc.AscError) as ctx:
            iap.pick_price_point(points(400, 500)["data"], 480)
        self.assertIn("400", str(ctx.exception))


class NonConsumableTests(unittest.TestCase):
    def run_products(self, routes, items):
        api = FakeApi(routes)
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "pro.png"), "wb") as f:
                f.write(PNG)
            report = iap.products(asc.Client(lambda: "token", opener=api),
                                  Args(bundle_id=BUNDLE, products=write_products(tmp, items), base_dir=tmp))
        return api, report

    def test_creates_product_price_availability_and_screenshot(self):
        routes = {**APP, **TERRITORIES,
                  ("GET", "/v1/apps/app1/inAppPurchasesV2"): {"data": []},
                  ("POST", "/v2/inAppPurchases"): {"data": {"id": "i1"}},
                  ("GET", "/v2/inAppPurchases/i1/inAppPurchaseLocalizations"): {"data": [], "links": {}},
                  ("POST", "/v1/inAppPurchaseLocalizations"): {"data": {"id": "l1"}},
                  ("GET", "/v2/inAppPurchases/i1/pricePoints"): points(400, 480),
                  ("POST", "/v1/inAppPurchasePriceSchedules"): {"data": {"id": "s1"}},
                  ("GET", "/v2/inAppPurchases/i1/inAppPurchaseAvailability"): 404,
                  ("POST", "/v1/inAppPurchaseAvailabilities"): {"data": {"id": "a1"}},
                  ("GET", "/v2/inAppPurchases/i1/appStoreReviewScreenshot"): {"data": None},
                  ("POST", "/v1/inAppPurchaseAppStoreReviewScreenshots"): {"data": {"id": "sh1", "attributes": {"uploadOperations": [
                      {"method": "PUT", "url": "https://upload.example/part1", "offset": 0, "length": len(PNG),
                       "requestHeaders": [{"name": "Content-Type", "value": "image/png"}]}]}}},
                  ("PATCH", "/v1/inAppPurchaseAppStoreReviewScreenshots/sh1"): {}}
        api, report = self.run_products(routes, [product(reviewScreenshot="docs/pro.png", familySharable=True)])
        body = {(m, p.split("?")[0]): b for m, p, b in api.calls}
        created = body[("POST", "/v2/inAppPurchases")]["data"]["attributes"]
        self.assertEqual(created["inAppPurchaseType"], "NON_CONSUMABLE")
        self.assertTrue(created["familySharable"])
        schedule = body[("POST", "/v1/inAppPurchasePriceSchedules")]
        self.assertEqual(schedule["data"]["relationships"]["baseTerritory"]["data"]["id"], "JPN")
        manual_id = schedule["data"]["relationships"]["manualPrices"]["data"][0]["id"]
        self.assertEqual(schedule["included"][0]["id"], manual_id)
        self.assertEqual(schedule["included"][0]["relationships"]["inAppPurchasePricePoint"]["data"]["id"], "pp480")
        territories = body[("POST", "/v1/inAppPurchaseAvailabilities")]["data"]["relationships"]["availableTerritories"]["data"]
        self.assertEqual([t["id"] for t in territories], ["JPN", "USA"])
        self.assertEqual(body[("PUT", "https://upload.example/part1")], PNG)  # 生のバイトを、鍵なしで送る
        patched = body[("PATCH", "/v1/inAppPurchaseAppStoreReviewScreenshots/sh1")]["data"]["attributes"]
        self.assertEqual(patched["sourceFileChecksum"], hashlib.md5(PNG).hexdigest())
        self.assertTrue(any("¥480" in r and "作成" in r for r in report))
        self.assertTrue(any("提出はしていない" in r for r in report))

    def test_existing_product_is_not_recreated(self):
        routes = {**APP,
                  ("GET", "/v1/apps/app1/inAppPurchasesV2"): {"data": [{"id": "i1"}]},
                  ("GET", "/v2/inAppPurchases/i1/inAppPurchaseLocalizations"): {"data": [{"id": "l1", "attributes": {
                      "locale": "ja", "name": "Pro", "description": "すべての型と詳しい振り返り"}}], "links": {}},
                  ("GET", "/v2/inAppPurchases/i1/pricePoints"): points(480),
                  ("POST", "/v1/inAppPurchasePriceSchedules"): {"data": {"id": "s1"}},
                  ("GET", "/v2/inAppPurchases/i1/inAppPurchaseAvailability"): {"data": {"id": "a1"}},
                  ("GET", "/v2/inAppPurchases/i1/appStoreReviewScreenshot"): {"data": {"id": "sh1"}}}
        api, report = self.run_products(routes, [product(reviewScreenshot="docs/pro.png")])
        posts = [p.split("?")[0] for m, p, _ in api.calls if m in ("POST", "PATCH")]
        self.assertEqual(posts, ["/v1/inAppPurchasePriceSchedules"])
        self.assertTrue(any("あり" in r and "0 件" in r for r in report))


class SubscriptionTests(unittest.TestCase):
    def test_creates_group_subscription_and_equalized_prices(self):
        sub = product(type="AUTO_RENEWABLE", productId=f"{BUNDLE}.sub.yearly", period="ONE_YEAR", group="Pro",
                      groupLocalizations=[{"locale": "ja", "name": "Pro"}], price=2000)
        routes = {**APP, **TERRITORIES,
                  ("GET", "/v1/apps/app1/subscriptionGroups"): {"data": []},
                  ("POST", "/v1/subscriptionGroups"): {"data": {"id": "g1"}},
                  ("GET", "/v1/subscriptionGroups/g1/subscriptionGroupLocalizations"): {"data": [], "links": {}},
                  ("POST", "/v1/subscriptionGroupLocalizations"): {"data": {"id": "gl1"}},
                  ("GET", "/v1/subscriptionGroups/g1/subscriptions"): {"data": []},
                  ("POST", "/v1/subscriptions"): {"data": {"id": "sub1"}},
                  ("GET", "/v1/subscriptions/sub1/subscriptionLocalizations"): {"data": [], "links": {}},
                  ("POST", "/v1/subscriptionLocalizations"): {"data": {"id": "sl1"}},
                  ("GET", "/v1/subscriptions/sub1/prices"): {"data": []},
                  ("GET", "/v1/subscriptions/sub1/pricePoints"): points(2000),
                  ("GET", "/v1/subscriptionPricePoints/pp2000/equalizations"): {"data": [
                      {"id": "eqUSA", "relationships": {"territory": {"data": {"type": "territories", "id": "USA"}}}},
                      {"id": "eqJPN", "relationships": {"territory": {"data": {"type": "territories", "id": "JPN"}}}}], "links": {}},
                  ("POST", "/v1/subscriptionPrices"): {"data": {"id": "p"}},
                  ("GET", "/v1/subscriptions/sub1/subscriptionAvailability"): {"data": None},
                  ("POST", "/v1/subscriptionAvailabilities"): {"data": {"id": "a1"}}}
        api = FakeApi(routes)
        with tempfile.TemporaryDirectory() as tmp:
            report = iap.products(asc.Client(lambda: "token", opener=api),
                                  Args(bundle_id=BUNDLE, products=write_products(tmp, [sub]), base_dir=tmp))
        created = next(b for m, p, b in api.calls if m == "POST" and p == "/v1/subscriptions")
        self.assertEqual(created["data"]["attributes"]["subscriptionPeriod"], "ONE_YEAR")
        prices = [(b["data"]["relationships"]["territory"]["data"]["id"], b["data"]["relationships"]["subscriptionPricePoint"]["data"]["id"])
                  for m, p, b in api.calls if m == "POST" and p == "/v1/subscriptionPrices"]
        self.assertEqual(prices, [("JPN", "pp2000"), ("USA", "eqUSA")])
        self.assertTrue(any("2 の国と地域" in r for r in report))

    def test_existing_prices_are_left_to_the_owner(self):
        sub = product(type="AUTO_RENEWABLE", productId=f"{BUNDLE}.sub.yearly", period="ONE_YEAR", group="Pro", price=2000)
        routes = {**APP,
                  ("GET", "/v1/apps/app1/subscriptionGroups"): {"data": [{"id": "g1"}]},
                  ("GET", "/v1/subscriptionGroups/g1/subscriptionGroupLocalizations"): {"data": [], "links": {}},
                  ("GET", "/v1/subscriptionGroups/g1/subscriptions"): {"data": [{"id": "sub1"}]},
                  ("GET", "/v1/subscriptions/sub1/subscriptionLocalizations"): {"data": [{"id": "sl1", "attributes": {
                      "locale": "ja", "name": "Pro", "description": "すべての型と詳しい振り返り"}}], "links": {}},
                  ("GET", "/v1/subscriptions/sub1/prices"): {"data": [{"id": "price1"}]},
                  ("GET", "/v1/subscriptions/sub1/subscriptionAvailability"): {"data": {"id": "a1"}}}
        api = FakeApi(routes)
        with tempfile.TemporaryDirectory() as tmp:
            report = iap.products(asc.Client(lambda: "token", opener=api),
                                  Args(bundle_id=BUNDLE, products=write_products(tmp, [sub]), base_dir=tmp))
        self.assertFalse([p for m, p, _ in api.calls if m in ("POST", "PATCH")])
        self.assertTrue(any("本人" in r for r in report))


if __name__ == "__main__":
    unittest.main()
