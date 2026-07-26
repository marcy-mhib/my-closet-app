# デプロイ手順（家族・友人数人での利用を想定）

## 推奨ホスティング先: PythonAnywhere

このアプリは SQLite（`instance/fashion.db`）とアップロード画像をローカルディスクに
保存する構成です。Render や Fly.io の無料プランはコンテナ再起動のたびにローカル
ファイルが消えるため、このままの構成では画像や登録データが消失します。
PythonAnywhere の無料プランはファイルシステムが永続化されるため、コード変更なし
でそのまま運用できます。

（Docker に抵抗がなく、より本格的に運用したい場合は Fly.io + Volume も選択肢です。
その場合はコード変更なしで永続ボリュームをマウントするだけで動きます。）

## 準備状況（このリポジトリ側は完了済み）

- [x] `requirements.txt`（依存パッケージ一覧）
- [x] `Procfile`（`web: gunicorn app:app`）
- [x] `.gitignore`（`.env` / DB / アップロード画像 / venv を除外）
- [x] `git init` して初回コミット済み

## 手順

1. GitHub 等にこのリポジトリを push する（`.env` は `.gitignore` 済みなので漏れません）。
2. https://www.pythonanywhere.com/ で無料アカウントを作成。
3. Bash コンソールを開き、リポジトリを clone する。
   ```
   git clone <あなたのリポジトリURL>
   ```
4. 仮想環境を作成して依存関係をインストール。
   ```
   mkvirtualenv --python=/usr/bin/python3.12 fashion-env
   pip install -r requirements.txt
   ```
5. クローンしたディレクトリに `.env` を新規作成し、以下を本番用の値で設定する
   （ローカルの `.env` をそのままコピーしない — 下記チェックリスト参照）。
6. 「Web」タブ → 「Add a new web app」→ Manual configuration → Python 3.12 を選択。
7. 「Virtualenv」欄に手順4で作った仮想環境のパスを設定。
8. 「WSGI configuration file」を編集し、`app.py` の `app` オブジェクトを読み込むよう
   PythonAnywhere のテンプレートに沿って修正。
9. 「Reload」ボタンを押すと `https://<username>.pythonanywhere.com` で公開される。

## 本番投入前のチェックリスト

- [ ] `SECRET_KEY` と `INVITE_CODE` は開発中に使った値を流用せず、本番用に再生成する
- [ ] `FLASK_DEBUG` は本番では `False`（未設定でも既定で False）
- [ ] 招待コードは家族・友人にだけ個別に共有する
- [ ] 最初にアクセスした人が最初のアカウントを登録すると、既存のテストデータ
      （「Tシャツ」など）がそのアカウントに自動的に引き継がれる
- [ ] `AI機能`（画像自動判定・AIコーデ提案）を使う場合は `ANTHROPIC_API_KEY` を設定する
