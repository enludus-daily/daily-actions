#!/usr/bin/env bash
# 秘密情報・個人情報の検査。push の前（各アプリの scripts/preflight-push.sh）と CI（scan/action.yml）で使う。
# 正本はこのファイル（enludus-daily/daily-actions/scan/scan-sensitive.sh）。テンプレートには複製を置く。
#
# 使い方: scan-sensitive.sh [リポジトリのディレクトリ] [コミット範囲 (例: origin/main..HEAD。省略時は全履歴)]
# 終了コード: 0 = 検出なし / 1 = 検出あり / 2 = 実行できない（fail closed）
#
# 検出した値そのものは出力しない（ログに秘密を残さないため）。ファイル:行 と規則名だけを出す。
set -uo pipefail

repo="${1:-.}"
range="${2:-}"
cd "$repo" || { echo "scan: ディレクトリに入れません: $repo" >&2; exit 2; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "scan: git リポジトリではありません" >&2; exit 2; }

found=0
report() { echo "  [$1] $2"; found=1; }

# 1. 置いてはいけないファイル（鍵・証明書・プロファイル・env）
echo "== 1. 禁止ファイル"
while IFS= read -r f; do
  case "$f" in
    *.p8|*.p12|*.pfx|*.pem|*.key|*.cer|*.der|*.mobileprovision|*.provisionprofile|*.keychain|*.keychain-db|*.jks|*.keystore)
      report forbidden-file "$f" ;;
    .env|.env.*|*/.env|*/.env.*)
      case "$f" in *.env.example) ;; *) report forbidden-file "$f" ;; esac ;;
  esac
done < <(git ls-files)

# 2. 秘密情報らしい文字列（作業ツリーの追跡ファイル）
#    gitleaks が使えるならそちらでも検査する（CI では scan/action.yml が入れる）。
echo "== 2. 秘密情報のパターン"
secret_rules=(
  'private-key|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'github-token|(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}'
  'github-pat|github_pat_[A-Za-z0-9_]{60,}'
  'aws-access-key|(AKIA|ASIA)[0-9A-Z]{16}'
  'google-api-key|AIza[0-9A-Za-z_-]{35}'
  'slack-token|xox[abprs]-[0-9A-Za-z-]{10,}'
  'stripe-key|(sk|rk)_live_[0-9A-Za-z]{20,}'
  'anthropic-key|sk-ant-[A-Za-z0-9_-]{20,}'
  'openai-key|sk-(proj-)?[A-Za-z0-9]{32,}'
  'jwt|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  'generic-secret-assignment|(api[_-]?key|secret|password|passwd|token)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][A-Za-z0-9/+_=-]{16,}["'"'"']'
)
for rule in "${secret_rules[@]}"; do
  name="${rule%%|*}"; pattern="${rule#*|}"
  while IFS= read -r hit; do
    [ -n "$hit" ] && report "$name" "$hit"
  done < <(git grep -I -n -i -E -o -e "$pattern" -- . ':!*.xcprivacy' 2>/dev/null | cut -d: -f1,2 | sort -u)
done

# 3. 個人情報らしい文字列
echo "== 3. 個人情報のパターン"
# メールアドレス: 許可するドメイン・アドレス以外は検出する。
email_allow='(@example\.(com|org|net)|@users\.noreply\.github\.com|^noreply@anthropic\.com$|^noreply@github\.com$)'
while IFS= read -r line; do
  loc="${line%:*}"; addr="${line##*:}"
  if ! printf '%s' "$addr" | grep -qiE "$email_allow"; then report email "$loc"; fi
done < <(git grep -I -n -o -E '[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- . 2>/dev/null \
          | awk -F: '{print $1":"$2":"$3}' | sort -u)
pii_rules=(
  'jp-phone|(^|[^0-9])(0[789]0-?[0-9]{4}-?[0-9]{4}|0[1-9]-[0-9]{4}-[0-9]{4}|0[1-9][0-9]{1,3}-[0-9]{1,4}-[0-9]{4}|\+81[- ]?[0-9]{1,4}[- ]?[0-9]{1,4}[- ]?[0-9]{4})([^0-9]|$)'
  'jp-postal|〒[[:space:]]*[0-9]{3}-?[0-9]{4}'
  'home-path|(/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|[Cc]:\\Users\\[A-Za-z0-9._-]+)'
)
for rule in "${pii_rules[@]}"; do
  name="${rule%%|*}"; pattern="${rule#*|}"
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    # CI とセッションの決まった場所は個人の場所ではないので除く
    if [ "$name" = home-path ]; then
      text=$(git grep -I -h -n -E -o -e "$pattern" -- "${hit%%:*}" 2>/dev/null | grep -vE '/(home/(user|runner)|Users/runner)/' || true)
      [ -z "$text" ] && continue
    fi
    report "$name" "$hit"
  done < <(git grep -I -n -E -e "$pattern" -- . 2>/dev/null | cut -d: -f1,2 | sort -u)
done

# 4. コミットの作者・コミッターのメールアドレス
#    本人のアドレスは、コミットの作者としてだけ許す（本人の判断 2026-11-03。テンプレートからの生成で作者が本人になるため）。
#    ファイルの中身に書かれた場合は 3. で検出する。
echo "== 4. コミットのメールアドレス"
commit_email_allow='^(noreply@anthropic\.com|noreply@github\.com|ibuki\.n5@icloud\.com|[0-9]+\+[A-Za-z0-9-]+(\[bot\])?@users\.noreply\.github\.com|[A-Za-z0-9-]+@users\.noreply\.github\.com)$'
if [ -n "$range" ]; then log_args=("$range"); else log_args=(--all); fi
while IFS='|' read -r sha ae ce; do
  printf '%s' "$ae" | grep -qiE "$commit_email_allow" || report commit-author-email "${sha:0:12}"
  printf '%s' "$ce" | grep -qiE "$commit_email_allow" || report commit-committer-email "${sha:0:12}"
done < <(git log --format='%H|%ae|%ce' "${log_args[@]}" 2>/dev/null)

# 5. 履歴に入った禁止ファイル（消しても履歴に残るため）
echo "== 5. 履歴の禁止ファイル"
while IFS= read -r f; do
  [ -n "$f" ] && report forbidden-file-in-history "$f"
done < <(git log --format= --name-only "${log_args[@]}" 2>/dev/null | sort -u \
          | grep -E '\.(p8|p12|pfx|pem|key|mobileprovision|provisionprofile|keychain(-db)?|jks|keystore)$|(^|/)\.env(\.[^/]*)?$' \
          | grep -vE '\.env\.example$' || true)

# 6. gitleaks（あれば。CI では必須にする: SCAN_REQUIRE_GITLEAKS=1）
echo "== 6. gitleaks"
if command -v gitleaks >/dev/null 2>&1; then
  if [ -n "$range" ]; then gl_opts=(--log-opts="$range"); else gl_opts=(); fi
  if ! gitleaks git --no-banner --redact --exit-code 1 "${gl_opts[@]}" . >/dev/null 2>&1; then
    report gitleaks "gitleaks git が検出しました（ローカルで gitleaks git --redact -v を実行して確認）"
  fi
elif [ "${SCAN_REQUIRE_GITLEAKS:-0}" = 1 ]; then
  echo "scan: gitleaks が必要ですが見つかりません" >&2; exit 2
else
  echo "  (gitleaks なし。1〜5 の検査だけ行いました)"
fi

if [ "$found" -ne 0 ]; then
  echo "scan: 検出あり。push しないでください。誤検出なら内容を書き換えて避けてください（許可リストは daily-actions 側で本人が管理）。" >&2
  exit 1
fi
echo "scan: 検出なし"
