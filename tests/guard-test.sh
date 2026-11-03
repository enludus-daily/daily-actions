#!/usr/bin/env bash
# guard.sh の判定（1日の上限・連続失敗）を、偽の gh（決めた JSON を返す）で確かめる。
set -euo pipefail
guard="$(cd "$(dirname "$0")/.." && pwd)/guard/guard.sh"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
mkdir "$work/bin"
cat > "$work/bin/gh" <<'EOF'
#!/usr/bin/env bash
# 使い方は guard.sh の呼び方だけに合わせる: gh api <path> --jq <expr>
path="$2"; expr="$4"
case "$path" in
  *status=completed*) json="$FAKE_RECENT" ;;
  *) json="$FAKE_TODAY" ;;
esac
jq -c "$expr" <<<"$json"
EOF
chmod +x "$work/bin/gh"

runs() { # runs <id:conclusion>... → {"workflow_runs":[...]}
  local items=() p
  for p in "$@"; do items+=("{\"id\":${p%%:*},\"conclusion\":\"${p#*:}\"}"); done
  local IFS=,; echo "{\"workflow_runs\":[${items[*]}]}"
}
expect() { # expect <名前> <期待する終了コード>
  local rc=0
  PATH="$work/bin:$PATH" REPO=o/r WORKFLOW_REF="o/r/.github/workflows/ci.yml@refs/heads/main" RUN_ID=999 \
    MAX_PER_DAY="${MAX:-3}" MAX_CONSECUTIVE_FAILURES="${CONSEC:-2}" GUARD_DISABLED="${DISABLED:-false}" \
    bash "$guard" >/dev/null 2>&1 || rc=$?
  [ "$rc" -eq "$2" ] || { echo "FAIL: $1 (rc=$rc, want $2)"; exit 1; }
  echo "ok: $1"
}

FAKE_TODAY=$(runs 999:null 1:success) FAKE_RECENT=$(runs 1:success 2:failure) expect "under limit" 0
FAKE_TODAY=$(runs 999:null 1:success 2:success 3:failure) FAKE_RECENT=$(runs 1:success) expect "limit reached (current run not counted)" 1
FAKE_TODAY=$(runs 1:failure) FAKE_RECENT=$(runs 1:failure 2:failure 3:success) expect "consecutive failures" 1
FAKE_TODAY=$(runs 1:failure) FAKE_RECENT=$(runs 1:failure 2:cancelled 3:success) expect "cancelled is skipped, then success" 0
FAKE_TODAY=$(runs) FAKE_RECENT=$(runs 1:failure) expect "single failure is not enough" 0
DISABLED=true FAKE_TODAY=$(runs 1:x 2:x 3:x 4:x) FAKE_RECENT=$(runs 1:failure 2:failure) expect "disabled by owner" 0
echo "all guard tests passed"
