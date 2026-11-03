# daily-actions

enludus-daily の iOS アプリ用の再利用ワークフローと composite action。各アプリは `@v1` で呼ぶ。

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
    uses: enludus-daily/daily-actions/.github/workflows/ios-ci.yml@v1
    with:
      scheme: MyApp
      guard-disabled: ${{ vars.DAILY_GUARD_DISABLED == 'true' && 'true' || 'false' }}
```

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

止まったら本人が原因を見て直す。どうしても上限を外すときは、そのアプリのリポジトリの Variables に `DAILY_GUARD_DISABLED=true` を本人が設定する（自動のセッションは変えない）。

## 版の付け方

- 変更は PR → main。リリースは `v1.x.y` のタグを付け、`v1` を動かす（`git tag -f v1 && git push -f origin v1`）。
- 互換を壊す変更（入力の削除・名前の変更、上限の考え方の変更）は `v2` にする。
- composite action は `@v1` で自分自身を参照している。`v1` を動かすまで、main の変更はアプリ側に届かない。

## 署名について

`docs/signing.md` を読むこと。App Manager のチームキーでクラウド署名ができるかは、公開文書では確定していない。
