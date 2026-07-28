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
import os
import re
import time
import uuid
from collections import deque
from datetime import date

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from PIL import Image, ImageFilter
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

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

db = SQLAlchemy(app)  # モデル定義・DB操作の窓口

login_manager = LoginManager(app)
login_manager.login_view = 'login'  # 未ログインで@login_requiredのページに来たら/loginへ飛ばす
login_manager.login_message = 'ログインしてください'

# ---- 2. 定数 ----
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
CATEGORIES = ['トップス', 'ボトムス', 'アウター', 'シューズ', 'アクセサリー']
SEASONS = ['春', '夏', '秋', '冬', 'オールシーズン']
DEFAULT_CITY = '東京'

# コーディネート詳細画面で、実際に着る順番(頭側→足元)に近い並びで表示するための優先順位
CATEGORY_ORDER = ['アクセサリー', 'アウター', 'トップス', 'ボトムス', 'シューズ']

# 気象庁アメダスの観測地点ID(https://www.jma.go.jp/bosai/amedas/const/amedastable.json より)。
# 主要な都道府県名・都市名の漢字入力を、対応する観測地点IDに変換してから問い合わせる。
# 地点が存在しない市区町村は、隣接する観測地点で代用している(コメントに実際の地点名を記載)。
AMEDAS_STATION_MAP = {
    '北海道': '14163', '札幌': '14163',
    '青森': '31312', '岩手': '33431', '盛岡': '33431', '宮城': '34392', '仙台': '34392',
    '秋田': '32402', '山形': '35426', '福島': '36127',
    '茨城': '40201', '水戸': '40201', '栃木': '41277', '宇都宮': '41277', '群馬': '42251', '前橋': '42251',
    '埼玉': '43241', '千葉': '45212', '東京': '44132',
    '神奈川': '46106', '横浜': '46106', '川崎': '46106',  # 川崎は横浜地点で代用
    '新潟': '54232', '富山': '55102', '石川': '56227', '金沢': '56227', '福井': '57066',
    '山梨': '49142', '甲府': '49142', '長野': '48156', '岐阜': '52586', '静岡': '50331',
    '愛知': '51106', '名古屋': '51106', '三重': '53133', '津': '53133',
    '滋賀': '60216', '京都': '61286', '大阪': '62078', '兵庫': '63518', '神戸': '63518',
    '奈良': '64036', '和歌山': '65042', '鳥取': '69122', '島根': '68132', '松江': '68132',
    '岡山': '66408', '広島': '67437', '山口': '81286',
    '徳島': '71106', '香川': '72086', '高松': '72086', '愛媛': '73166', '松山': '73166', '高知': '74182',
    '福岡': '82182', '佐賀': '85142', '長崎': '84496', '熊本': '86141',
    '大分': '83216', '宮崎': '87376', '鹿児島': '88317',
    '沖縄': '91197', '那覇': '91197',
    # 天気ページのコンパス配置で使う、都道府県庁所在地以外の市区町村
    '北九州市': '82056',  # 八幡地点で代用
    '久留米市': '82306', '大牟田市': '82361', '飯塚市': '82136',
    '福山市': '67401', '呉市': '67511', '三次市': '67106',
    '高槻市': '62037',  # 茨木地点で代用
    '東大阪市': '62078',  # 大阪地点で代用
    '堺市': '62091',
    '岸和田市': '62091',  # 堺地点で代用
    '青梅市': '44056',
    '立川市': '44116',  # 府中地点で代用
    '八王子市': '44112',
    '町田市': '44112',  # 八王子地点で代用
}
JAPAN_ADMIN_SUFFIXES = ('都', '道', '府', '県', '市')

# 天気ページでニュース番組風に「県内の主要都市の気温」を並べる県だけ、
# 実在の地理的な位置関係に合わせてコンパス方位(nw/n/ne/w/c/e/sw/s/se)に都市を割り当てる。
# 対応していない都道府県では、今まで通り単一都市の表示にフォールバックする。
PREFECTURE_CITY_LAYOUT = {
    '福岡': [
        {'name': '北九州市', 'pos': 'n'},
        {'name': '福岡市', 'pos': 'w'},
        {'name': '飯塚市', 'pos': 'c'},
        {'name': '久留米市', 'pos': 's'},
        {'name': '大牟田市', 'pos': 'sw'},
    ],
    '広島': [
        {'name': '三次市', 'pos': 'n'},
        {'name': '広島市', 'pos': 'w'},
        {'name': '福山市', 'pos': 'e'},
        {'name': '呉市', 'pos': 's'},
    ],
    '大阪': [
        {'name': '高槻市', 'pos': 'n'},
        {'name': '大阪市', 'pos': 'c'},
        {'name': '東大阪市', 'pos': 'e'},
        {'name': '堺市', 'pos': 's'},
        {'name': '岸和田市', 'pos': 'sw'},
    ],
    '東京': [
        {'name': '青梅市', 'pos': 'nw'},
        {'name': '立川市', 'pos': 'w'},
        {'name': '東京(23区)', 'pos': 'e', 'lookup': '東京'},
        {'name': '八王子市', 'pos': 'sw'},
        {'name': '町田市', 'pos': 's'},
    ],
}

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
    # 背景を透過した切り抜き画像。無地の背景でないなど生成に失敗した場合はNoneのまま
    cutout_path = db.Column(db.String(200))

    @property
    def thumbnail_path(self):
        """コーデ一覧など背景を揃えて見せたい場面で使う画像パス(切り抜きが無ければ元画像)。"""
        return self.cutout_path or self.image_path


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
        conn.commit()


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

    cutout_path = None
    with open(image_path_abs, 'rb') as f:
        image_bytes = f.read()
    cutout_name = f'{uuid.uuid4().hex}_cut.png'
    if generate_cutout(image_bytes, os.path.join(app.config['UPLOAD_FOLDER'], cutout_name)):
        cutout_path = f'uploads/{cutout_name}'

    return ClothesImage(
        clothes_id=clothes_id, image_path=f'uploads/{unique_name}', cutout_path=cutout_path,
    )


# 背景除去の判定に使う色距離の閾値(RGB空間でのユークリッド距離の2乗)。
# 値を大きくすると背景と誤認しやすくなり、小さくすると背景が残りやすくなる。無地の背景を想定した経験値。
CUTOUT_COLOR_THRESHOLD_SQ = 42 ** 2
CUTOUT_ANALYSIS_SIZE = 240  # 判定用に縮小する一辺の最大px数(処理速度とのバランス)


def generate_cutout(image_bytes, out_path_abs):
    """写真の外周と同じ色が連結している領域(=背景)を透過にしたPNGを保存する。
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


def resolve_amedas_station(city):
    """「福岡」のような漢字の都市名を、対応する気象庁アメダス観測地点ID(AMEDAS_STATION_MAP参照)に変換する。"""
    city = (city or '').strip()
    if city in AMEDAS_STATION_MAP:
        return AMEDAS_STATION_MAP[city]
    for suffix in JAPAN_ADMIN_SUFFIXES:
        if city.endswith(suffix) and city[:-1] in AMEDAS_STATION_MAP:
            return AMEDAS_STATION_MAP[city[:-1]]
    return None


# 気象庁アメダスの観測データは10分おきの更新のため、リクエストのたびに取得し直さず
# しばらく使い回す(JMAのサーバーへの負荷軽減と応答速度の両方を兼ねる)。
_amedas_cache = {'data': None, 'fetched_at': 0}
AMEDAS_CACHE_SECONDS = 300


def fetch_amedas_snapshot():
    """気象庁アメダスの最新の全地点観測データを取得する(取得失敗時は直前のキャッシュかNoneを返す)。"""
    now = time.time()
    if _amedas_cache['data'] is not None and now - _amedas_cache['fetched_at'] < AMEDAS_CACHE_SECONDS:
        return _amedas_cache['data']
    try:
        latest_res = requests.get('https://www.jma.go.jp/bosai/amedas/data/latest_time.txt', timeout=5)
        latest_res.raise_for_status()
        # "2026-07-28T17:00:00+09:00" -> "20260728170000"
        stamp = re.sub(r'[-:T]', '', latest_res.text.strip().split('+')[0])
        data_res = requests.get(f'https://www.jma.go.jp/bosai/amedas/data/map/{stamp}.json', timeout=10)
        data_res.raise_for_status()
        data = data_res.json()
        _amedas_cache['data'] = data
        _amedas_cache['fetched_at'] = now
        return data
    except (requests.RequestException, ValueError):
        return _amedas_cache['data']


def describe_amedas_weather(obs):
    """アメダスは天気概況テキストを直接持たないため、降水量・日照時間から簡易的に文言を推定する。"""
    precipitation = (obs.get('precipitation1h') or [0])[0] or 0
    sunshine = (obs.get('sun1h') or [None])[0]
    if precipitation >= 1:
        return '雨'
    if precipitation > 0:
        return '小雨'
    if sunshine is None:
        return '曇り'
    if sunshine >= 0.8:
        return '晴れ'
    if sunshine >= 0.3:
        return '晴れ時々曇り'
    return '曇り'


def fetch_weather(city):
    """気象庁アメダスの実況データから気温・天気概況を取得する。失敗時は例外を投げずNone/エラーメッセージを返す。"""
    station_id = resolve_amedas_station(city)
    if station_id is None:
        return None, '対応していない地域です'
    snapshot = fetch_amedas_snapshot()
    if not snapshot:
        return None, '天気情報を取得できませんでした'
    obs = snapshot.get(station_id)
    temp = (obs or {}).get('temp')
    if not obs or not temp or temp[0] is None:
        return None, '天気情報を取得できませんでした'
    return round(temp[0], 1), describe_amedas_weather(obs)


def prefecture_layout_for(city):
    """登録都市が対応済みの都道府県なら、県内主要都市のコンパス配置リストを返す(非対応ならNone)。"""
    city = (city or '').strip()
    if city in PREFECTURE_CITY_LAYOUT:
        return PREFECTURE_CITY_LAYOUT[city]
    for suffix in JAPAN_ADMIN_SUFFIXES:
        if city.endswith(suffix) and city[:-1] in PREFECTURE_CITY_LAYOUT:
            return PREFECTURE_CITY_LAYOUT[city[:-1]]
    return None


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
        'index.html',
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

    return render_template(
        'add.html',
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

    return render_template(
        'edit.html',
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
    coords = (
        Coordinate.query.filter_by(user_id=current_user.id)
        .order_by(Coordinate.id.desc())
        .all()
    )
    coord_thumbs = {c.id: sorted_by_category(c.items)[:4] for c in coords}
    return render_template('coordinates.html', coordinates=coords, coord_thumbs=coord_thumbs)


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
    return render_template('coordinate_new.html', clothes=clothes, selected_ids=set())


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

    layout = prefecture_layout_for(city)
    city_weather = []
    if layout:
        for item in layout:
            city_temp, city_description = fetch_weather(item.get('lookup', item['name']))
            city_weather.append({
                'name': item['name'], 'pos': item['pos'],
                'temp': city_temp, 'description': city_description,
            })

    return render_template(
        'weather.html', temp=temp, description=description, city=city, city_weather=city_weather,
    )


# ---- エントリーポイント ----
# `python app.py`で直接実行したときだけここが動く(gunicorn/WSGI経由の本番実行では通らない)。
if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode)
