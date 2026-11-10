# daily-actions

enludus-daily の iOS アプリ用の再利用ワークフローと composite action。各アプリは **コミットの SHA（40 桁）で固定して** 呼ぶ（タグは使わない）。

| 名前 | 種類 | すること |
|---|---|---|
| `.github/workflows/ios-ci.yml` | 再利用ワークフロー | 上限の確認 → 秘密・個人情報の検査 → XcodeGen → ビルドとテスト（シミュレータ、署名なし）→ スクリーンショット（画面の名前に `@dark` でダークモード、`@en` で英語）→ `demo-video: en` でデモの録画（起動引数 `-demo`、artifact `demo-video`。YouTube のシリーズ用） |
| `.github/workflows/ios-testflight.yml` | 再利用ワークフロー | 上限の確認 → 検査 → アーカイブ（クラウド署名）→ ipa の書き出し → `altool` で TestFlight へ → 処理を待って内部テストのグループに配信 |
| `.github/workflows/ios-asc.yml` | 再利用ワークフロー | App Store Connect API：`metadata`（名前・説明・カテゴリ・URL などを入れ、提出用のバージョンにビルドを選ぶ）/ `distribute`（最新のビルドを内部テストに配信）/ `products`（App 内課金の商品・価格・配信地域・審査用の画像を作る）/ `submit`（`confirm-version` と同じ版のときだけ、バージョンと App 内課金を審査に出す）/ `withdraw`（審査待ち・審査中のものを取り下げる） |
| `.github/workflows/ios-store-screenshots.yml` | 再利用ワークフロー | 6.9 インチの iPhone のシミュレータで各画面を撮る → `screenshots/frame.swift` で見出しと枠をつけて 1290×2796 にする → 提出用のバージョンの `APP_IPHONE_67` を入れ替える。`app-language: en` で英語の画面を撮り、`locale: en-US` に入れる |
| `asc/` | スクリプト | `asc.py`（App Store Connect API。標準ライブラリと openssl だけ）、`iap.py`（App 内課金の商品）、`store.py`（審査への提出・ストアのスクリーンショット）、`bundle-id.sh` |
| `screenshots/` | スクリプト | `frame.swift`（macOS の標準の部品だけで、ストア用の画像を作る） |
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

## App Store Connect API（asc/、2026-11-04 本人の許可で追加）

- 鍵は TestFlight と同じ 3 つの Secrets（Admin のチームキー。`docs/signing.md`）。JWT（ES256）は openssl で署名し、外部のパッケージは使わない。
- `distribute`：ビルドの処理（VALID）を待つ → 輸出コンプライアンスが未回答なら「標準の暗号だけ（NO）」と答える → 「テストの内容」を書く → 内部テストのグループ `daily-internal` を探す（無ければ作る。**全ビルドを自動で配信**）→ App Store Connect のユーザーを内部テスターに加える。
- `metadata`：アプリの `docs/app-store.json` を読み（`"en-US": {...}` のようなほかの言語のかたまりも。2026-11-10）、アプリ名・サブタイトル・プライバシーポリシー URL・カテゴリ・説明・キーワード・サポート URL・宣伝文を入れる。提出用のバージョンが無ければ作り、最新の処理済みビルドを選ぶ。
- `submit`（2026-11-10 本人の許可で追加）：提出用のバージョンが `confirm-version` と同じときだけ、App 内課金（提出の準備ができたもの）といっしょに審査に出す。App プライバシー・年齢区分・審査用の連絡先が足りないと Apple のエラーで止まる。アプリ側は、本人が「出して」と言ったときだけ実行する。
- ストアのスクリーンショット（2026-11-10 本人の許可で追加）：`ios-store-screenshots.yml`。アプリの `docs/store-screenshots.json` の画面と見出しで作る。1 日 10 回、連続 2 回の失敗で停止。
- **しないこと**：App プライバシーの申告（公開 API がない）、価格・配信地域・年齢区分、App の新規作成（API がない）。
- 上限：`ios-asc.yml` は 1 日 20 回、連続 3 回の失敗で停止。
- メールアドレスなどの個人情報・鍵・トークンはログとまとめに出さない。


### App 内課金の商品（asc/iap.py、2026-11-05 本人の許可で追加）
- 本人の方針：毎日のアプリに課金を必ず入れる。商品の作成と価格は AI が決めてよい（本人の回答 2026-11-05）。
- アプリの `docs/products.json` のとおりに、買い切り（NON_CONSUMABLE）と自動更新サブスク（AUTO_RENEWABLE）を作る。何度実行しても同じ結果（あるものは作り直さない）。
- 価格は日本円（基準の国は JPN）。ほかの国は Apple の換算に任せる。サブスクの価格は、まだ無いときだけ入れる（変えるのは本人）。
- 消耗型（ジェム・コイン）と非更新サブスクは作らない。毎日のアプリはサーバーを持たず、残高や期間を守れないため。
- 審査への提出はしない。App 内課金は、初めてはアプリのバージョンと一緒に本人が出す。
- API の形は Apple の OpenAPI 仕様（App Store Connect API 4.6、2026-11-04 版）で確かめた。実際の App Store Connect での動作は、最初の実行で確かめる。

## 既定値と根拠（2026-11-03 に確認）

- ランナー `macos-26`：標準ランナーは public リポジトリで無料。`-large` / `-xlarge` は有料なので使わない（GitHub Docs: billing / github-actions）。
- Xcode `26.6`：`macos-26-arm64` イメージの既定（`/Applications/Xcode_26.6.app`、iOS SDK 26.5）。2026-04-28 以降、App Store Connect へのアップロードには Xcode 26 以降・iOS 26 SDK が必要（developer.apple.com/news/upcoming-requirements）。
- アップロードは `xcrun altool --upload-app`（App Store Connect ヘルプ「Upload builds」に載っている方法）。
- ビルド番号は UTC の `YYYYMMDDHHMM`。同じ Bundle ID の中で必ず増える。

## 上限（既定）

2026-11-04 に本人の判断で引き上げた（アプリごとに本人が意見を出し、そのたびに直して TestFlight に出すため）。暴走を止めるのは主に「連続失敗で停止」のほう。

| | 1日あたり（JST） | 連続失敗で停止 |
|---|---|---|
| CI | 40 回 | 3 回 |
| TestFlight | 10 回 | 2 回 |
| App Store Connect（ios-asc） | 20 回 | 3 回 |

止まったら本人が原因を見て直す（毎日のセッションは止まって `needs-human` を付ける）。どうしても上限を外すときは、そのアプリのリポジトリの Variables に `DAILY_GUARD_DISABLED=true` を本人が設定する（自動のセッションは変えない）。

## 版の付け方（SHA 固定）

1. daily-actions を直して main に push する（PR でもよい）。
2. 自己 CI（`.github/workflows/ci.yml`）がそのコミットで通ったことを確かめる。
3. daily-app-template で `scripts/pin-daily-actions.sh <SHA>` を実行して push する（スクリプトは、その SHA で自己 CI が成功していることを確かめてから書き換える）。
4. すでに作ったアプリのリポジトリは、作った時点の SHA のまま（遡って更新しない）。

**daily-actions を直すのは、本人が始める整備用のセッションか、本人がそのセッションで明示的に許したときだけ。** 毎日の予約実行のセッションには daily-actions を付けない（CI・上限のガード・検査を、毎日の AI が緩められないようにするため）。

## 署名について

`docs/signing.md` を読むこと。App Manager のチームキーでクラウド署名ができるかは、公開文書では確定していない。
