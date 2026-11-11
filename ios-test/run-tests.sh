#!/usr/bin/env bash
# xcodebuild test を 1 台で走らせる（ios-ci.yml の build-test と test-extra から呼ぶ）。
# 使い方: SCHEME=<スキーム> DESTINATION=<行き先> RESULT=<xcresult の置き場> bash ios-test/run-tests.sh
set -euo pipefail
: "${SCHEME:?}" "${DESTINATION:?}" "${RESULT:?}"

# テストの前にシミュレータを起動しきっておく（起動したばかりのシミュレータでは、UI テストの実行役が
# 起動の途中で止まることがある。2026-11-11）。見つからなくても xcodebuild に任せて続ける
preboot() {
  local name os key udid
  name=$(printf '%s' "$1" | sed -nE 's/.*name=([^,]+).*/\1/p')
  os=$(printf '%s' "$1" | sed -nE 's/.*OS=([^,]+).*/\1/p')
  [ -n "$name" ] || return 0
  if [ -z "$os" ] || [ "$os" = latest ]; then
    # Duo 専用のランタイム（iOS-27-1-iPhone19-4 など）は除き、iOS-<数>-<数> のうち新しいもの
    key='[.devices | to_entries[] | select(.key | test("SimRuntime\\.iOS-[0-9]+-[0-9]+$")) | .key] | sort_by(split("-")[-2:] | map(tonumber)) | last'
  else
    key="\"com.apple.CoreSimulator.SimRuntime.iOS-${os//./-}\""
  fi
  udid=$(xcrun simctl list devices available -j | jq -r --arg n "$name" ".devices[$key][]? | select(.name == \$n) | .udid" | head -1)
  [ -n "$udid" ] || { echo "::warning::起動しておくシミュレータが見つかりません: $1"; return 0; }
  xcrun simctl boot "$udid" 2>/dev/null || true
  xcrun simctl bootstatus "$udid" -b >/dev/null && echo "起動済み: $name ($os) $udid"
}
preboot "$DESTINATION" || true

# 失敗したテストは 1 回だけやり直す（XCTest の「does not have a process ID」「background assertion」など、
# シミュレータ側のまれな失敗のため。ボタンが画面の外にあるなどの本当の失敗は毎回起きるので、見逃さない）
xcodebuild test \
  -scheme "$SCHEME" \
  -destination "$DESTINATION" \
  -retry-tests-on-failure -test-iterations 2 \
  -derivedDataPath "$RUNNER_TEMP/dd" \
  -resultBundlePath "$RESULT" \
  CODE_SIGNING_ALLOWED=NO \
  | xcbeautify --renderer github-actions
