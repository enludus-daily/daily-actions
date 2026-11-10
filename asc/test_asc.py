"""asc.py のテスト（ネットワークに出ない。python3 -m unittest asc/test_asc.py）"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
import asc  # noqa: E402


def raw_to_der(raw: bytes) -> bytes:
    def integer(b: bytes) -> bytes:
        b = b.lstrip(b"\x00") or b"\x00"
        if b[0] & 0x80:
            b = b"\x00" + b
        return b"\x02" + bytes([len(b)]) + b
    body = integer(raw[:32]) + integer(raw[32:])
    return b"\x30" + bytes([len(body)]) + body


class TokenTests(unittest.TestCase):
    def test_der_round_trip(self):
        raw = bytes(range(1, 65))
        self.assertEqual(asc.der_to_raw(raw_to_der(raw)), raw)
        leading_zero = b"\x00" * 31 + b"\x05" + b"\xff" * 32
        self.assertEqual(asc.der_to_raw(raw_to_der(leading_zero)), leading_zero)

    def test_der_rejects_garbage(self):
        with self.assertRaises(asc.AscError):
            asc.der_to_raw(b"\x31\x00")

    def test_token_signature_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            key = os.path.join(d, "AuthKey_TEST.p8")
            pub = os.path.join(d, "pub.pem")
            subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key + ".ec"], check=True)
            subprocess.run(["openssl", "pkcs8", "-topk8", "-nocrypt", "-in", key + ".ec", "-out", key], check=True)
            subprocess.run(["openssl", "ec", "-in", key, "-pubout", "-out", pub], check=True, capture_output=True)
            token = asc.make_token("KEYID", "ISSUER", key, now=1_000)
            header_b64, payload_b64, sig_b64 = token.split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
            header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
            payload = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
            self.assertEqual(header, {"alg": "ES256", "kid": "KEYID", "typ": "JWT"})
            self.assertEqual(payload, {"iss": "ISSUER", "iat": 1000, "exp": 1900, "aud": "appstoreconnect-v1"})
            sig = base64.urlsafe_b64decode(pad(sig_b64))
            self.assertEqual(len(sig), 64)
            sig_path = os.path.join(d, "sig.der")
            with open(sig_path, "wb") as f:
                f.write(raw_to_der(sig))
            verify = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sig_path],
                input=f"{header_b64}.{payload_b64}".encode(), capture_output=True,
            )
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_bad_key_is_reported_without_contents(self):
        with tempfile.NamedTemporaryFile("w", suffix=".p8") as f:
            f.write("not a key")
            f.flush()
            with self.assertRaises(asc.AscError) as ctx:
                asc.make_token("K", "I", f.name)
            self.assertNotIn("not a key", str(ctx.exception))


class PickTests(unittest.TestCase):
    def test_pick_app_info_prefers_editable(self):
        infos = [
            {"id": "live", "attributes": {"state": "READY_FOR_DISTRIBUTION"}},
            {"id": "edit", "attributes": {"state": "PREPARE_FOR_SUBMISSION"}},
        ]
        self.assertEqual(asc.pick_app_info(infos)["id"], "edit")
        self.assertEqual(asc.pick_app_info(infos[:1])["id"], "live")
        self.assertEqual(asc.pick_app_info([{"id": "old", "attributes": {"appStoreState": "PREPARE_FOR_SUBMISSION"}}])["id"], "old")

    def test_pick_editable_version(self):
        versions = [
            {"id": "v1", "attributes": {"appVersionState": "READY_FOR_DISTRIBUTION"}},
            {"id": "v2", "attributes": {"appStoreState": "PREPARE_FOR_SUBMISSION"}},
        ]
        self.assertEqual(asc.pick_editable_version(versions)["id"], "v2")
        self.assertIsNone(asc.pick_editable_version(versions[:1]))

    def test_find_localization_ignores_case(self):
        items = [{"id": "a", "attributes": {"locale": "en-US"}}, {"id": "b", "attributes": {"locale": "ja"}}]
        self.assertEqual(asc.find_localization(items, "JA")["id"], "b")
        self.assertIsNone(asc.find_localization(items, "fr-FR"))

    def test_load_metadata_checks_limits(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"locale": "ja", "name": "あ" * 31}, f)
        try:
            with self.assertRaises(asc.AscError):
                asc.load_metadata(f.name)
        finally:
            os.unlink(f.name)

    def test_query_names(self):
        self.assertEqual(asc.q("filter[bundleId]"), "filter__bundleId_E")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeApi:
    """パスとメソッドで答えを返す偽の App Store Connect"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=0):
        path = req.full_url.replace(asc.API, "")
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), path, body))
        for (method, prefix), answer in self.routes.items():
            if req.get_method() == method and path.startswith(prefix):
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(req.full_url, answer, "err", {}, io.BytesIO(b'{"errors":[{"code":"X","detail":"d"}]}'))
                return FakeResponse(json.dumps(answer).encode())
        raise AssertionError(f"想定外の呼び出し: {req.get_method()} {path}")


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class DistributeTests(unittest.TestCase):
    def test_creates_group_adds_tester_and_answers_compliance(self):
        api = FakeApi({
            ("GET", "/v1/apps?"): {"data": [{"id": "app1", "attributes": {"bundleId": "com.x.001"}}]},
            ("GET", "/v1/builds?"): {"data": [{"id": "b1", "attributes": {"version": "202611040000", "processingState": "VALID", "usesNonExemptEncryption": None}}]},
            ("PATCH", "/v1/builds/b1"): {},
            ("GET", "/v1/apps/app1/betaGroups"): {"data": [], "links": {}},
            ("POST", "/v1/betaGroups"): {"data": {"id": "g1", "attributes": {"name": "daily-internal", "isInternalGroup": True, "hasAccessToAllBuilds": True}}},
            ("GET", "/v1/betaGroups/g1/betaTesters"): {"data": [], "links": {}},
            ("GET", "/v1/users"): {"data": [{"id": "u1", "attributes": {"username": "owner@example.com", "firstName": "O", "lastName": "W"}}], "links": {}},
            ("GET", "/v1/betaTesters?"): {"data": []},
            ("POST", "/v1/betaTesters"): {"data": {"id": "t1"}},
        })
        client = asc.Client(lambda: "token", opener=api)
        report = asc.distribute(client, Args(bundle_id="com.x.001", build_number="202611040000", wait_minutes=1, metadata="", group_name="daily-internal"))
        methods = [(m, p.split("?")[0]) for m, p, _ in api.calls]
        self.assertIn(("POST", "/v1/betaGroups"), methods)
        self.assertIn(("POST", "/v1/betaTesters"), methods)
        patch = next(b for m, p, b in api.calls if m == "PATCH")
        self.assertFalse(patch["data"]["attributes"]["usesNonExemptEncryption"])
        group_body = next(b for m, p, b in api.calls if m == "POST" and p == "/v1/betaGroups")
        self.assertTrue(group_body["data"]["attributes"]["isInternalGroup"])
        self.assertTrue(group_body["data"]["attributes"]["hasAccessToAllBuilds"])
        self.assertTrue(any("1 人" in r for r in report))
        self.assertFalse(any("owner@example.com" in r for r in report))

    def test_failed_processing_stops(self):
        api = FakeApi({
            ("GET", "/v1/apps?"): {"data": [{"id": "app1", "attributes": {"bundleId": "com.x.001"}}]},
            ("GET", "/v1/builds?"): {"data": [{"id": "b1", "attributes": {"version": "1", "processingState": "INVALID"}}]},
        })
        client = asc.Client(lambda: "token", opener=api)
        with self.assertRaises(asc.AscError):
            asc.distribute(client, Args(bundle_id="com.x.001", build_number="1", wait_minutes=1, metadata="", group_name="g"))

    def test_missing_app_is_a_clear_error(self):
        api = FakeApi({("GET", "/v1/apps?"): {"data": []}})
        client = asc.Client(lambda: "token", opener=api)
        with self.assertRaises(asc.AscError) as ctx:
            asc.find_app(client, "com.x.009")
        self.assertIn("com.x.009", str(ctx.exception))

    def test_http_error_message_has_detail_not_token(self):
        api = FakeApi({("GET", "/v1/apps?"): 403})
        client = asc.Client(lambda: "secret-token", opener=api)
        with self.assertRaises(asc.AscError) as ctx:
            asc.find_app(client, "com.x.001")
        self.assertIn("403", str(ctx.exception))
        self.assertNotIn("secret-token", str(ctx.exception))


class MetadataTests(unittest.TestCase):
    def test_updates_existing_localizations_and_creates_version(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"locale": "ja", "name": "名前", "subtitle": "サブ", "primaryCategory": "EDUCATION",
                       "description": "説明", "keywords": "a,b", "supportUrl": "https://example.com/s",
                       "privacyPolicyUrl": "https://example.com/p"}, f)
        api = FakeApi({
            ("GET", "/v1/apps?"): {"data": [{"id": "app1", "attributes": {"bundleId": "com.x.001", "primaryLocale": "ja"}}]},
            ("GET", "/v1/apps/app1/appInfos"): {"data": [{"id": "i1", "attributes": {"state": "PREPARE_FOR_SUBMISSION"}}], "links": {}},
            ("PATCH", "/v1/appInfos/i1"): {},
            ("GET", "/v1/appInfos/i1/appInfoLocalizations"): {"data": [{"id": "il1", "attributes": {"locale": "ja"}}], "links": {}},
            ("PATCH", "/v1/appInfoLocalizations/il1"): {},
            ("GET", "/v1/apps/app1/appStoreVersions"): {"data": [], "links": {}},
            ("POST", "/v1/appStoreVersions"): {"data": {"id": "v1"}},
            ("GET", "/v1/appStoreVersions/v1/appStoreVersionLocalizations"): {"data": [], "links": {}},
            ("POST", "/v1/appStoreVersionLocalizations"): {"data": {"id": "vl1"}},
        })
        try:
            client = asc.Client(lambda: "token", opener=api)
            report = asc.metadata(client, Args(bundle_id="com.x.001", metadata=f.name, version="1.0.0", attach_latest_build=False))
        finally:
            os.unlink(f.name)
        bodies = {(m, p.split("?")[0]): b for m, p, b in api.calls}
        self.assertEqual(bodies[("PATCH", "/v1/appInfoLocalizations/il1")]["data"]["attributes"],
                         {"name": "名前", "subtitle": "サブ", "privacyPolicyUrl": "https://example.com/p"})
        self.assertEqual(bodies[("PATCH", "/v1/appInfos/i1")]["data"]["relationships"]["primaryCategory"]["data"]["id"], "EDUCATION")
        self.assertEqual(bodies[("POST", "/v1/appStoreVersions")]["data"]["attributes"], {"platform": "IOS", "versionString": "1.0.0"})
        vloc = bodies[("POST", "/v1/appStoreVersionLocalizations")]["data"]["attributes"]
        self.assertEqual(vloc["locale"], "ja")
        self.assertEqual(vloc["keywords"], "a,b")
        self.assertNotIn("marketingUrl", vloc)
        self.assertTrue(report)


class OtherLocaleTests(unittest.TestCase):
    def test_writes_en_us_block(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"locale": "ja", "name": "名前", "description": "説明", "supportUrl": "https://example.com/s",
                       "privacyPolicyUrl": "https://example.com/p",
                       "en-US": {"name": "Name", "subtitle": "Sub", "keywords": "snake,pool", "description": "Desc", "promotionalText": "Promo"}}, f)
        api = FakeApi({
            ("GET", "/v1/apps?"): {"data": [{"id": "app1", "attributes": {"bundleId": "com.x.001", "primaryLocale": "ja"}}]},
            ("GET", "/v1/apps/app1/appInfos"): {"data": [{"id": "i1", "attributes": {"state": "PREPARE_FOR_SUBMISSION"}}], "links": {}},
            ("GET", "/v1/appInfos/i1/appInfoLocalizations"): {"data": [{"id": "il1", "attributes": {"locale": "ja"}}], "links": {}},
            ("PATCH", "/v1/appInfoLocalizations/il1"): {},
            ("POST", "/v1/appInfoLocalizations"): {"data": {"id": "il2"}},
            ("GET", "/v1/apps/app1/appStoreVersions"): {"data": [{"id": "v1", "attributes": {"appStoreState": "PREPARE_FOR_SUBMISSION"}}], "links": {}},
            ("GET", "/v1/appStoreVersions/v1/appStoreVersionLocalizations"): {"data": [{"id": "vl1", "attributes": {"locale": "ja"}}], "links": {}},
            ("PATCH", "/v1/appStoreVersionLocalizations/vl1"): {},
            ("POST", "/v1/appStoreVersionLocalizations"): {"data": {"id": "vl2"}},
        })
        try:
            report = asc.metadata(asc.Client(lambda: "t", opener=api), Args(bundle_id="com.x.001", metadata=f.name, version="1.1.0", attach_latest_build=False))
        finally:
            os.unlink(f.name)
        posts = {p: b for m, p, b in api.calls if m == "POST"}
        info = posts["/v1/appInfoLocalizations"]["data"]["attributes"]
        self.assertEqual(info["locale"], "en-US")
        self.assertEqual(info["name"], "Name")
        self.assertEqual(info["privacyPolicyUrl"], "https://example.com/p")
        ver = posts["/v1/appStoreVersionLocalizations"]["data"]["attributes"]
        self.assertEqual(ver["keywords"], "snake,pool")
        self.assertEqual(ver["supportUrl"], "https://example.com/s")
        self.assertTrue(any("en-US" in r for r in report))

    def test_limits_apply_to_other_locales(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"locale": "ja", "en-US": {"subtitle": "x" * 31}}, f)
        try:
            with self.assertRaises(asc.AscError):
                asc.load_metadata(f.name)
        finally:
            os.unlink(f.name)


class ErrorTextTests(unittest.TestCase):
    def test_associated_errors_are_shown(self):
        x = {"code": "STATE_ERROR.ENTITY_STATE_INVALID", "detail": "This resource cannot be reviewed",
             "meta": {"associatedErrors": {"/v1/appStoreVersions/v1": [
                 {"code": "ENTITY_ERROR.ATTRIBUTE.REQUIRED", "detail": "You must provide a value for the attribute 'supportUrl'"}]}}}
        text = asc.describe_error(x)
        self.assertIn("cannot be reviewed", text)
        self.assertIn("/v1/appStoreVersions/v1 ENTITY_ERROR.ATTRIBUTE.REQUIRED", text)
        self.assertIn("supportUrl", text)

    def test_plain_error_has_no_reason_part(self):
        self.assertEqual(asc.describe_error({"code": "X", "detail": "y"}), "X: y")


if __name__ == "__main__":
    unittest.main()
