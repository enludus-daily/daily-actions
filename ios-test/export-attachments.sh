#!/usr/bin/env bash
# xcresult から UI テストの添付の画像を取り出し、<端末>-<番号>-<操作>-screen<n>.png の名前で置く。
# 失敗の要約もログに出す。何があっても 0 で終わる（テストの成否は run-tests.sh が決める）。
# 使い方: bash ios-test/export-attachments.sh <xcresult> <出力フォルダ>
set -uo pipefail
res="${1:?}"; out="${2:?}"; raw="$out.raw"
[ -d "$res" ] || { echo "テストの結果がありません"; exit 0; }
mkdir -p "$raw" "$out"
xcrun xcresulttool export attachments --path "$res" --output-path "$raw" >/dev/null || exit 0
jq -r '.[] | .attachments[]? | [.exportedFileName, .suggestedHumanReadableName] | @tsv' "$raw/manifest.json" 2>/dev/null |
  while IFS=$'\t' read -r file name; do
    case "$file" in *.png|*.jpg|*.jpeg|*.heic) ;; *) continue ;; esac
    # 書き出しが付ける "_<番号>_<UUID>" と拡張子を外す
    base=$(printf '%s' "$name" | sed -E 's/\.(png|jpe?g|heic)$//; s/_[0-9]+_[0-9A-Fa-f-]{36}$//')
    safe=$(printf '%s' "$base" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-120)
    cp "$raw/$file" "$out/$safe.${file##*.}"
  done
rm -rf "$raw"
find "$out" -type f | sort | head -80
echo "--- テストの結果の要約（失敗）"
xcrun xcresulttool get test-results summary --path "$res" --compact 2>/dev/null |
  jq -r '.testFailures[]? | "\(.targetName) / \(.testName): \(.failureText)"' | head -40 || true
exit 0
