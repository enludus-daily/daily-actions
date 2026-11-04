#!/usr/bin/env bash
# project.yml（XcodeGen）から、アプリのターゲットの Bundle ID（最初の PRODUCT_BUNDLE_IDENTIFIER）を取り出す。
set -euo pipefail
spec="${1:-project.yml}"
[ -f "$spec" ] || { echo "bundle-id: $spec がありません" >&2; exit 2; }
id=$(grep -m1 -E '^[[:space:]]*PRODUCT_BUNDLE_IDENTIFIER:' "$spec" | sed -E 's/^[^:]*:[[:space:]]*//; s/["'\'']//g; s/[[:space:]]+$//')
if ! [[ "$id" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{2,154}$ ]]; then
  echo "bundle-id: Bundle ID を読めません（'$id'）" >&2; exit 1
fi
printf '%s\n' "$id"
