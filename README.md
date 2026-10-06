# 日本株スクリーニング自動化 - 仕様

**目的**: 日本株の銘柄をスクリーニングする

## 技術構成

- 言語/環境: Python、`uv`でプロジェクトごとに依存関係管理
- プロジェクト場所: `03_projects/python/jp-stock-screener/`（作成済み）
- データ取得: `yfinance`（導入済み）
- データ処理: `pandas`（導入済み）
- 出力先: ローカル保存（CSV）に加え、ダッシュボードはStreamlit Community Cloudにデプロイ済み（GitHubリポジトリ作成済み、そこと連携してデプロイ）
- 定期実行: Mac miniの`LaunchDaemon`で実行する方針に決定（詳細は「定期実行の方針」セクション参照）
- 通知: Discord Webhookで日次ジョブの結果を通知（詳細は「通知（Discord Webhook）」セクション参照）

## 段階的な進め方

1. 数銘柄（例: 7203.T, 6758.T, 8035.T）でyfinanceから株価取得 ✅完了（`main.py`）
2. 流動性フィルタのロジックを固める ✅完了（`screening.py`）
3. 対象銘柄を100銘柄程度に拡大 ✅完了（`tickers.py`、日経225構成銘柄から100銘柄抽出、除外0件を確認）
4. 東証全銘柄規模へ拡大 ✅完了（`fetch_universe.py` + `universe.csv`、3,707銘柄中1,518銘柄がフィルタ通過）
5. ローカルへの結果保存を実装 ✅完了（`screening_result.csv`。当初はGoogle Sheets書き込みを想定していたが、まずはローカル管理で進める方針に変更）
6. 日次の終値・出来高蓄積の仕組みを実装 ✅完了（`daily_update.py`、`daily_ohlcv.csv`）
7. 流動性フィルタ通過銘柄に対しTOPIX相対強度でスクリーニング ✅完了（`filters.py`, `run_screening.py`, `backfill_history.py`, `rs_ranking.csv`）
8. ゴールデンクロス（初動）スクリーニングを追加 ✅完了（`filters.py`, `run_golden_cross.py`, `golden_cross.csv`）
9. ランキング閲覧・チャート表示用のダッシュボードを実装 ✅完了（`dashboard.py`、Streamlit + Plotly）
10. ダッシュボードをStreamlit Community Cloudにデプロイ ✅完了（GitHubリポジトリ作成・連携済み）
11. 定期実行の方式を決定 ✅完了（Mac miniの`LaunchDaemon`を採用、詳細は下記セクション）
12. 日々の通知の実装 ✅完了（`notify.py`、Discord Webhookで成功・失敗と新規銘柄を通知）
13. Mac mini側のセットアップ（リポジトリclone・`uv sync`・`LaunchDaemon`登録） ✅完了（Mac mini上のClaude Codeセッションで実施。2026-09-28(月)から平日18:00の定期実行が稼働し、データ更新・commit＆push・Discord通知が正常に行われていることを確認）
14. （将来検討）月次ジョブ（`fetch_universe.py`/`screening.py`）の自動化

## 流動性フィルタの仕様

- 売買代金 = 終値（Close）× 出来高（Volume）
- 10日移動平均・10日中央値の両方を算出（中央値を採用基準、平均は参考値）
- 除外基準: 10日中央値売買代金が1億円未満
- 取得期間: `period="1mo"`（10日移動平均に十分な日数）
- 20日ではなく10日を採用した理由: 流動性フィルタは直近の動向を反映したいため、より反応の早い10日窓を選択

## 対象銘柄の全体拡大（東証全銘柄）

- 銘柄マスタ: JPX公式サイトが配布する上場銘柄一覧（`data_j.xlsx`）を`fetch_universe.py`でダウンロードし、`universe.csv`に保存（銘柄コード、銘柄名、市場区分、33業種区分、ticker）
- 対象市場: プライム（内国株式）・スタンダード（内国株式）・グロース（内国株式）の合計3,707銘柄（2026年8月末時点）。ETF・ETN、REIT、PRO Market、外国株式は対象外
- バッチ処理: yfinanceへの一括リクエストは失敗しやすいため、200銘柄ずつに分割し、バッチ間に2秒の待機を挟んで取得（`screening.py`）
- 取得失敗の扱い: 失敗した銘柄は`failed_tickers.txt`に記録しスキップ（3,707銘柄中8銘柄が失敗。うち1件は社債型種類株式など特殊な銘柄でyfinance非対応は妥当と判断）
- 実行結果（2026年9月時点）: 取得成功3,699銘柄、流動性基準未達で除外2,181銘柄、フィルタ通過1,518銘柄

## 日次・月次のジョブ分割（終値の蓄積）

最終的なスクリーニングには日々の終値の蓄積が必要になるが、フィルタ判定（流動性など）まで毎回全銘柄を再計算するのは無駄が多いため、頻度で役割を分けている。

- **月次**（新規上場・上場廃止の反映、流動性フィルタの見直し）
  - `fetch_universe.py`: JPX公式データから`universe.csv`を再生成
  - `screening.py`: 更新された全銘柄で`period="1mo"`のデータを取得し、流動性フィルタ（10日中央値売買代金）を再計算、`screening_result.csv`に保存
- **日次**（終値・出来高の蓄積）
  - `daily_update.py`: `universe.csv`の全銘柄を対象に、OHLCV（Open/High/Low/Close/Volume）を`period="5d"`で取得し、取得できた全営業日分を保存（当初は最新行のみ保存していたが、実行漏れの日がそのまま欠損として残るため2026年9月に変更。直近5日以内の実行漏れなら次回実行で自動補完され、取引時間中に取得した暫定値も次回実行で確定値に上書きされる）
  - 結果を`daily_ohlcv.csv`（long形式: `date, ticker, open, high, low, close, volume`）に追記。同一日付・銘柄の組み合わせは上書きし重複を防止
  - ファイルは月単位で分割せず、単一ファイルに追記し続ける方針（新規上場・廃止は行の増減として自然に吸収されるため）
  - 当初はClose/Volumeのみ`daily_close.csv`/`daily_volume.csv`に分けて保存していたが、将来的なローソク足チャート描画には四本値（OHLC）が必要なため`daily_ohlcv.csv`に統合。yfinanceは元々OHLCVを一括取得しているため追加の取得コストは発生しない
  - 保存形式はlong形式（date, ticker, 値）を採用。wide形式（dateを行・tickerを列）は移動平均計算などで直感的だが、銘柄の新規上場・上場廃止のたびに列を追加・NaN埋めしてファイル全体を書き直す必要があり、月次で銘柄入れ替えがある本プロジェクトの運用と相性が悪いため見送り。分析時に必要ならその都度`pivot`でwide形式に変換する方針
  - 初回実行結果（2026年9月時点）: 取得成功3,704銘柄、失敗3銘柄（`daily_failed_tickers.txt`）

## スクリーニング条件（流動性フィルタ後のランキング）

流動性フィルタ通過後、さらにTOPIXに対する相対強度（Relative Strength）でランキングし上位を抽出する。条件を後から変更・追加できるよう、各条件を`filters.py`の独立した関数として定義し、`run_screening.py`で組み合わせる構成にしている。

- **ベンチマーク**: TOPIX指数自体（`^TOPX`, `998405.T`）はyfinanceで404となり取得不可だったため、TOPIX連動ETFの`1306.T`（NEXT FUNDS TOPIX連動型上場投信）を代替として採用
- **相対強度の算出方法**: 個別銘柄とTOPIX ETFの21営業日騰落率の差（個別銘柄の騰落率 − ベンチマークの騰落率）。IBD式RSレーティングのような複合期間の加重平均は複雑なため見送り、まずはシンプルな方式を採用
- **抽出基準**: 流動性フィルタ通過銘柄のうち、相対強度上位10%（当初5%で開始し、もう少し下位まで見たいとの要望で10%に拡大）
- **通知**: 日次ジョブ終了時に、前回から新規にランク入りした銘柄をDiscordへ通知（「通知（Discord Webhook）」セクション参照）
- **データ蓄積の前提**: 相対強度の計算には21営業日超のヒストリカルデータが必要なため、`backfill_history.py`で流動性フィルタ通過銘柄＋ベンチマークの過去分を一度だけバックフィルし、`daily_ohlcv.csv`に統合。以降は`daily_update.py`の日次更新で追随（ベンチマーク`1306.T`も日次取得対象に追加済み）。バックフィル期間は`2mo`→`3mo`→`1y`と段階的に拡大（週足表示や将来の長期EMAを見据えて、2026年9月時点で245日分、2025-09-18〜2026-09-18を蓄積。ファイルサイズは27MB・37万行程度で、CSVのまま数年は問題ない見込み）
- **ファイル構成**:
  - `storage.py`: CSVへの重複除外追記処理を共通化（`daily_update.py`と`backfill_history.py`で共用）
  - `filters.py`: `liquidity_passed_tickers()`（流動性フィルタ通過銘柄の抽出）、`load_close_wide()`（daily_ohlcv.csvをwide形式にpivot）、`relative_strength()`（相対強度の計算、単一時点）、`relative_strength_series()`（相対強度の時系列、ダッシュボードのチャート用）、`relative_strength_wide()`（全銘柄・全日付の相対強度）、`above_ema_wide()`（終値がEMAより上か）、`sector_breadth()`（条件を満たす銘柄の割合を業種別・日付別に集計）。後ろ3つはダッシュボードの「業種別」タブ用
  - `run_screening.py`: 上記を組み合わせて実行し、`rs_ranking.csv`に出力
- 実行結果（2026年9月時点）: 流動性フィルタ通過1,518銘柄中、相対強度計算成功1,517銘柄（1銘柄はデータ欠損）、上位10%（151銘柄）を抽出

## スクリーニング条件（ゴールデンクロス・初動検知）

RSが「今強い銘柄」を捉えるのに対し、「上昇に転換した初動」を捉える条件として、5EMAが25EMAを下から上に抜けるゴールデンクロスを追加。`filters.py`に条件を関数として追加する設計のおかげで、既存のRS条件に影響を与えずに追加できた。

- **判定方法**: `filters.py`の`golden_cross_tickers()`。短期EMA（5日）と長期EMA（25日）の差分を計算し、前日は差分が0以下・当日は0超になった日を「クロス発生日」として検出
- **対象母集団**: 流動性フィルタ通過銘柄（RSランキングと同じ）
- **検出範囲**: 直近3営業日以内にクロスが発生した銘柄（日次実行のタイミングのズレを考慮した余裕）
- **既知の制約**: データ蓄積期間が短いとEMAの「立ち上がり」段階で価格に敏感に反応し、通常より多めにクロスを検出する傾向がある（2026年9月時点、1,518銘柄中252〜261銘柄を検出、やや多め）。データ蓄積が進むにつれて精度が上がる見込み
- **出力**: `run_golden_cross.py`を実行し`golden_cross.csv`（ticker, クロス日, 銘柄名, 業種）に保存

## ダッシュボード（`dashboard.py`）

既存の`sector-rotation-dashboard`と同じ構成（Streamlit + Plotly）で、スクリーニング結果を閲覧するダッシュボードを実装。

- **タブ構成**: 「RS上位」「ゴールデンクロス」「業種別」の3タブ。前の2タブはそれぞれ独立したリスト+チャートの組み合わせで、`render_screening_tab()`として共通化（条件が増えてもこの関数を呼び出すだけで追加可能）。「業種別」は構成が異なるため`render_breadth_tab()`で別に描画
- **リスト選択**: `st.dataframe`の`on_select="rerun"` + `selection_mode="single-row"`で行クリックによる選択に対応（デフォルトの`st.dataframe`は表示専用のため明示的な設定が必要だった）
- **チャート内容**（`render_chart()`で共通化）:
  - ローソク足（陽線・陰線とも線と塗りつぶしを同色に統一、視認性を考慮してやや淡い色を採用）。隣り合うローソク足が詰まって見えたため、2026年10月に細めに調整: ひげ（実体の枠線も兼ねる）の太さは`line.width=1.2`（既定2）、実体の幅はレイアウトの`boxgap=0.4`（日付間隔に対する隙間の割合、既定0.3。実体幅は間隔の60%）
  - 25EMA・5EMA・75EMA（それぞれ別色。5EMAはゴールデンクロス判定と同じ期間で、クロスの様子を目視確認できる）
  - 25EMAを中心としたボリンジャーバンド±1σ・±2σ（ローソク足・EMAと被らない薄い青系統、塗りつぶし付き）
  - 下段サブプロットに対TOPIX相対強度（21営業日）の時系列。インジケータ的な位置づけなので主役のローソク足より小さい比率（高さ比0.8:0.2）で表示
- **既知の制約**:
  - 上記インジケータは全て過去データの蓄積量に依存するため、蓄積初期は表示期間の前半が空白になる（ウォームアップ期間）。データ蓄積が進めば解消
  - **開発上の注意**: Streamlitサーバーは`dashboard.py`本体の変更は自動検知して再実行するが、`import`しているモジュール（`filters.py`, `storage.py`など）はPythonの`sys.modules`にキャッシュされるため、それらを編集した場合はサーバーの再起動が必要（自動リロードでは反映されない）。Streamlit Cloudでも同様で、これらのモジュールを変更してpushした場合は「Manage app」→「Reboot app」で再起動する（2026年10月、`filters.py`に関数を追加してpushした際に、新しい`dashboard.py`が古い`filters.py`を参照して`ImportError`になった）
- 依存関係として`streamlit`・`plotly`を追加
- **表示期間セレクタ**（2026年9月追加）: サイドバーに「1ヶ月/3ヶ月/6ヶ月/1年/全期間」の`st.selectbox`を追加（デフォルト6ヶ月）。EMA・ボリンジャーバンド・RSは常に全期間データで計算してから表示範囲を`display_days`でスライスする設計にし、期間を短く絞ってもインジケータ序盤が不自然にならないようにしている（`render_chart()`内で計算→スライスの順序を徹底）
- **休場日を詰めたチャート表示**（2026年10月追加）: 日付軸のままだと土日・祝日が空白として表示されるため、Plotlyの`rangebreaks`で表示範囲内のデータのない日をx軸から除外（`render_chart()`）。除外日は「表示範囲の全日付 − その銘柄のデータがある日」で算出するため、祝日カレンダーを別途持つ必要がなく、売買停止日も同時に詰められる。`fig.update_xaxes`で上段（ローソク足）・下段（RS）の両方に適用し、上下の日付位置を揃えている。x軸をカテゴリ型（日付を文字列ラベルとして並べる）にする案は、目盛りの自作が必要で上下段の対応も崩れやすいため不採用
- **データ基準日の表示**（2026年9月追加）: タイトル直下に「データ基準日: YYYY-MM-DD（曜日）の終値」を表示し、どの日の終値に基づく結果かを分かるようにした（`data_as_of()`）。基準日は全銘柄の最新日ではなくベンチマーク`1306.T`の最新日を採用（一部銘柄に取引時間中の暫定値が混ざっても基準日がずれないようにし、RS計算の基準とも揃えるため）。日次ジョブの実行日時ではなくデータの日付を表示するのは、Streamlit Cloud上ではファイルの更新日時がリポジトリ取り込み時刻になり信頼できないため。データが古いままの場合の鮮度警告（例: 基準日が5日以上前なら`st.warning`）は今回は見送り（入れる場合は、GW・年末年始などの連休中に誤って警告が出る点への対処が必要）
- **業種別タブ**（2026年10月追加）: 流動性フィルタ通過銘柄（RSランキングと同じ母集団）のうち、条件を満たす銘柄の割合を33業種別に表示
  - 指標: 「RSがプラス（対TOPIX 21営業日）」「25EMAより上」「75EMAより上」を`st.radio`で切り替え。移動平均はチャートの線と目視で照合できるようEMAを採用（一般に公表される「25日線より上の比率」はSMAが多いため、外部の数値とは一致しない）。200日線はデータ蓄積が約1年分で推移を見られる期間がほとんどないため見送り
  - 計算: 各指標を「条件を満たすか」の1/0/NaN（NaN=終値欠損などで判定不可、母数から除外）のwide形式で作り、`sector_breadth()`で業種別・日付別の割合と銘柄数に集計する共通構成。指標の追加は1/0/NaNを作る関数を足すだけで済む。新しいCSVや日次ジョブの変更は不要で、ダッシュボード上で毎回計算する（3指標とも全日付で約1.3秒、`st.cache_data`で10分キャッシュ）
  - 左: 最新日の業種別の割合を横棒グラフ（割合の高い順）で表示し、全体の割合を点線で表示。ラベルは「割合（銘柄数）」。銘柄数10未満の業種は割合が振れやすいため、銘柄数をオレンジ太字＋※で表示（棒の色は変えない）
  - 右: 選択した業種の割合の推移（サイドバーの表示期間に連動）。業種選択時は比較用に全体を灰色点線で重ね、50%に薄い破線を表示
  - 業種の選択は`st.session_state`で保持。選択肢の並び順は指標ごとの割合順で、指標を切り替えると並び順が変わりStreamlitが別ウィジェットとして作り直すため、保持しないと選択が「全体」に戻る
  - 75EMAはデータ先頭の数か月が助走期間のため、「全期間」表示の序盤は参考値

## データ品質の問題と対応（ベンチマークの異常値）

2026年4月末〜5月頭にかけて、ほぼ全銘柄でRSが-970%前後という明らかな異常値が発生。複数の無関係な銘柄で同時期に同程度の異常値が出ていたことから、個別銘柄ではなくベンチマーク側の問題と特定。

- **原因**: ベンチマーク`1306.T`のyfinanceデータで、2026-03-30・03-31の2日間だけ終値が約37円（前後の日は375〜382円、約1/10.2）という異常値になっていた。RSの21営業日窓の比較対象としてこの異常値が使われ、4月末〜5月頭の計算に混入していた
- **切り分け**: `yf.download`の再取得、`auto_adjust=False`での取得のどちらでも同じ異常値が返り、分割・配当イベントの記録もなかったため、Yahoo Finance側のソースデータ自体の欠損と判断（yfinance側のバグや設定ミスではない）
- **対応**: 正しい値の取得ができないため、前後の営業日（2026-03-27, 2026-04-01）から線形補間した値で`daily_ohlcv.csv`の該当2日分を手動修正。修正後、異常値は解消（-970%前後 → 通常範囲の16〜49%程度）を確認
- **今後の懸念**: 同様の孤立した異常値が他の銘柄にも潜んでいる可能性がある。現状は個別に気づいた都度対応する方針だが、頻発するようなら`load_close_wide()`への汎用的な異常値検出・除去ロジックの追加を検討

## 定期実行の方針

`daily_update.py`（および月次の`fetch_universe.py`/`screening.py`）を自動実行する方式として、GitHub Actionsではなく**Mac miniの`LaunchDaemon`**を採用することに決定。

- **比較した選択肢**: GitHub Actions／ローカルのlaunchd（Mac mini）／VPS常時cron／サーバーレス（AWS Lambda等）／PythonAnywhere等のPaaS
- **launchdを選んだ理由**: 常時稼働のMac miniが既にあるため、GitHub Actionsの最大の利点（「自分のPCが起きていなくても実行できる」）が意味を持たない。一方でGitHub Actionsはランナーが使い捨てのため、`daily_ohlcv.csv`への追記結果をgit commit＆pushで書き戻す仕組みが別途必要になり、その分セットアップの手間が増える。launchdなら今のローカル運用のコード・データの持ち方を一切変えずに済む
- **LaunchAgentではなくLaunchDaemonを採用する理由**: `LaunchAgent`はGUIログインセッションが必要だが、`LaunchDaemon`はログイン状態に関係なく動作する。今回のジョブは画面表示が不要なバックグラウンド処理なので、Mac miniのログイン状態を気にしなくて済む`LaunchDaemon`の方が適している（実行ユーザーは`plist`の`UserName`キーで指定する）
- **Streamlit Cloudとの連携**: ダッシュボードはGitHubリポジトリと連携してStreamlit Community Cloud上にデプロイ済み。ダッシュボードが参照するCSVはリポジトリ内のものなので、Mac miniで`daily_update.py`を実行した後は**git commit＆pushまで自動化する**必要がある（LaunchDaemonから呼ぶスクリプト内に組み込む想定）
- **開発体制**（2026年9月27日変更）: データの取得・更新を常時稼働のMac miniで行うことになったため、開発もマシンごとに役割を分ける
  - **データ取得・スクリーニング系**（`daily_update.py`, `backfill_history.py`, `run_screening.py`, `run_golden_cross.py`, `filters.py`, `scripts/`）: Mac miniで開発する。実データ・実行環境（LaunchDaemon）がその場にあり検証しやすいため。MacBook Airで開発すると、動作確認が翌営業日18:00の定期実行を待つことになり効率が悪い
  - **通知**（`notify.py`、手順12）: Mac miniで開発する。日次ジョブ（`scripts/daily_job.sh`）の末尾に組み込まれており、実データでの送信テストもMac miniでしか行えないため
  - **ダッシュボード**（`dashboard.py`）: MacBook Air・Mac miniのどちらで開発してもよい
  - マシン間の同期はGitHubの`develop`ブランチ経由で行う。Mac miniは平日毎日データ更新をpushするため、MacBook Airで作業を始める前には必ず`git pull`する。MacBook AirではデータCSV（`daily_ohlcv.csv`など）を編集・commitしない（競合を避けるため）
  - Mac miniでの開発時の注意: 日次ジョブは開発と同じ作業ツリーで動く。平日18:00の時点で`develop`以外のブランチにいたり、コードに未commitの変更があったりすると、意図しないブランチへのcommitや書きかけのコードの実行が起こりうる。18:00前後は`develop`にいて、コードの変更はcommit済みにしておく（`daily_job.sh`冒頭への安全チェック追加を検討中）
  - Claude Codeのセッション履歴はマシンごとに独立しており引き継がれないため、このREADME.mdがマシン間・セッション間の引き継ぎの起点になる
- **Mac mini側のセットアップ状況**（2026年9月27日時点）
  - `uv`を公式インストーラで導入（`~/.local/bin/uv`）し、`uv sync`で環境構築済み。gitのユーザーはリポジトリローカルに`abesegg <abe.segg@gmail.com>`を設定済み。push用のSSH鍵は`~/.ssh/id_ed25519`（個人鍵・パスフレーズなし）。リポジトリ専用のDeploy keyへの切り替えは保留中
  - `scripts/daily_job.sh`: LaunchDaemonから呼ばれる日次ジョブ。`git pull --rebase` → `daily_update.py` → `run_screening.py` → `run_golden_cross.py` → 変更があれば`develop`へcommit＆push → Discordへ結果を通知。途中で失敗した場合はcommitしない（`set -e`）。LaunchDaemonは環境変数が最小限のため、`HOME`・`PATH`をスクリプト内で明示的に設定している
  - `scripts/com.abesegg.jp-stock-screener.daily.plist`: 平日（月〜金）18:00に上記を実行。ログは`logs/daily_job.log`（git管理外）
  - **`~/Documents`の外への移動**: 当初は`~/Documents/claude-work`に置いていたが、macOSのプライバシー保護（TCC）によりLaunchDaemonから`~/Documents`配下にアクセスできず、`posix_spawn(/bin/bash) ... Operation not permitted`（exit code 78: EX_CONFIG）で起動に失敗した。`/bin/bash`へのフルディスクアクセス付与は影響範囲が広すぎるため見送り、`claude-work`ごと`~/claude-work`へ移動する方針とした（移動後は`.venv`内の絶対パスが壊れるため`uv sync`で再作成する）。移動・`uv sync --reinstall`（`.venv`内に旧パスが残っていたため）・LaunchDaemon再登録を実施し、`kickstart`での手動実行が正常終了（exit code 0）したことを確認済み（2026年9月27日）
  - 登録・解除・手動実行・状態確認のコマンド（plistを変更した場合は`bootout` → `cp` → `bootstrap`で再登録する）:
    ```bash
    sudo cp scripts/com.abesegg.jp-stock-screener.daily.plist /Library/LaunchDaemons/
    sudo launchctl bootstrap system /Library/LaunchDaemons/com.abesegg.jp-stock-screener.daily.plist
    sudo launchctl bootout system/com.abesegg.jp-stock-screener.daily
    sudo launchctl kickstart system/com.abesegg.jp-stock-screener.daily
    launchctl print system/com.abesegg.jp-stock-screener.daily
    ```
  - 失敗時の調査: `logs/daily_job.log`に何も出ていない場合はlaunchd側で起動に失敗している。`/usr/bin/log show --last 30m --predicate 'eventMessage CONTAINS "jp-stock-screener"'`で原因を確認する（zshでは組み込みの`log`と衝突するためフルパスで実行する）
  - 月次ジョブ（`fetch_universe.py`/`screening.py`）の自動化は未対応

## 通知（Discord Webhook）

日次ジョブの結果をDiscordへ通知する（`notify.py`、2026年9月追加）。

- **方式の選択**: Discordプラグイン（Claude Codeのチャンネル機能）はClaude Codeセッションの起動中しか動かず、LaunchDaemonからの無人実行には向かないため、Discord Webhookへ直接POSTする方式を採用。標準ライブラリ（`urllib`）のみで実装し依存関係の追加なし
- **成功時**: RS上位・ゴールデンクロスそれぞれの総数と、前回から**新規に入った銘柄**（各最大10件、超過分は「…ほか○銘柄」）、ダッシュボードのリンク（`<>`で囲みリンクプレビューを抑止）を通知
- **新規判定**: `daily_job.sh`が`git pull`直後のcommitを`BASE_REF`として記録し、そのcommit時点のCSV（`git show`）と今回のCSVを比較。前日の結果はcommit済みなので別途状態ファイルを持つ必要がない
- **失敗時**: `trap ERR`で失敗した行番号とexit codeを通知。通知自体の失敗ではジョブを失敗扱いにしない（`|| true`）
- **設定**: Webhook URLとダッシュボードURLは`.env`（git管理外、権限600）に記載。GitHubにpushしているリポジトリのため、URLをコードに書かない
    ```
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
    DASHBOARD_URL=https://〇〇.streamlit.app
    DISCORD_MENTION_USER_ID=...  # 任意。自分のDiscordユーザーID（下記「スマホに通知が届かない件」参照）
    ```
- **動作確認**: `uv run python notify.py success --base <git ref> --dry-run`で送信せずに通知文を表示できる
- **1メッセージの文字数**: Discordの上限（2000文字）を超える場合は切り詰める（通常は700文字程度）
- **スマホに通知が届かない件**（2026年10月7日解決）: 実際にはスマホに通知は届いていたが、Discordのアプリアイコンのバッジは自分宛てのメンションとDMしか数えないため、Webhookの投稿ではバッジが付かず見落としていた。対策として、`.env`に`DISCORD_MENTION_USER_ID`（自分のDiscordユーザーID）を設定すると通知の先頭に自分宛てのメンションを付けるようにした（`allowed_mentions`で対象を自分だけに限定）。未設定ならメンションなしで送信する。テスト送信でバッジが付くことを確認済み（調査の過程で、iOSの集中モード・時刻指定要約、ブラウザでのDiscord表示中のプッシュ抑止は原因でないことを確認）

## 検討して見送った代替案

- GAS + J-Quants: J-Quants無料プランはデータ遅延が大きく直近終値が取れない、GASは実行時間制限（最大6分）や集計処理の書きにくさがあるため不採用。現行のyfinance + ローカル実行案を継続

## 現在の進捗

プロジェクト初期化・依存関係導入・株価取得（`main.py`）・流動性フィルタ（`screening.py`）・100銘柄への拡大（`tickers.py`）・東証全銘柄への拡大とローカルCSV出力（`fetch_universe.py`, `universe.csv`, `screening_result.csv`）・日次OHLCV蓄積（`daily_update.py`, `daily_ohlcv.csv`）・TOPIX相対強度スクリーニング（`filters.py`, `run_screening.py`, `backfill_history.py`, `rs_ranking.csv`）・ゴールデンクロススクリーニング（`run_golden_cross.py`, `golden_cross.csv`）・閲覧用ダッシュボード（`dashboard.py`）・ダッシュボードのStreamlit Community Cloudデプロイ・定期実行方式の決定（Mac miniの`LaunchDaemon`）まで完了。Mac mini側のセットアップは`uv`導入・`uv sync`・日次ジョブのスクリプト（`scripts/daily_job.sh`）とplistの作成（手動テストでcommit＆pushまで成功済み）、TCC回避のための`~/claude-work`への移動・`uv sync`・LaunchDaemonの再登録・`kickstart`での手動実行まで完了。Discord Webhookによる日次通知（`notify.py`）も実装済み（詳細は「通知（Discord Webhook）」参照）。2026-09-28(月)から平日18:00の定期実行が稼働しており、データ更新・commit＆push・Discord通知の正常動作を確認済み。開発はデータ取得系をMac mini、ダッシュボードはどちらでも行う体制に変更（「開発体制」参照）。ダッシュボードにデータ基準日の表示・休場日を詰めたチャート表示・業種別タブ（RSプラス／25EMA・75EMAより上の銘柄の割合）を追加。残っている課題は、`daily_job.sh`冒頭への安全チェックの追加（「開発体制」参照）と月次ジョブの自動化。
