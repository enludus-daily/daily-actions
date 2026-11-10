"""store.py のテスト（ネットワークに出ない。python3 asc/test_store.py）"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import asc  # noqa: E402
import store  # noqa: E402
from test_asc import Args, FakeApi, FakeResponse  # noqa: E402

APP = {("GET", "/v1/apps?"): {"data": [{"id": "app1", "attributes": {"bundleId": "com.x.006"}}]}}
VERSION = {("GET", "/v1/apps/app1/appStoreVersions"): {"data": [{"id": "v1", "attributes": {"appStoreState": "PREPARE_FOR_SUBMISSION", "versionString": "1.0.0"}}], "links": {}}}


def products_file(items):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"products": items}, f)
    f.close()
    return f.name


PRO = {"type": "NON_CONSUMABLE", "productId": "com.x.006.pro", "referenceName": "Pro", "price": 480,
       "localizations": [{"locale": "ja", "name": "Pro", "description": "ぜんぶ"}]}


class SubmitTests(unittest.TestCase):
    def routes(self, extra=None):
        r = {**APP, **VERSION,
             ("GET", "/v1/appStoreVersions/v1/build"): {"data": {"id": "b1", "attributes": {"version": "202611100602"}}},
             ("GET", "/v1/apps/app1/inAppPurchasesV2"): {"data": [{"id": "iap1", "attributes": {"state": "READY_TO_SUBMIT"}}], "links": {}},
             ("POST", "/v1/inAppPurchaseSubmissions"): {"data": {"id": "s"}},
             ("GET", "/v1/reviewSubmissions/rs1/items"): {"data": [], "links": {}},
             ("GET", "/v1/reviewSubmissions?"): {"data": [], "links": {}},
             ("POST", "/v1/reviewSubmissions"): {"data": {"id": "rs1", "attributes": {"state": "READY_FOR_REVIEW"}}},
             ("POST", "/v1/reviewSubmissionItems"): {"data": {"id": "it1"}},
             ("PATCH", "/v1/reviewSubmissions/rs1"): {"data": {"id": "rs1"}}}
        r.update(extra or {})
        return r

    def test_submits_version_and_ready_purchases(self):
        path = products_file([PRO])
        try:
            api = FakeApi(self.routes())
            report = store.submit(asc.Client(lambda: "t", opener=api),
                                  Args(bundle_id="com.x.006", confirm_version="1.0.0", products=path))
        finally:
            os.unlink(path)
        calls = [(m, p.split("?")[0]) for m, p, _ in api.calls]
        self.assertIn(("POST", "/v1/inAppPurchaseSubmissions"), calls)
        self.assertIn(("POST", "/v1/reviewSubmissionItems"), calls)
        patch = next(b for m, p, b in api.calls if m == "PATCH")
        self.assertTrue(patch["data"]["attributes"]["submitted"])
        item = next(b for m, p, b in api.calls if p == "/v1/reviewSubmissionItems")
        self.assertEqual(item["data"]["relationships"]["appStoreVersion"]["data"]["id"], "v1")
        self.assertIn("審査に提出した", report)

    def test_refuses_a_different_version(self):
        api = FakeApi(self.routes())
        with self.assertRaises(asc.AscError) as ctx:
            store.submit(asc.Client(lambda: "t", opener=api), Args(bundle_id="com.x.006", confirm_version="2.0.0", products=""))
        self.assertIn("1.0.0", str(ctx.exception))
        self.assertFalse(any(m in ("POST", "PATCH") for m, _, _ in api.calls))

    def test_needs_a_build(self):
        api = FakeApi(self.routes({("GET", "/v1/appStoreVersions/v1/build"): {"data": None}}))
        with self.assertRaises(asc.AscError):
            store.submit(asc.Client(lambda: "t", opener=api), Args(bundle_id="com.x.006", confirm_version="1.0.0", products=""))

    def test_purchase_missing_metadata_stops(self):
        path = products_file([PRO])
        try:
            api = FakeApi(self.routes({("GET", "/v1/apps/app1/inAppPurchasesV2"): {"data": [{"id": "iap1", "attributes": {"state": "MISSING_METADATA"}}], "links": {}}}))
            with self.assertRaises(asc.AscError):
                store.submit(asc.Client(lambda: "t", opener=api), Args(bundle_id="com.x.006", confirm_version="1.0.0", products=path))
        finally:
            os.unlink(path)
        self.assertFalse(any(m == "PATCH" for m, _, _ in api.calls))

    def test_reuses_an_open_submission(self):
        api = FakeApi(self.routes({
            ("GET", "/v1/reviewSubmissions?"): {"data": [{"id": "rs1", "attributes": {"state": "READY_FOR_REVIEW"}}], "links": {}},
            ("GET", "/v1/reviewSubmissions/rs1/items"): {"data": [{"id": "it0", "relationships": {"appStoreVersion": {"data": {"id": "v1"}}}}], "links": {}},
        }))
        store.submit(asc.Client(lambda: "t", opener=api), Args(bundle_id="com.x.006", confirm_version="1.0.0", products=""))
        calls = [(m, p.split("?")[0]) for m, p, _ in api.calls]
        self.assertNotIn(("POST", "/v1/reviewSubmissions"), calls)
        self.assertNotIn(("POST", "/v1/reviewSubmissionItems"), calls)
        self.assertIn(("PATCH", "/v1/reviewSubmissions/rs1"), calls)


class ScreenshotTests(unittest.TestCase):
    def test_replaces_screenshots_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("02-b.png", "01-a.png"):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(b"\x89PNG" + name.encode())
            uploads = []

            class Api(FakeApi):
                def __call__(self, req, timeout=0):
                    if req.full_url.startswith("https://upload.example"):
                        uploads.append(req.data)
                        return FakeResponse(b"")
                    return super().__call__(req, timeout)

            n = {"i": 0}

            def reserve():
                n["i"] += 1
                return {"data": {"id": f"shot{n['i']}", "attributes": {"uploadOperations": [
                    {"method": "PUT", "url": "https://upload.example/x", "offset": 0, "length": 100, "requestHeaders": []}]}}}

            routes = {**APP, **VERSION,
                      ("GET", "/v1/appStoreVersions/v1/appStoreVersionLocalizations"): {"data": [{"id": "vl1", "attributes": {"locale": "ja"}}], "links": {}},
                      ("GET", "/v1/appStoreVersionLocalizations/vl1/appScreenshotSets"): {"data": [], "links": {}},
                      ("POST", "/v1/appScreenshotSets"): {"data": {"id": "set1"}},
                      ("GET", "/v1/appScreenshotSets/set1/appScreenshots"): {"data": [{"id": "old1"}], "links": {}},
                      ("DELETE", "/v1/appScreenshots/old1"): {},
                      ("PATCH", "/v1/appScreenshots/"): {}}
            api = Api(routes)
            original = api.__call__

            def call(req, timeout=0):
                if req.get_method() == "POST" and req.full_url.endswith("/v1/appScreenshots"):
                    api.calls.append(("POST", "/v1/appScreenshots", json.loads(req.data)))
                    return FakeResponse(json.dumps(reserve()).encode())
                return original(req, timeout)

            report = store.screenshots(asc.Client(lambda: "t", opener=call),
                                       Args(bundle_id="com.x.006", dir=d, display_type="APP_IPHONE_67", locale="ja"))
        names = [b["data"]["attributes"]["fileName"] for m, p, b in api.calls if p == "/v1/appScreenshots"]
        self.assertEqual(names, ["01-a.png", "02-b.png"])
        self.assertTrue(any(m == "DELETE" for m, _, _ in api.calls))
        self.assertEqual(len(uploads), 2)
        self.assertIn("2 枚を入れた", report[0])

    def test_rejects_unknown_display_type(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(asc.AscError):
                store.screenshots(asc.Client(lambda: "t", opener=FakeApi({})),
                                  Args(bundle_id="x", dir=d, display_type="IPAD", locale="ja"))


if __name__ == "__main__":
    unittest.main()
