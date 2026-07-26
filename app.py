import base64
import json
import os
import uuid
from datetime import date

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fashion.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY is not set. Please define it in the .env file.')

csrf = CSRFProtect(app)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'ログインしてください'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
CATEGORIES = ['トップス', 'ボトムス', 'アウター', 'シューズ', 'アクセサリー']
SEASONS = ['春', '夏', '秋', '冬', 'オールシーズン']
DEFAULT_CITY = 'Fukuoka'

# OpenWeatherMapのq=パラメータは英字都市名しか安定して解決できないため、
# 主要な都道府県名・都市名の漢字入力を英語名に変換してから問い合わせる。
JAPAN_CITY_NAME_MAP = {
    '北海道': 'Sapporo', '札幌': 'Sapporo',
    '青森': 'Aomori', '岩手': 'Morioka', '宮城': 'Sendai', '仙台': 'Sendai',
    '秋田': 'Akita', '山形': 'Yamagata', '福島': 'Fukushima',
    '茨城': 'Mito', '栃木': 'Utsunomiya', '群馬': 'Maebashi',
    '埼玉': 'Saitama', '千葉': 'Chiba', '東京': 'Tokyo',
    '神奈川': 'Yokohama', '横浜': 'Yokohama', '川崎': 'Kawasaki',
    '新潟': 'Niigata', '富山': 'Toyama', '石川': 'Kanazawa', '福井': 'Fukui',
    '山梨': 'Kofu', '長野': 'Nagano', '岐阜': 'Gifu', '静岡': 'Shizuoka',
    '愛知': 'Nagoya', '名古屋': 'Nagoya', '三重': 'Tsu',
    '滋賀': 'Otsu', '京都': 'Kyoto', '大阪': 'Osaka', '兵庫': 'Kobe', '神戸': 'Kobe',
    '奈良': 'Nara', '和歌山': 'Wakayama', '鳥取': 'Tottori', '島根': 'Matsue',
    '岡山': 'Okayama', '広島': 'Hiroshima', '山口': 'Yamaguchi',
    '徳島': 'Tokushima', '香川': 'Takamatsu', '愛媛': 'Matsuyama', '高知': 'Kochi',
    '福岡': 'Fukuoka', '佐賀': 'Saga', '長崎': 'Nagasaki', '熊本': 'Kumamoto',
    '大分': 'Oita', '宮崎': 'Miyazaki', '鹿児島': 'Kagoshima',
    '沖縄': 'Naha', '那覇': 'Naha',
}
JAPAN_ADMIN_SUFFIXES = ('都', '道', '府', '県', '市')

NEUTRAL_COLORS = {'白', '黒', 'グレー', 'グレイ', 'ネイビー', '紺', 'ベージュ'}
COMPATIBLE_COLOR_PAIRS = {
    frozenset({'デニム', '白'}),
    frozenset({'カーキ', '茶'}),
    frozenset({'赤', '黒'}),
    frozenset({'青', '白'}),
    frozenset({'ピンク', 'グレー'}),
}

coordinate_items = db.Table(
    'coordinate_items',
    db.Column('coordinate_id', db.Integer, db.ForeignKey('coordinate.id'), primary_key=True),
    db.Column('clothes_id', db.Integer, db.ForeignKey('clothes.id'), primary_key=True),
)


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(100))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Clothes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
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


class Coordinate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.Date, default=date.today, nullable=False)
    items = db.relationship('Clothes', secondary=coordinate_items, backref='coordinates')


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
        conn.commit()


with app.app_context():
    db.create_all()
    run_migrations()


def allowed_file(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in ALLOWED_EXTENSIONS


def save_clothes_image(image, clothes_id):
    if not image or image.filename == '':
        return None
    filename = secure_filename(image.filename)
    if not filename or not allowed_file(filename):
        return None
    ext = filename.rsplit('.', 1)[1].lower()
    unique_name = f'{uuid.uuid4().hex}.{ext}'
    image.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_name))
    return ClothesImage(clothes_id=clothes_id, image_path=f'uploads/{unique_name}')


def resolve_city_name(city):
    city = city.strip()
    if city in JAPAN_CITY_NAME_MAP:
        return JAPAN_CITY_NAME_MAP[city]
    for suffix in JAPAN_ADMIN_SUFFIXES:
        if city.endswith(suffix) and city[:-1] in JAPAN_CITY_NAME_MAP:
            return JAPAN_CITY_NAME_MAP[city[:-1]]
    return city


def fetch_weather(city):
    api_key = os.getenv('OPENWEATHER_API_KEY')
    resolved_city = resolve_city_name(city)
    url = f'http://api.openweathermap.org/data/2.5/weather?q={resolved_city}&appid={api_key}&units=metric&lang=ja'
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        temp = round(data['main']['temp'], 1)
        description = data['weather'][0]['description']
        return temp, description
    except (requests.RequestException, KeyError, ValueError):
        return None, '天気情報を取得できませんでした'


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
    if temp is None:
        return set()
    if temp >= 25:
        return {'夏', 'オールシーズン'}
    if temp >= 15:
        return {'春', '秋', 'オールシーズン'}
    return {'冬', 'オールシーズン'}


def is_color_compatible(color1, color2):
    if not color1 or not color2:
        return True
    c1, c2 = color1.strip(), color2.strip()
    if c1 == c2:
        return True
    if c1 in NEUTRAL_COLORS or c2 in NEUTRAL_COLORS:
        return True
    return frozenset({c1, c2}) in COMPATIBLE_COLOR_PAIRS


def get_anthropic_client():
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


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
        query = query.filter_by(season=season)
    clothes = query.order_by(Clothes.id.desc()).all()

    temp, description = fetch_weather(city)
    neglected_preview = (
        Clothes.query.filter_by(user_id=current_user.id)
        .order_by(Clothes.last_worn_at.asc())
        .limit(5)
        .all()
    )
    return render_template(
        'index.html',
        clothes=clothes,
        temp=temp,
        description=description,
        city=city,
        categories=CATEGORIES,
        seasons=SEASONS,
        today=date.today(),
        suitable_seasons=suitable_seasons_for_temp(temp),
        neglected_preview=neglected_preview,
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
    return redirect(url_for('index'))


@app.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        color = request.form['color']
        season = request.form['season']
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
        clothes.season = request.form['season']
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
    if clothes.last_worn_at != date.today():
        clothes.wear_count = (clothes.wear_count or 0) + 1
        clothes.last_worn_at = date.today()
        db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/unwear/<int:id>', methods=['POST'])
@login_required
def unwear(id):
    clothes = owned_clothes_or_404(id)
    if clothes.last_worn_at == date.today() and clothes.wear_count > 0:
        clothes.wear_count -= 1
        clothes.last_worn_at = None
        db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/coordinates')
@login_required
def coordinates():
    coords = (
        Coordinate.query.filter_by(user_id=current_user.id)
        .order_by(Coordinate.id.desc())
        .all()
    )
    return render_template('coordinates.html', coordinates=coords)


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
    return render_template('coordinate_new.html', clothes=clothes)


@app.route('/coordinates/<int:id>')
@login_required
def coordinate_detail(id):
    coordinate = owned_coordinate_or_404(id)
    items = coordinate.items
    warnings = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if not is_color_compatible(items[i].color, items[j].color):
                warnings.append((items[i], items[j]))
    return render_template('coordinate_detail.html', coordinate=coordinate, warnings=warnings)


@app.route('/coordinates/<int:id>/delete', methods=['POST'])
@login_required
def delete_coordinate(id):
    coordinate = owned_coordinate_or_404(id)
    db.session.delete(coordinate)
    db.session.commit()
    return redirect(url_for('coordinates'))


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
        f'- {c.name}(カテゴリ: {c.category}, 色: {c.color}, 季節: {c.season})' for c in clothes
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


@app.route('/weather')
@login_required
def weather():
    city = current_user.city or DEFAULT_CITY
    temp, description = fetch_weather(city)
    return render_template('weather.html', temp=temp, description=description, city=city)


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode)
