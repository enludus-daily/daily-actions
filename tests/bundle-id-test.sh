#!/usr/bin/env bash
# asc/bundle-id.sh のテスト
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/ok.yml" <<'YAML'
targets:
  App:
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.enludus.daily.001
  AppTests:
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.enludus.daily.001.tests
YAML
[ "$(bash "$root/asc/bundle-id.sh" "$tmp/ok.yml")" = com.enludus.daily.001 ] || { echo "FAIL: 最初の Bundle ID"; exit 1; }
printf 'PRODUCT_BUNDLE_IDENTIFIER: "com.example.app"\n' > "$tmp/quoted.yml"
[ "$(bash "$root/asc/bundle-id.sh" "$tmp/quoted.yml")" = com.example.app ] || { echo "FAIL: 引用符"; exit 1; }
printf 'PRODUCT_BUNDLE_IDENTIFIER: $(evil)\n' > "$tmp/bad.yml"
if bash "$root/asc/bundle-id.sh" "$tmp/bad.yml" 2>/dev/null; then echo "FAIL: 不正な値を通した"; exit 1; fi
if bash "$root/asc/bundle-id.sh" "$tmp/none.yml" 2>/dev/null; then echo "FAIL: ないファイルを通した"; exit 1; fi
echo "bundle-id-test: OK"
