# 署名の方針（2026-11-03）

> **追記（2026-11-05）：証明書を毎回作らせない。** クラウド署名だけでは、GitHub Actions の Mac に証明書がないため、`xcodebuild archive -allowProvisioningUpdates` が実行のたびに **Apple Development 証明書を新しく作っていた**（秘密鍵は実行が終わると消える）。これで証明書の数が上限に達し、「Your account has reached the maximum number of certificates」で止まった（app-20261103-spread-diary の run 41961099548、app-20261105-koyomi-haiku の run 42044489085）。
> 本人の判断で、**本人の Mac にある証明書（秘密鍵つき）1 枚を .p12 に書き出し、組織の Secrets に入れて使い回す**ことにした。
> - Secrets：`SIGNING_CERT_P12_BASE64`（.p12 を base64 にした文字列。`base64 -i cert.p12 | pbcopy`）、`SIGNING_CERT_PASSWORD`
> - `ios-testflight.yml` は、使い捨てのキーチェーンに読み込み、証明書の種類（Apple Development / Apple Distribution）に合わせて `CODE_SIGN_IDENTITY` を決めてアーカイブし、最後にキーチェーンを消す。Secrets がないときは、証明書を作らずにエラーで止まる
> - プロファイルと、書き出し（配布）の署名は今までどおり API キーで自動
> - 呼び出し元（各アプリの `testflight.yml`）は、この 2 つも `secrets:` で渡す
> - developer.apple.com で使い捨ての証明書を取り消すとき、この 1 枚は取り消さない。期限（1 年）が来たら、新しい証明書を書き出して Secrets を入れ替える

> **結果と決定（2026-11-03）**：App Manager のチームキーでは、アーカイブは通ったが、配布用の書き出しで `Cloud signing permission error` と `No profiles for 'com.enludus.daily.001' were found` になった（app-20261103-pipeline-check の TestFlight 実行 41587186017）。本人の判断で **Admin のチームキーに替える**（案2）。理由：本人の他のリポジトリでも Admin のキーを Secrets に入れて運用しており、証明書とプロファイルの管理を増やさないため。

## 今の方式：クラウド署名（自動）

`xcodebuild archive / -exportArchive` に `-allowProvisioningUpdates` と API キー（`-authenticationKeyPath/ID/IssuerID`）を渡し、配布証明書（クラウド管理）とプロファイルを Xcode に用意させる。証明書もプロファイルもリポジトリや Secrets に置かずに済む。

## 確かめたこと（一次資料）

| 事実 | 出典 |
|---|---|
| xcodebuild は API キーで自動署名できる（Xcode 13 から） | Xcode 13 Release Notes |
| API キーには、ユーザーと同じ役割が付く | App Store Connect API「Creating API Keys」 |
| 個人キーは Provisioning のエンドポイントを使えない（チームキーは使える） | 同上 |
| 役割の表で、App Manager には「その他のクラウド管理証明書の作成」に ✓ がある。一方、配布用プロファイルの作成、App ID の登録、配布証明書の作成には「Certificates, Identifiers & Profiles へのアクセスが要る（App Store Connect で付与）」とある | developer.apple.com/help/account/access/roles |

## 確定できなかったこと

- **チームキー**に「Certificates, Identifiers & Profiles へのアクセス」を追加で付与できるか。ユーザーにはできるが、キーについての記述は見つからなかった。
- そのため、App Manager のキーで「配布証明書（クラウド管理）を使う」「プロファイルを自動で作る」が通るかは、**最初の TestFlight の実行で確かめる**。

## 失敗したときの代わりの案（本人に提案してから入れる）

**案1：証明書とプロファイルを組織の Secrets に入れる（手動署名）**
1. 本人が Mac で、Apple Distribution 証明書を `.p12` に書き出し、App Store 用のプロファイルを Bundle ID ごとに作る（com.enludus.daily.001〜007）。
2. 組織の Secrets に入れる：`DIST_CERT_P12_BASE64`、`DIST_CERT_PASSWORD`、`PROFILES_TAR_BASE64`（7 個のプロファイルをまとめたもの）。
3. ワークフローは、使い捨てのキーチェーンに証明書を入れ、プロファイルを置き、`CODE_SIGN_STYLE=Manual` でアーカイブする。終わったらキーチェーンを消す。

- 良い点：今の App Manager のキーのままでよい。役割を上げなくてよい。
- 悪い点：証明書の期限（1年）とプロファイルの更新を本人が管理する。秘密が増える。枠を増やすたびにプロファイルも足す。

**案2：Admin の役割のチームキーを作る**
- 良い点：クラウド署名がそのまま通る見込みが高い。管理するものが増えない。
- 悪い点：鍵が漏れたときの被害が大きい（ユーザー管理などもできる）。public リポジトリの Actions で使う鍵としては重い。

推奨は **案1**。理由は、鍵の権限を小さく保てることを、管理の手間より重く見るため。
