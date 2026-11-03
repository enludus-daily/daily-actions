#!/usr/bin/env bash
# scan-sensitive.sh が、きれいなリポジトリを通し、秘密・個人情報を含むリポジトリを止めることを確かめる。
set -euo pipefail
scan="$(cd "$(dirname "$0")/.." && pwd)/scan/scan-sensitive.sh"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
commit() { git -c user.email="$1" -c user.name=t commit -qm init; }

mkdir "$work/clean" && cd "$work/clean" && git init -q
printf 'let url = "https://example.com"\n// dev@example.com\nlet p = "/home/runner/work"\nlet d = "2026-11-03"\n' > a.swift
git add . && commit noreply@anthropic.com
bash "$scan" . >/dev/null || { echo "FAIL: clean repo was rejected"; exit 1; }

check() { # check <名前> <期待する規則> -- <ファイル内容> [<コミットのメール>] [<追加ファイル>]
  local name="$1" rule="$2" content="$3" email="${4:-noreply@anthropic.com}" extra="${5:-}"
  local d="$work/$name"; mkdir "$d" && cd "$d" && git init -q
  printf '%s\n' "$content" > f.txt
  [ -n "$extra" ] && echo x > "$extra"
  git add . && commit "$email"
  local out; set +e; out=$(bash "$scan" . 2>&1); local rc=$?; set -e
  if [ "$rc" -ne 1 ] || ! grep -q "\[$rule\]" <<<"$out"; then echo "FAIL: $name (rc=$rc)"; echo "$out"; exit 1; fi
  echo "ok: $name"
}
check token github-token "k = \"ghp_$(printf 'a%.0s' {1..36})\""
check privkey private-key "-----BEGIN EC PRIV""ATE KEY-----"
check email email "mail: someone@""gmail.com"
check phone jp-phone "tel 090""-1234-5678"
check postal jp-postal "〒""100-0001"
check home home-path "path /Us""ers/taro/x"
check assign generic-secret-assignment 'api_key = "abcdefgh''ijklmnop1234"'
check author commit-author-email "hello" "me@""gmail.com"
check p8 forbidden-file "hello" noreply@anthropic.com AuthKey_ABC.p8

# 本人のアドレスは、コミットの作者としては通り、ファイルの中身では検出される
owner="ibuki.n5@""icloud.com"
mkdir "$work/owner" && cd "$work/owner" && git init -q && echo hello > f.txt && git add . && commit "$owner"
bash "$scan" . >/dev/null || { echo "FAIL: owner as commit author was rejected"; exit 1; }
echo "ok: owner-author-allowed"
check owner-in-content email "contact: $owner"
echo "all scan tests passed"
