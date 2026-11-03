#!/usr/bin/env bash
# 1日の実行回数の上限と、連続失敗での停止。再利用ワークフローの最初のジョブで動かす。
# アプリのリポジトリからは変えられない場所（daily-actions@v1）に置くことで、セッションの規律だけに頼らない。
#
# 必要な環境変数:
#   GH_TOKEN          actions: read を持つ GITHUB_TOKEN
#   REPO              owner/repo
#   WORKFLOW_REF      github.workflow_ref（呼び出し元のワークフロー）
#   RUN_ID            今の実行 ID（数えない）
#   MAX_PER_DAY       日本時間の1日あたりの上限
#   MAX_CONSECUTIVE_FAILURES  直近で続けて失敗してよい回数（これに達したら止める）
#   GUARD_DISABLED    "true" なら検査しない（本人がリポジトリの Variables で設定する。セッションは変えない）
set -euo pipefail

if [ "${GUARD_DISABLED:-}" = "true" ]; then
  echo "::warning::DAILY_GUARD_DISABLED=true のため上限を検査しません"
  exit 0
fi

# "owner/repo/.github/workflows/ci.yml@refs/heads/main" → "ci.yml"
wf_path="${WORKFLOW_REF#*/*/}"; wf_path="${wf_path%@*}"; wf_file="${wf_path##*/}"
today_jst=$(TZ=Asia/Tokyo date +%Y-%m-%d)
since="${today_jst}T00:00:00+09:00"

runs_today=$(gh api "repos/$REPO/actions/workflows/$wf_file/runs?per_page=100&created=%3E%3D${since//+/%2B}" \
  --jq "[.workflow_runs[] | select(.id != $RUN_ID)] | length")
echo "今日（JST $today_jst）の $wf_file の実行: $runs_today 回 / 上限 $MAX_PER_DAY 回"
if [ "$runs_today" -ge "$MAX_PER_DAY" ]; then
  echo "::error title=DAILY_LIMIT::今日の $wf_file の上限（$MAX_PER_DAY 回）に達しました。止めて本人に戻してください。"
  exit 1
fi

# 完了した直近の実行（取り消しは数えない）が全部失敗なら止める
recent=$(gh api "repos/$REPO/actions/workflows/$wf_file/runs?status=completed&per_page=20" \
  --jq "[.workflow_runs[] | select(.id != $RUN_ID) | select(.conclusion != \"cancelled\" and .conclusion != \"skipped\") | .conclusion][:$MAX_CONSECUTIVE_FAILURES]")
count=$(jq 'length' <<<"$recent"); failures=$(jq '[.[] | select(. == "failure" or . == "timed_out")] | length' <<<"$recent")
echo "直近 $count 回のうち失敗 $failures 回（連続 $MAX_CONSECUTIVE_FAILURES 回で停止）"
if [ "$count" -ge "$MAX_CONSECUTIVE_FAILURES" ] && [ "$failures" -eq "$count" ]; then
  echo "::error title=CONSECUTIVE_FAILURES::$wf_file が $MAX_CONSECUTIVE_FAILURES 回続けて失敗しています。止めて本人に戻してください。"
  exit 1
fi
echo "guard: OK"
