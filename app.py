# ============================================================
# クロークノート - Flaskアプリ本体
#
# ファイル内の構成(上から順に):
#   1. 初期設定(Flask / DB / ログイン管理の準備)
#   2. 定数(カテゴリ・季節・都市名マップ・色パレットなど)
#   3. モデル定義(User / Clothes / ClothesImage / Coordinate)
#   4. DBマイグレーション処理
#   5. ヘルパー関数(ルートから呼び出す共通処理)
#   6. ルート(認証 → クローゼット → コーディネート → AI・画像解析 → 天気)
# ============================================================

import base64
import io
import json
import math
import os
import time
import uuid
from collections import Counter, deque
from datetime import date, datetime
from itertools import combinations

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from PIL import Image, ImageChops, ImageFilter
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from utils import get_template_folder

# ---- 1. 初期設定 ----
load_dotenv()  # .envファイルの中身を環境変数として読み込む

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fashion.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY is not set. Please define it in the .env file.')

csrf = CSRFProtect(app)  # 全POSTフォームにCSRFトークンを必須にする


@app.context_processor
def inject_template_folder():
    """全テンプレートで template_folder ('pc' か 'mobile') を使えるようにする。

    base.html がヘッダーを出し分けるのに使っている。ルート側を書き換えなくても
    端末に合わせたヘッダーが読み込まれる。
    """
    return {'template_folder': get_template_folder()}


db = SQLAlchemy(app)  # モデル定義・DB操作の窓口

login_manager = LoginManager(app)
login_manager.login_view = 'login'  # 未ログインで@login_requiredのページに来たら/loginへ飛ばす
login_manager.login_message = 'ログインしてください'

# ---- 2. 定数 ----
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
CATEGORIES = ['トップス', 'ボトムス', 'アウター', 'シューズ', 'アクセサリー', '帽子', 'バッグ', 'サングラス']
SEASONS = ['春', '夏', '秋', '冬', 'オールシーズン']
DEFAULT_CITY = '東京'

# コーディネート詳細画面で、実際に着る順番(頭側→足元)に近い並びで表示するための優先順位
CATEGORY_ORDER = ['帽子', 'サングラス', 'アクセサリー', 'アウター', 'トップス', 'ボトムス', 'シューズ', 'バッグ']

# 主要な都道府県名・都市名の緯度経度(気象庁アメダスの観測地点表を元にした座標)。
# Open-Meteo(気象庁の予報モデルを含む)に問い合わせる際の位置指定に使う。
# 地点そのものが無い市区町村は、隣接する地点の座標で代用している(コメントに実際の地点名を記載)。
CITY_COORDINATES = {
    '北海道': (43.06, 141.33), '札幌': (43.06, 141.33),
    '青森': (40.82, 140.77), '岩手': (39.70, 141.16), '盛岡': (39.70, 141.16),
    '宮城': (38.26, 140.90), '仙台': (38.26, 140.90),
    '秋田': (39.72, 140.10), '山形': (38.26, 140.34), '福島': (37.76, 140.47),
    '茨城': (36.38, 140.47), '水戸': (36.38, 140.47),
    '栃木': (36.55, 139.87), '宇都宮': (36.55, 139.87),
    '群馬': (36.41, 139.06), '前橋': (36.41, 139.06),
    '埼玉': (35.88, 139.59), '千葉': (35.60, 140.10), '東京': (35.69, 139.75),
    '神奈川': (35.44, 139.65), '横浜': (35.44, 139.65), '川崎': (35.44, 139.65),  # 川崎は横浜地点で代用
    '新潟': (37.89, 139.02), '富山': (36.71, 137.20), '石川': (36.59, 136.63), '金沢': (36.59, 136.63),
    '福井': (36.05, 136.22), '山梨': (35.67, 138.55), '甲府': (35.67, 138.55),
    '長野': (36.66, 138.19), '岐阜': (35.40, 136.76), '静岡': (34.98, 138.40),
    '愛知': (35.17, 136.97), '名古屋': (35.17, 136.97), '三重': (34.73, 136.52), '津': (34.73, 136.52),
    '滋賀': (34.99, 135.91), '京都': (35.01, 135.73), '大阪': (34.68, 135.52),
    '兵庫': (34.70, 135.21), '神戸': (34.70, 135.21),
    '奈良': (34.67, 135.84), '和歌山': (34.23, 135.16),
    '鳥取': (35.49, 134.24), '島根': (35.46, 133.06), '松江': (35.46, 133.06),
    '岡山': (34.69, 133.93), '広島': (34.40, 132.46), '山口': (34.16, 131.46),
    '徳島': (34.07, 134.57), '香川': (34.32, 134.05), '高松': (34.32, 134.05),
    '愛媛': (33.84, 132.78), '松山': (33.84, 132.78), '高知': (33.57, 133.55),
    '福岡': (33.58, 130.38), '佐賀': (33.27, 130.31), '長崎': (32.73, 129.87), '熊本': (32.81, 130.71),
    '大分': (33.23, 131.62), '宮崎': (31.94, 131.41), '鹿児島': (31.55, 130.55),
    '沖縄': (26.21, 127.69), '那覇': (26.21, 127.69),
    # 天気ページの県内マップで使う、都道府県庁所在地以外の市区町村。
    # Open-Meteoは観測地点ではなく格子データなので、代用地点ではなく市役所のある実際の座標を使う
    # (マップ上の位置計算にもこの座標をそのまま使うため、実際の位置関係とずれない)。
    '北九州市': (33.88, 130.88), '久留米市': (33.32, 130.51),
    '大牟田市': (33.03, 130.45), '飯塚市': (33.65, 130.69),
    '福山市': (34.49, 133.36), '呉市': (34.25, 132.57), '三次市': (34.81, 132.85),
    '高槻市': (34.85, 135.62), '東大阪市': (34.68, 135.60),
    '堺市': (34.57, 135.48), '岸和田市': (34.46, 135.37),
    '青梅市': (35.79, 139.28), '立川市': (35.71, 139.41),
    '八王子市': (35.67, 139.32), '町田市': (35.55, 139.44),
}
JAPAN_ADMIN_SUFFIXES = ('都', '道', '府', '県', '市')

# 天気ページの県内マップ用データ(県の輪郭と、そこに並べる主要都市5つ)。
# tools/build_prefecture_data.py が生成したJSONを起動時に一度だけ読み込む。
#   輪郭: 国土数値情報(国土交通省)由来の行政区域データを、1県90点まで簡略化した(緯度, 経度)の列
#   都市: 県庁所在地を起点に、県内へまんべんなく散るよう選んだ5市(緯度経度つき)
# どちらも離島は除いてある(形が読み取りにくくなるため)。
PREFECTURE_DATA_PATH = os.path.join(app.root_path, 'static', 'data', 'prefectures.json')
with open(PREFECTURE_DATA_PATH, encoding='utf-8') as _prefecture_file:
    PREFECTURE_DATA = json.load(_prefecture_file)

# 県内マップに出てくる市名も、都市変更フォームから検索できるように登録しておく。
# 複数の県に同じ市名がある場合はどちらを指すか決められないので、その名前は登録しない。
_city_name_counts = Counter(city['name'] for pref in PREFECTURE_DATA.values() for city in pref['cities'])
for _pref in PREFECTURE_DATA.values():
    for _city in _pref['cities']:
        if _city_name_counts[_city['name']] == 1:
            CITY_COORDINATES.setdefault(_city['name'], (_city['lat'], _city['lon']))

# 県内マップのカードが重ならないよう押し広げるときに使う、見た目のサイズの目安(px)。
# style.css の .pref-city の大きさ・.pref-map-inner の --map-height と揃えておくこと。
# スマホは地図もカードも小さいので、別の値を使わないとカードが重なってしまう。
MAP_SIZES = {
    'pc': {'card_w': 74, 'card_h': 60, 'max_w': 452, 'max_h': 400},
    'mobile': {'card_w': 62, 'card_h': 50, 'max_w': 300, 'max_h': 330},
}


def build_prefecture_shape(outline):
    """(緯度, 経度)の多角形を、天気ページのマップ用のSVG情報に変換する。

    都市カードの位置も同じ緯度経度の範囲(bounds)から%で計算するので、
    輪郭と都市の位置関係が必ず一致する。
    """
    lats = [lat for lat, _ in outline]
    lons = [lon for _, lon in outline]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_span, lon_span = lat_max - lat_min, lon_max - lon_min
    # 経度1度の実距離は高緯度ほど縮むので、cosで補正して実際の県の縦横比に近づける
    aspect = lon_span * math.cos(math.radians((lat_min + lat_max) / 2)) / lat_span
    height = 1000.0
    width = height * aspect
    points = [
        f'{(lon - lon_min) / lon_span * width:.1f} {(lat_max - lat) / lat_span * height:.1f}'
        for lat, lon in outline  # 緯度が高い(北)ほどyが小さい=上になる
    ]
    return {
        'path': 'M ' + ' L '.join(points) + ' Z',
        'viewbox': f'0 0 {width:.1f} {height:.1f}',
        'aspect': f'{aspect:.3f}',
        'bounds': (lat_min, lat_max, lon_min, lon_max),
    }


PREFECTURE_SHAPES = {key: build_prefecture_shape(pref['outline']) for key, pref in PREFECTURE_DATA.items()}

COLOR_PALETTE = {
    '白': (255, 255, 255),
    '黒': (25, 25, 25),
    'グレー': (130, 130, 130),
    'ネイビー': (30, 40, 70),
    'ベージュ': (222, 202, 168),
    '茶': (101, 67, 33),
    'カーキ': (150, 140, 90),
    'デニム': (66, 99, 130),
    '赤': (200, 40, 40),
    'ピンク': (240, 170, 190),
    'オレンジ': (230, 130, 40),
    '黄色': (230, 200, 40),
    '緑': (60, 130, 70),
    '青': (40, 100, 190),
    '水色': (120, 190, 220),
    '紫': (110, 70, 140),
}

# ---- 3. モデル定義 ----
# Coordinate(コーディネート)とClothes(服)は「多対多」の関係なので、
# 中間テーブル(どのコーデにどの服が入っているかの対応表)が別途必要になる。
coordinate_items = db.Table(
    'coordinate_items',
    db.Column('coordinate_id', db.Integer, db.ForeignKey('coordinate.id'), primary_key=True),
    db.Column('clothes_id', db.Integer, db.ForeignKey('clothes.id'), primary_key=True),
)


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)  # パスワードは平文で保存せず、ハッシュ化した値だけ持つ
    city = db.Column(db.String(100))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    # ログイン中かどうかをFlask-Loginが確認するたびに呼ばれ、current_userに詰める中身を返す
    return db.session.get(User, int(user_id))


class Clothes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # user_idはNULL許容にしている(認証機能を後付けした際、持ち主未定のまま残るデータがあるため)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50))
    color = db.Column(db.String(50))
    season = db.Column(db.String(50))
    is_favorite = db.Column(db.Boolean, default=False, nullable=False)
    wear_count = db.Column(db.Integer, default=0, nullable=False)
    last_worn_at = db.Column(db.Date)
    price = db.Column(db.Integer)
    images = db.relationship('ClothesImage', backref='clothes', lazy=True, cascade='all, delete-orphan')


class ClothesImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clothes_id = db.Column(db.Integer, db.ForeignKey('clothes.id'), nullable=False)
    image_path = db.Column(db.String(200), nullable=False)
    # 背景を透過した切り抜き画像。現在は生成しておらず、過去に作った分だけが残っている
    cutout_path = db.Column(db.String(200))
    # アイテムの周りの余白を切り落としたサムネイル。コーデ一覧で縦に隙間なく積むために使う
    thumb_path = db.Column(db.String(200))

    @property
    def thumbnail_path(self):
        """コーデ一覧で使う画像パス(余白を落としたサムネイル。作れていなければ元画像)。"""
        return self.thumb_path or self.image_path


class Coordinate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.Date, default=date.today, nullable=False)
    items = db.relationship('Clothes', secondary=coordinate_items, backref='coordinates')


# ---- 4. DBマイグレーション ----
# db.create_all()は「まだ無いテーブル」しか作らないので、既存のDBファイルに
# 新しいカラムを追加したときは、ここで手動でALTER TABLEして追いつかせる。
def run_migrations():
    with db.engine.connect() as conn:
        existing_cols = {row[1] for row in conn.execute(text('PRAGMA table_info(clothes)'))}
        statements = []
        if 'is_favorite' not in existing_cols:
            statements.append('ALTER TABLE clothes ADD COLUMN is_favorite BOOLEAN DEFAULT 0 NOT NULL')
        if 'wear_count' not in existing_cols:
            statements.append('ALTER TABLE clothes ADD COLUMN wear_count INTEGER DEFAULT 0 NOT NULL')
        if 'last_worn_at' not in existing_cols:
            statements.append('ALTER TABLE clothes ADD COLUMN last_worn_at DATE')
        if 'price' not in existing_cols:
            statements.append('ALTER TABLE clothes ADD COLUMN price INTEGER')
        if 'user_id' not in existing_cols:
            statements.append('ALTER TABLE clothes ADD COLUMN user_id INTEGER')
        for statement in statements:
            conn.execute(text(statement))

        existing_coord_cols = {row[1] for row in conn.execute(text('PRAGMA table_info(coordinate)'))}
        if 'user_id' not in existing_coord_cols:
            conn.execute(text('ALTER TABLE coordinate ADD COLUMN user_id INTEGER'))

        existing_user_cols = {row[1] for row in conn.execute(text('PRAGMA table_info(user)'))}
        if 'city' not in existing_user_cols:
            conn.execute(text('ALTER TABLE user ADD COLUMN city VARCHAR(100)'))

        existing_image_cols = {row[1] for row in conn.execute(text('PRAGMA table_info(clothes_image)'))}
        if 'cutout_path' not in existing_image_cols:
            conn.execute(text('ALTER TABLE clothes_image ADD COLUMN cutout_path VARCHAR(200)'))
        if 'thumb_path' not in existing_image_cols:
            conn.execute(text('ALTER TABLE clothes_image ADD COLUMN thumb_path VARCHAR(200)'))
        conn.commit()


# サムネイルの作り方を変えたらこの番号を上げる。ファイル名に埋め込んでいるので、
# 番号が違う=古い作り方のサムネイルとみなして起動時に自動で作り直される。
# 3: 切り抜き画像ではなく元写真から作るようにした(背景ごと表示する方針に変更したため)
THUMB_VERSION = 3


def thumbnail_filename():
    return f'{uuid.uuid4().hex}_thumb{THUMB_VERSION}.png'


def needs_thumbnail(record):
    """サムネイルが無いか、古い作り方のままなら作り直しが必要。"""
    return not record.thumb_path or f'_thumb{THUMB_VERSION}.' not in record.thumb_path


def backfill_thumbnails():
    """サムネイルが無い画像・古い作り方の画像に、今の方式のサムネイルをまとめて作る。

    既存の登録画像を今の表示方式に追いつかせるための処理で、
    作り終われば以降はスキップされる(作れなかった画像は元画像のまま表示される)。
    """
    pending = [record for record in ClothesImage.query.all() if needs_thumbnail(record)]
    for record in pending:
        source = os.path.join(app.root_path, 'static', record.image_path)
        thumb_name = thumbnail_filename()
        if not os.path.exists(source) or not build_flat_thumbnail(
                source, os.path.join(app.config['UPLOAD_FOLDER'], thumb_name)):
            continue
        if record.thumb_path:  # 作り直せたら古いファイルは残さない
            outdated = os.path.join(app.root_path, 'static', record.thumb_path)
            if os.path.exists(outdated):
                os.remove(outdated)
        record.thumb_path = f'uploads/{thumb_name}'
    if pending:
        db.session.commit()


with app.app_context():
    db.create_all()  # まだ無いテーブルだけ新規作成(既存テーブルには触らない)
    run_migrations()  # 既存テーブルへのカラム追加はこちらの担当


# ---- 5. ヘルパー関数 ----
# ここから下はルート(@app.route)から呼び出される共通処理。

def allowed_file(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in ALLOWED_EXTENSIONS


def save_clothes_image(image, clothes_id):
    """アップロード画像を検証し、安全なファイル名で保存してClothesImageを返す(未選択/不正な形式ならNone)。"""
    if not image or image.filename == '':
        return None
    filename = secure_filename(image.filename)
    if not filename or not allowed_file(filename):
        return None
    ext = filename.rsplit('.', 1)[1].lower()
    # 元のファイル名は使わずUUIDで生成し直す(同名ファイルによる上書き・パストラバーサル対策)
    unique_name = f'{uuid.uuid4().hex}.{ext}'
    image_path_abs = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    image.save(image_path_abs)

    # コーデ一覧用に、アイテムの周りの余白を落としたサムネイルを作っておく。
    # 背景の切り抜き(generate_cutout)は、うまく抜ける写真と抜けない写真が混ざって
    # 一覧の見た目がそろわないため使っていない。写真は背景ごとそのまま表示する。
    thumb_path = None
    thumb_name = thumbnail_filename()
    if build_flat_thumbnail(image_path_abs, os.path.join(app.config['UPLOAD_FOLDER'], thumb_name)):
        thumb_path = f'uploads/{thumb_name}'

    return ClothesImage(
        clothes_id=clothes_id, image_path=f'uploads/{unique_name}', thumb_path=thumb_path,
    )


# サムネイル切り出しで「背景と違う色」と判定する明るさの差(0-255)。小さいほど薄い影まで拾う。
THUMB_EDGE_THRESHOLD = 26
THUMB_MARGIN_RATIO = 0.02  # 切り出し後に残すわずかな余白(短辺に対する割合)
THUMB_MAX_SIZE = 480  # サムネイルの一辺の最大px数(コーデ一覧では小さくしか使わないので縮めて保存する)


def build_flat_thumbnail(source_path_abs, out_path_abs):
    """アイテムの周りの余白を切り落としたサムネイルPNGを保存する(できなければFalse)。

    コーデ一覧ではアイテムを縦に隙間なく積むので、写真に写り込んだ床や壁の余白が残っていると
    アイテム同士が離れて見えてしまう。透過画像なら不透明な部分、透過していない写真なら
    「外周の色と違う部分」をアイテムとみなし、その外接矩形で切り出す。
    """
    try:
        image = Image.open(source_path_abs)
        background = None
        if image.mode in ('RGBA', 'LA'):
            image = image.convert('RGBA')
            box = image.getchannel('A').getbbox()  # 完全に透明な縁を落とす
            scale = 1.0
        else:
            image = image.convert('RGB')
            # 判定は縮小画像で行う(元画像のままだと大きな写真で時間がかかるため)
            small = image.copy()
            small.thumbnail((CUTOUT_ANALYSIS_SIZE, CUTOUT_ANALYSIS_SIZE), Image.LANCZOS)
            width, height = small.size
            pixels = small.load()
            border = (
                [pixels[x, 0] for x in range(width)] + [pixels[x, height - 1] for x in range(width)]
                + [pixels[0, y] for y in range(height)] + [pixels[width - 1, y] for y in range(height)]
            )
            background = tuple(round(sum(p[i] for p in border) / len(border)) for i in range(3))
            difference = ImageChops.difference(small, Image.new('RGB', small.size, background))
            mask = difference.convert('L').point(lambda v: 255 if v > THUMB_EDGE_THRESHOLD else 0)
            box = mask.getbbox()
            scale = image.width / width

        if box is None:
            return False
        left, top, right, bottom = (round(v * scale) for v in box)
        margin = round(min(image.width, image.height) * THUMB_MARGIN_RATIO)
        left, top = max(left - margin, 0), max(top - margin, 0)
        right, bottom = min(right + margin, image.width), min(bottom + margin, image.height)
        # 判定がうまくいかず画像のほとんどが消える場合は、切り出さず元のまま使う
        if right - left < image.width * 0.1 or bottom - top < image.height * 0.1:
            left, top, right, bottom = 0, 0, image.width, image.height

        # 余白は足さずアイテムぴったりで切り出す。コーデ一覧では上下に隙間なく積みたいので、
        # 画像に縦の余白があるとそのままアイテム同士の隙間になってしまう。
        # 横幅の差(トップスと帽子など)は、表示側で枠の背景色が見えることで埋まる。
        thumbnail = image.crop((left, top, right, bottom))
        thumbnail.thumbnail((THUMB_MAX_SIZE, THUMB_MAX_SIZE), Image.LANCZOS)
        thumbnail.save(out_path_abs, 'PNG')
        return True
    except Exception:
        return False


# 背景除去の判定に使う色距離の閾値(RGB空間でのユークリッド距離の2乗)。
# 値を大きくすると背景と誤認しやすくなり、小さくすると背景が残りやすくなる。無地の背景を想定した経験値。
CUTOUT_COLOR_THRESHOLD_SQ = 42 ** 2
CUTOUT_ANALYSIS_SIZE = 240  # 判定用に縮小する一辺の最大px数(処理速度とのバランス)


def generate_cutout(image_bytes, out_path_abs):
    """写真の外周と同じ色が連結している領域(=背景)を透過にしたPNGを保存する。

    現在この関数は呼び出していない。写真によって綺麗に抜ける・抜けないが分かれ、
    コーデ一覧に切り抜き済みと切り抜き無しが混在して見た目がそろわなかったため、
    背景ごと表示する方針に変えた。再び切り抜きたくなったときのために残してある。
    外周から塗りつぶし(flood fill)で辿れる範囲だけを背景とみなすため、
    アイテム内部に背景と似た色があっても(白いスニーカーなど)誤って消えることはない。
    柄物など背景が単色でない写真では綺麗に抜けないことがあり、その場合はFalseを返す。
    """
    try:
        original = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        analysis = original.copy()
        analysis.thumbnail((CUTOUT_ANALYSIS_SIZE, CUTOUT_ANALYSIS_SIZE), Image.LANCZOS)
        width, height = analysis.size
        pixels = analysis.load()

        border_pixels = (
            [pixels[x, 0] for x in range(width)] + [pixels[x, height - 1] for x in range(width)]
            + [pixels[0, y] for y in range(height)] + [pixels[width - 1, y] for y in range(height)]
        )
        bg_r = sum(p[0] for p in border_pixels) / len(border_pixels)
        bg_g = sum(p[1] for p in border_pixels) / len(border_pixels)
        bg_b = sum(p[2] for p in border_pixels) / len(border_pixels)

        def is_background(x, y):
            r, g, b = pixels[x, y]
            return (r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2 <= CUTOUT_COLOR_THRESHOLD_SQ

        # is_background=1 かつ外周から連結している画素だけを1にする(=透過対象)
        is_removed = bytearray(width * height)
        queue = deque()
        for x in range(width):
            for y in (0, height - 1):
                idx = y * width + x
                if not is_removed[idx] and is_background(x, y):
                    is_removed[idx] = 1
                    queue.append((x, y))
        for y in range(height):
            for x in (0, width - 1):
                idx = y * width + x
                if not is_removed[idx] and is_background(x, y):
                    is_removed[idx] = 1
                    queue.append((x, y))

        while queue:
            x, y = queue.popleft()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    idx = ny * width + nx
                    if not is_removed[idx] and is_background(nx, ny):
                        is_removed[idx] = 1
                        queue.append((nx, ny))

        removed_ratio = sum(is_removed) / len(is_removed)
        # 背景がほぼ全部/ほぼ0だと判定失敗(単色写真・複雑な背景など)とみなし、元画像にフォールバックさせる
        if removed_ratio > 0.95 or removed_ratio < 0.03:
            return False

        # 背景と服の色が近い写真(カーペット等)だと、消し残しが飛び石状に散らばり
        # まだら模様になることがある。「残った画素のうち最大の一塊が占める割合」が低い=
        # アイテムが一枚の塊として残っていない=まだら失敗とみなし、フォールバックさせる。
        kept_total = len(is_removed) - sum(is_removed)
        visited = bytearray(len(is_removed))
        largest_component = 0
        for sy in range(height):
            for sx in range(width):
                start_idx = sy * width + sx
                if is_removed[start_idx] or visited[start_idx]:
                    continue
                size = 0
                component_queue = deque([(sx, sy)])
                visited[start_idx] = 1
                while component_queue:
                    x, y = component_queue.popleft()
                    size += 1
                    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                        if 0 <= nx < width and 0 <= ny < height:
                            idx = ny * width + nx
                            if not is_removed[idx] and not visited[idx]:
                                visited[idx] = 1
                                component_queue.append((nx, ny))
                largest_component = max(largest_component, size)
        if kept_total == 0 or largest_component / kept_total < 0.85:
            return False

        keep_alpha = bytes(0 if v else 255 for v in is_removed)
        mask = Image.frombytes('L', (width, height), keep_alpha)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=1.5))
        mask = mask.resize(original.size, Image.BILINEAR)

        cutout = original.convert('RGBA')
        cutout.putalpha(mask)
        cutout.save(out_path_abs, 'PNG')
        return True
    except Exception:
        return False


def detect_dominant_color(image_bytes):
    """画像の中央付近から支配的な色を抽出し、COLOR_PALETTEの中で一番近い色名を返す(無料の色自動判定)。"""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    except Exception:
        return None

    # 写真の外周は背景(床・壁など)が写り込みやすいため、中央寄りだけをサンプリングする
    width, height = img.size
    left, top = int(width * 0.25), int(height * 0.25)
    right, bottom = int(width * 0.75), int(height * 0.75)
    cropped = img.crop((left, top, right, bottom))
    cropped.thumbnail((60, 60))

    quantized = cropped.quantize(colors=5, method=Image.MEDIANCUT)
    color_counts = quantized.getcolors()
    if not color_counts:
        return None
    palette = quantized.getpalette()
    color_counts.sort(reverse=True)
    _, index = color_counts[0]
    r, g, b = palette[index * 3:index * 3 + 3]

    best_name, best_distance = None, None
    for name, (cr, cg, cb) in COLOR_PALETTE.items():
        distance = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if best_distance is None or distance < best_distance:
            best_distance, best_name = distance, name
    return best_name


def resolve_city_coords(city):
    """「福岡」のような漢字の都市名を、緯度経度(CITY_COORDINATES参照)に変換する。"""
    city = (city or '').strip()
    if city in CITY_COORDINATES:
        return CITY_COORDINATES[city]
    for suffix in JAPAN_ADMIN_SUFFIXES:
        if city.endswith(suffix) and city[:-1] in CITY_COORDINATES:
            return CITY_COORDINATES[city[:-1]]
    return None


# WMO天気コード(Open-Meteoが返すweather_code)を日本語の概況文言に変換する対応表
WMO_WEATHER_JA = {
    0: '快晴', 1: '晴れ', 2: '晴れ時々曇り', 3: '曇り',
    45: '霧', 48: '霧',
    51: '霧雨', 53: '霧雨', 55: '霧雨', 56: '着氷性の霧雨', 57: '着氷性の霧雨',
    61: '雨', 63: '雨', 65: '強い雨', 66: '着氷性の雨', 67: '着氷性の雨',
    71: '雪', 73: '雪', 75: '強い雪', 77: '雪',
    80: 'にわか雨', 81: 'にわか雨', 82: '激しいにわか雨',
    85: 'にわか雪', 86: 'にわか雪',
    95: '雷雨', 96: '雷雨', 99: '雷雨',
}

# 同じ地点への問い合わせを毎回リクエストし直さないよう、しばらく結果を使い回す
_weather_cache = {}
WEATHER_CACHE_SECONDS = 600


def fetch_weather(city):
    """都市名から今日の気温・天気概況を取得する(対応していない地名ならエラーメッセージを返す)。"""
    coords = resolve_city_coords(city)
    if coords is None:
        return None, '対応していない地域です'
    return fetch_weather_at(*coords)


def fetch_weather_at(lat, lon):
    """Open-Meteo(気象庁の予報モデルなどを含む)から今日の気温・天気概況を緯度経度で取得する。
    失敗時は例外を投げずNone/エラーメッセージを返す。
    PythonAnywhere無料プランの外部通信ホワイトリストに載っているエンドポイントのみを使用している。
    """
    cache_key = (lat, lon)
    now = time.time()
    cached = _weather_cache.get(cache_key)
    if cached and now - cached['fetched_at'] < WEATHER_CACHE_SECONDS:
        return cached['temp'], cached['description']

    today = date.today().isoformat()
    url = (
        'https://historical-forecast-api.open-meteo.com/v1/forecast'
        f'?latitude={lat}&longitude={lon}&hourly=temperature_2m,weather_code'
        f'&timezone=Asia%2FTokyo&start_date={today}&end_date={today}'
    )
    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        hourly = response.json()['hourly']
        current_hour = datetime.now().strftime('%Y-%m-%dT%H:00')
        idx = hourly['time'].index(current_hour) if current_hour in hourly['time'] else len(hourly['time']) - 1
        temp = hourly['temperature_2m'][idx]
        if temp is None:
            return None, '天気情報を取得できませんでした'
        description = WMO_WEATHER_JA.get(hourly['weather_code'][idx], '不明')
        temp = round(temp, 1)
        _weather_cache[cache_key] = {'temp': temp, 'description': description, 'fetched_at': now}
        return temp, description
    except (requests.RequestException, KeyError, ValueError, IndexError):
        return None, '天気情報を取得できませんでした'


def resolve_prefecture_key(city):
    """登録都市が都道府県名なら、PREFECTURE_DATA/PREFECTURE_SHAPESのキーを返す。

    市区町村名など都道府県名以外が登録されている場合はNoneを返し、
    呼び出し側は県内マップなしの単一都市表示にフォールバックする。
    """
    city = (city or '').strip()
    if city in PREFECTURE_DATA:
        return city
    for suffix in JAPAN_ADMIN_SUFFIXES:
        if city.endswith(suffix) and city[:-1] in PREFECTURE_DATA:
            return city[:-1]
    return None


def spread_out_cards(spots, aspect, device='pc'):
    """気温カードが重なっている分だけ、少しずつ押し広げて読めるようにする。

    緯度経度どおりの位置を出発点にして、重なっているペアだけを「ずれが小さくて済むほうの軸」に
    動かすので、県内での位置関係(どちらが北か・東か)は保たれる。
    どれくらい離せば重ならないかは画面の大きさで変わるので、端末ごとの目安値を使う。
    """
    size = MAP_SIZES.get(device, MAP_SIZES['pc'])
    width = min(size['max_w'], size['max_h'] * aspect)  # 実際に表示される地図の大きさ(px)
    min_dx = size['card_w'] / width * 100
    min_dy = size['card_h'] / (width / aspect) * 100
    for _ in range(200):
        overlapped = False
        for a, b in combinations(spots, 2):
            dx, dy = b['left'] - a['left'], b['top'] - a['top']
            gap_x, gap_y = min_dx - abs(dx), min_dy - abs(dy)
            if gap_x <= 0 or gap_y <= 0:  # どちらかの軸で離れていれば重なっていない
                continue
            overlapped = True
            if gap_x / min_dx <= gap_y / min_dy:
                shift = (gap_x / 2 + 0.2) * (1 if dx >= 0 else -1)
                a['left'] -= shift
                b['left'] += shift
            else:
                shift = (gap_y / 2 + 0.2) * (1 if dy >= 0 else -1)
                a['top'] -= shift
                b['top'] += shift
        # 枠の外へ出ないよう毎回押し戻す。端に貼り付いたカードは動けなくなるが、
        # そのぶん相手側が押し出され続けるので、狭い県(沖縄など)でも最後には離れる
        for spot in spots:
            spot['left'] = min(max(spot['left'], 0), 100)
            spot['top'] = min(max(spot['top'], 0), 100)
        if not overlapped:
            break
    for spot in spots:
        spot['left'] = round(spot['left'], 1)
        spot['top'] = round(spot['top'], 1)


def parse_price(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def suitable_seasons_for_temp(temp):
    """今日の気温から「今日にぴったり」バッジを出す季節を判定する。オールシーズンは常に対象。"""
    if temp is None:
        return set()
    if temp >= 25:
        return {'夏', 'オールシーズン'}
    if temp >= 15:
        return {'春', '秋', 'オールシーズン'}
    return {'冬', 'オールシーズン'}


def seasons_to_string(season_list):
    """複数選択された季節のリストを、DBに保存する1つの文字列("春,秋"のようなカンマ区切り)に変換する。"""
    return ','.join(s for s in season_list if s)


def seasons_from_string(season_str):
    """DBに保存されているカンマ区切りの季節文字列を、季節名のリストに戻す。"""
    return [s for s in (season_str or '').split(',') if s]


def sorted_by_category(items):
    """コーディネートのアイテムを、CATEGORY_ORDERに沿って頭側→足元の順に並べ替える。"""
    def sort_key(item):
        try:
            return CATEGORY_ORDER.index(item.category)
        except ValueError:
            return len(CATEGORY_ORDER)
    return sorted(items, key=sort_key)


def get_anthropic_client():
    """ANTHROPIC_API_KEYが未設定ならNoneを返す(呼び出し側はNoneならAI機能を無効表示にする)。"""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


# 「そのIDのデータは存在するが、他人の持ち物だった」場合も404にする。
# (403 Forbiddenにすると「他人のデータがそこに存在する」ことがバレてしまうため、あえて404で統一)
def owned_clothes_or_404(id):
    clothes = Clothes.query.get_or_404(id)
    if clothes.user_id != current_user.id:
        abort(404)
    return clothes


def owned_coordinate_or_404(id):
    coordinate = Coordinate.query.get_or_404(id)
    if coordinate.user_id != current_user.id:
        abort(404)
    return coordinate


# ヘルパーの定数・関数がすべて出そろってから実行する
# (定義より前に呼ぶとNameErrorになり、例外を握りつぶす作りのため気付かないまま失敗する)
with app.app_context():
    backfill_thumbnails()  # サムネイル導入前に登録済みの画像を追いつかせる(一度きり)


# ---- 6-1. ルート: 認証(登録・ログイン・ログアウト) ----

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        error = None

        if not username or not password:
            error = 'ユーザー名とパスワードを入力してください。'
        elif User.query.filter_by(username=username).first():
            error = 'そのユーザー名はすでに使われています。'

        if error:
            return render_template('register.html', error=error)

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        if User.query.count() == 1:
            # 最初のユーザー登録時、これまで持ち主のいなかった既存データを引き継ぐ
            Clothes.query.filter_by(user_id=None).update({'user_id': user.id})
            Coordinate.query.filter_by(user_id=None).update({'user_id': user.id})

        db.session.commit()
        login_user(user)
        return redirect(url_for('index'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(request.args.get('next') or url_for('index'))
        return render_template('login.html', error='ユーザー名またはパスワードが違います。')

    return render_template('login.html')


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ---- 6-2. ルート: クローゼット(服の一覧・登録・編集・削除・お気に入り・着用) ----

@app.route('/')
@login_required
def index():
    city = current_user.city or DEFAULT_CITY
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '')
    color = request.args.get('color', '').strip()
    season = request.args.get('season', '')

    query = Clothes.query.filter_by(user_id=current_user.id)
    if q:
        query = query.filter(Clothes.name.ilike(f'%{q}%'))
    if category:
        query = query.filter_by(category=category)
    if color:
        query = query.filter(Clothes.color.ilike(f'%{color}%'))
    if season:
        # season列は"春,秋"のようなカンマ区切りで複数季節を持ちうるため、部分一致で絞り込む
        query = query.filter(Clothes.season.ilike(f'%{season}%'))
    clothes = query.order_by(Clothes.id.desc()).all()

    temp, description = fetch_weather(city)
    suitable_seasons = suitable_seasons_for_temp(temp)
    recommended_ids = {
        c.id for c in clothes if set(seasons_from_string(c.season)) & suitable_seasons
    }
    return render_template(
        f'{get_template_folder()}/index.html',
        clothes=clothes,
        temp=temp,
        description=description,
        city=city,
        categories=CATEGORIES,
        seasons=SEASONS,
        today=date.today(),
        recommended_ids=recommended_ids,
        filters={'q': q, 'category': category, 'color': color, 'season': season},
    )


@app.route('/rarely_worn')
@login_required
def rarely_worn():
    clothes = (
        Clothes.query.filter_by(user_id=current_user.id)
        .order_by(Clothes.last_worn_at.asc())
        .all()
    )
    return render_template('rarely_worn.html', clothes=clothes, today=date.today())


@app.route('/city', methods=['POST'])
@login_required
def set_city():
    city = request.form.get('city', '').strip()
    if city:
        current_user.city = city
        db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        color = request.form['color']
        season = seasons_to_string(request.form.getlist('season'))
        price = parse_price(request.form.get('price'))

        new_clothes = Clothes(
            name=name, category=category, color=color, season=season, price=price,
            user_id=current_user.id,
        )
        db.session.add(new_clothes)
        db.session.flush()

        images = request.files.getlist('images')
        for image in images:
            new_image = save_clothes_image(image, new_clothes.id)
            if new_image:
                db.session.add(new_image)

        db.session.commit()
        return redirect(url_for('index'))

    # 端末に応じて pc/add.html か mobile/add.html を出し分ける(POST側の処理は共通)
    return render_template(
        f'{get_template_folder()}/add.html',
        categories=CATEGORIES,
        seasons=SEASONS,
        ai_enabled=bool(os.getenv('ANTHROPIC_API_KEY')),
    )


@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    clothes = owned_clothes_or_404(id)
    if request.method == 'POST':
        clothes.name = request.form['name']
        clothes.category = request.form['category']
        clothes.color = request.form['color']
        clothes.season = seasons_to_string(request.form.getlist('season'))
        clothes.price = parse_price(request.form.get('price'))

        images = request.files.getlist('images')
        for image in images:
            new_image = save_clothes_image(image, clothes.id)
            if new_image:
                db.session.add(new_image)

        db.session.commit()
        return redirect(url_for('index'))

    # 端末に応じて pc/edit.html か mobile/edit.html を出し分ける(POST側の処理は共通)
    return render_template(
        f'{get_template_folder()}/edit.html',
        clothes=clothes,
        categories=CATEGORIES,
        seasons=SEASONS,
        ai_enabled=bool(os.getenv('ANTHROPIC_API_KEY')),
    )


@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    clothes = owned_clothes_or_404(id)
    db.session.delete(clothes)
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/delete_image/<int:id>', methods=['POST'])
@login_required
def delete_image(id):
    image = ClothesImage.query.get_or_404(id)
    if image.clothes.user_id != current_user.id:
        abort(404)
    clothes_id = image.clothes_id
    db.session.delete(image)
    db.session.commit()
    return redirect(url_for('edit', id=clothes_id))


@app.route('/favorite/<int:id>', methods=['POST'])
@login_required
def toggle_favorite(id):
    clothes = owned_clothes_or_404(id)
    clothes.is_favorite = not clothes.is_favorite
    db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/wear/<int:id>', methods=['POST'])
@login_required
def wear(id):
    clothes = owned_clothes_or_404(id)
    # last_worn_atが今日の日付ならすでに記録済み=1日1回の制限として何もしない
    if clothes.last_worn_at != date.today():
        clothes.wear_count = (clothes.wear_count or 0) + 1
        clothes.last_worn_at = date.today()
        db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/unwear/<int:id>', methods=['POST'])
@login_required
def unwear(id):
    clothes = owned_clothes_or_404(id)
    # 今日つけた記録だけ取り消せる(昨日以前の記録はここでは戻せない)
    if clothes.last_worn_at == date.today() and clothes.wear_count > 0:
        clothes.wear_count -= 1
        clothes.last_worn_at = None
        db.session.commit()
    return redirect(request.referrer or url_for('index'))


# ---- 6-3. ルート: コーディネート(組み合わせの保存・一覧・詳細・削除) ----

@app.route('/coordinates')
@login_required
def coordinates():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '')
    color = request.args.get('color', '').strip()
    season = request.args.get('season', '')

    query = Coordinate.query.filter_by(user_id=current_user.id)
    if q:
        # コーデ名そのもの、または含まれるアイテムの名前のどちらかに一致すればヒットさせる
        query = query.filter(
            db.or_(
                Coordinate.name.ilike(f'%{q}%'),
                Coordinate.items.any(Clothes.name.ilike(f'%{q}%')),
            )
        )
    # カテゴリ・色・季節はコーデ自体でなくアイテム側が持つ情報なので、
    # 含まれるアイテムのどれかが一致すればそのコーデをヒットさせる(クローゼットの検索と同じ考え方)
    if category:
        query = query.filter(Coordinate.items.any(Clothes.category == category))
    if color:
        query = query.filter(Coordinate.items.any(Clothes.color.ilike(f'%{color}%')))
    if season:
        query = query.filter(Coordinate.items.any(Clothes.season.ilike(f'%{season}%')))
    coords = query.order_by(Coordinate.id.desc()).all()

    # サムネイルはカテゴリごとに1枠(アウターはトップスの左、バッグは右…)に配置するため、
    # カテゴリ名 → アイテムの辞書にしておく(同じカテゴリが複数あれば先頭の1点だけ使う)
    coord_thumbs = {}
    for c in coords:
        by_category = {}
        for item in sorted_by_category(c.items):
            by_category.setdefault(item.category, item)
        coord_thumbs[c.id] = by_category

    return render_template(
        f'{get_template_folder()}/coordinates.html', coordinates=coords, coord_thumbs=coord_thumbs,
        categories=CATEGORIES, seasons=SEASONS,
        filters={'q': q, 'category': category, 'color': color, 'season': season},
    )


@app.route('/coordinates/new', methods=['GET', 'POST'])
@login_required
def new_coordinate():
    if request.method == 'POST':
        name = request.form['name']
        item_ids = request.form.getlist('clothes_ids')
        coordinate = Coordinate(name=name, user_id=current_user.id)
        if item_ids:
            coordinate.items = Clothes.query.filter(
                Clothes.id.in_(item_ids), Clothes.user_id == current_user.id,
            ).all()
        db.session.add(coordinate)
        db.session.commit()
        return redirect(url_for('coordinates'))

    clothes = (
        Clothes.query.filter_by(user_id=current_user.id)
        .order_by(Clothes.category, Clothes.name)
        .all()
    )
    return render_template(
        f'{get_template_folder()}/coordinate_new.html', clothes=clothes, selected_ids=set())


@app.route('/coordinates/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_coordinate(id):
    coordinate = owned_coordinate_or_404(id)
    if request.method == 'POST':
        coordinate.name = request.form['name']
        item_ids = request.form.getlist('clothes_ids')
        coordinate.items = (
            Clothes.query.filter(
                Clothes.id.in_(item_ids), Clothes.user_id == current_user.id,
            ).all()
            if item_ids else []
        )
        db.session.commit()
        return redirect(url_for('coordinates'))

    clothes = (
        Clothes.query.filter_by(user_id=current_user.id)
        .order_by(Clothes.category, Clothes.name)
        .all()
    )
    selected_ids = {item.id for item in coordinate.items}
    return render_template(
        'coordinate_edit.html', coordinate=coordinate, clothes=clothes, selected_ids=selected_ids,
    )


@app.route('/coordinates/<int:id>/delete', methods=['POST'])
@login_required
def delete_coordinate(id):
    coordinate = owned_coordinate_or_404(id)
    db.session.delete(coordinate)
    db.session.commit()
    return redirect(url_for('coordinates'))


# ---- 6-4. ルート: AI・画像解析 ----
# detect_colorは常時無料で使える。analyze_image / suggestはANTHROPIC_API_KEY設定時のみ有効。

@app.route('/detect_color', methods=['POST'])
@login_required
def detect_color():
    image = request.files.get('image')
    if not image or image.filename == '':
        return jsonify({'error': '画像が選択されていません'}), 400

    filename = secure_filename(image.filename)
    if not filename or not allowed_file(filename):
        return jsonify({'error': '対応していないファイル形式です'}), 400

    color = detect_dominant_color(image.read())
    if not color:
        return jsonify({'error': '色を判定できませんでした'}), 500
    return jsonify({'color': color})


@app.route('/analyze_image', methods=['POST'])
@login_required
def analyze_image():
    client = get_anthropic_client()
    if client is None:
        return jsonify({'error': 'AI機能を使うにはANTHROPIC_API_KEYの設定が必要です'}), 400

    image = request.files.get('image')
    if not image or image.filename == '':
        return jsonify({'error': '画像が選択されていません'}), 400

    filename = secure_filename(image.filename)
    if not filename or not allowed_file(filename):
        return jsonify({'error': '対応していないファイル形式です'}), 400

    ext = filename.rsplit('.', 1)[1].lower()
    media_type = f'image/{"jpeg" if ext == "jpg" else ext}'
    encoded = base64.b64encode(image.read()).decode('utf-8')

    prompt = (
        f'この画像に写っている服を分類してください。'
        f'カテゴリは次の中から1つ選んでください: {", ".join(CATEGORIES)}。'
        f'季節は次の中から1つ選んでください: {", ".join(SEASONS)}。'
        f'色は日本語で簡潔に1つ答えてください。'
        f'出力は必ず次のJSON形式のみで、他の文章は含めないでください: '
        f'{{"category": "...", "color": "...", "season": "..."}}'
    )

    try:
        message = client.messages.create(
            model='claude-sonnet-5',
            max_tokens=200,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}},
                    {'type': 'text', 'text': prompt},
                ],
            }],
        )
        result = json.loads(message.content[0].text.strip())
        return jsonify(result)
    except Exception:
        return jsonify({'error': 'AI解析に失敗しました。時間をおいて再度お試しください。'}), 500


@app.route('/suggest')
@login_required
def suggest():
    city = current_user.city or DEFAULT_CITY
    temp, description = fetch_weather(city)
    clothes = Clothes.query.filter_by(user_id=current_user.id).all()

    if not clothes:
        return render_template(
            'suggest.html', suggestion=None, temp=temp, description=description, city=city,
            error='クローゼットに服が登録されていません。まずは服を登録してください。',
        )

    client = get_anthropic_client()
    if client is None:
        return render_template(
            'suggest.html', suggestion=None, temp=temp, description=description, city=city,
            error='AI機能を使うにはANTHROPIC_API_KEYの設定が必要です。',
        )

    wardrobe_lines = [
        f'- {c.name}(カテゴリ: {c.category}, 色: {c.color}, '
        f'季節: {"・".join(seasons_from_string(c.season)) or "未設定"})'
        for c in clothes
    ]
    prompt = (
        f'今日の{city}の天気は「{description}」、気温は{temp}℃です。\n'
        f'以下のクローゼットの中から、今日着るのにおすすめのコーディネートを1つ提案してください。'
        f'色の組み合わせや季節感も考慮してください。\n\n'
        f'【クローゼット】\n' + '\n'.join(wardrobe_lines) + '\n\n'
        f'提案理由も含めて日本語で簡潔に(200文字程度)答えてください。'
    )

    try:
        message = client.messages.create(
            model='claude-sonnet-5',
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}],
        )
        suggestion = message.content[0].text.strip()
        error = None
    except Exception:
        suggestion = None
        error = 'AIコーデ提案の取得に失敗しました。時間をおいて再度お試しください。'

    return render_template(
        'suggest.html', suggestion=suggestion, temp=temp, description=description, city=city, error=error,
    )


# ---- 6-5. ルート: 天気 ----

@app.route('/weather')
@login_required
def weather():
    city = current_user.city or DEFAULT_CITY
    temp, description = fetch_weather(city)

    device = get_template_folder()
    prefecture_key = resolve_prefecture_key(city)
    city_weather = []
    prefecture_shape = None
    if prefecture_key:
        prefecture_shape = PREFECTURE_SHAPES[prefecture_key]
        # 都市カードは、県の輪郭とまったく同じ緯度経度の範囲を基準に%位置へ変換する。
        # こうすることで「輪郭のどのあたりの都市か」が実際の地理と一致する。
        lat_min, lat_max, lon_min, lon_max = prefecture_shape['bounds']
        lat_span = max(lat_max - lat_min, 0.05)
        lon_span = max(lon_max - lon_min, 0.05)
        for item in PREFECTURE_DATA[prefecture_key]['cities']:
            city_temp, city_description = fetch_weather_at(item['lat'], item['lon'])
            city_weather.append({
                'name': item['name'],
                'left': (item['lon'] - lon_min) / lon_span * 100,
                'top': (lat_max - item['lat']) / lat_span * 100,  # 緯度が高い(北)ほど上
                'temp': city_temp, 'description': city_description,
            })
        spread_out_cards(city_weather, float(prefecture_shape['aspect']), device)

    return render_template(
        'weather.html', temp=temp, description=description, city=city,
        city_weather=city_weather, prefecture_shape=prefecture_shape,
    )


# ---- エントリーポイント ----
# `python app.py`で直接実行したときだけここが動く(gunicorn/WSGI経由の本番実行では通らない)。
if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode)
