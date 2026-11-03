# daily-actions

enludus-daily の iOS アプリ用の再利用ワークフローと composite action。各アプリは **コミットの SHA（40 桁）で固定して** 呼ぶ（タグは使わない）。

| 名前 | 種類 | すること |
|---|---|---|
| `.github/workflows/ios-ci.yml` | 再利用ワークフロー | 上限の確認 → 秘密・個人情報の検査 → XcodeGen → ビルドとテスト（シミュレータ、署名なし）→ スクリーンショット |
| `.github/workflows/ios-testflight.yml` | 再利用ワークフロー | 上限の確認 → 検査 → アーカイブ（クラウド署名）→ ipa の書き出し → `altool` で TestFlight へ |
| `guard/` | composite | 日本時間の1日あたりの実行上限と、連続失敗での停止 |
| `scan/` | composite | `scan-sensitive.sh`（正本）＋ gitleaks 8.30.1（版とチェックサムで固定） |
| `setup-ios/` | composite | Xcode の版の選択と XcodeGen 2.46.0（版とハッシュで固定） |

## 呼び方

```yaml
# .github/workflows/ci.yml（アプリ側）
jobs:
  ci:
    uses: enludus-daily/daily-actions/.github/workflows/ios-ci.yml@<40桁の SHA> # daily-actions YYYY-MM-DD
    with:
      actions-sha: <同じ SHA>   # 中の部品（guard / scan / setup-ios）もこの版で使う
      scheme: MyApp
      guard-disabled: ${{ vars.DAILY_GUARD_DISABLED == 'true' && 'true' || 'false' }}
```

`@` と `actions-sha` には同じ SHA を書く（コミットは自分自身の SHA を中に書けないため、部品の版は呼び出し側から渡す）。テンプレートでは `scripts/pin-daily-actions.sh <SHA>` が両方を書き換える。

TestFlight は `workflow_dispatch` からだけ、既定ブランチでだけ動く（pull_request から秘密に届かないようにするため）。秘密は `secrets: inherit` ではなく、使う3つだけを渡す。

## 既定値と根拠（2026-11-03 に確認）

- ランナー `macos-26`：標準ランナーは public リポジトリで無料。`-large` / `-xlarge` は有料なので使わない（GitHub Docs: billing / github-actions）。
- Xcode `26.6`：`macos-26-arm64` イメージの既定（`/Applications/Xcode_26.6.app`、iOS SDK 26.5）。2026-04-28 以降、App Store Connect へのアップロードには Xcode 26 以降・iOS 26 SDK が必要（developer.apple.com/news/upcoming-requirements）。
- アップロードは `xcrun altool --upload-app`（App Store Connect ヘルプ「Upload builds」に載っている方法）。
- ビルド番号は UTC の `YYYYMMDDHHMM`。同じ Bundle ID の中で必ず増える。

## 上限（既定）

| | 1日あたり（JST） | 連続失敗で停止 |
|---|---|---|
| CI | 12 回 | 3 回 |
| TestFlight | 3 回 | 2 回 |

止まったら本人が原因を見て直す（毎日のセッションは止まって `needs-human` を付ける）。どうしても上限を外すときは、そのアプリのリポジトリの Variables に `DAILY_GUARD_DISABLED=true` を本人が設定する（自動のセッションは変えない）。

## 版の付け方（SHA 固定）

1. daily-actions を直して main に push する（PR でもよい）。
2. 自己 CI（`.github/workflows/ci.yml`）がそのコミットで通ったことを確かめる。
3. daily-app-template で `scripts/pin-daily-actions.sh <SHA>` を実行して push する（スクリプトは、その SHA で自己 CI が成功していることを確かめてから書き換える）。
4. すでに作ったアプリのリポジトリは、作った時点の SHA のまま（遡って更新しない）。

**daily-actions を直すのは、本人が始める整備用のセッションだけ。** 毎日の予約実行のセッションには daily-actions を付けない（CI・上限のガード・検査を、毎日の AI が緩められないようにするため）。

## 署名について

`docs/signing.md` を読むこと。App Manager のチームキーでクラウド署名ができるかは、公開文書では確定していない。
