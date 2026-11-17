# keirin-predictor

競輪の予想アプリ（個人利用・非営利）。設計書は Claude のドキュメント「競輪予想アプリ 設計書」。
このリポジトリにはコードだけを置き、取得したデータ・記録・収支は Google ドライブ（非公開）に置く。

## データの取得元
| 取得元 | 取るもの | 備考 |
| --- | --- | --- |
| チャリロト | 出走表・全オッズ・並び予想・結果・払戻・競輪場データ・開催カレンダー | robots.txt 制限なし。3秒以上の間隔で取得 |
| netkeirin | 出走表（H回数など）・並び予想・本紙見解・選手コメント | 規約により私的利用に限る。データは公開しない |
| 気象庁 | 最寄り観測地点の10分ごとの風向・風速・気温 | 出典：気象庁ホームページ（過去の気象データ検索）を加工して作成 |
| Open-Meteo | 気象庁が取れないときの風（座標） | |
| 国土地理院 | 競輪場の住所→座標（config/venues.json 作成時のみ） | |

## 主なコマンド
- `python -m keirin.scripts.collect --start 2026-11-16 --end 2026-11-16`：期間のデータを取得（data/ に月ごとのParquet）
- `python -m keirin.scripts.collect --start 今日 --end 今日 --today-mode`：当日の出走表・並び予想・朝のオッズ
- `python -m keirin.scripts.build_venues`：競輪場マスタ config/venues.json を作り直す

## GitHub Actions
- daily-fetch：毎日10:30（JST）に前日の確定分と当日分
- backfill-auto：6時間ごとに config/backfill_range.txt の期間を月単位で続きから
- build-venues：手動。競輪場マスタを作り直してコミット

Secrets：RCLONE_CONFIG（Google ドライブ。保存先 gdrive:keirin-predictor/data）
