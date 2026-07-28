"""天気ページの県内マップ用データ(static/data/prefectures.json)を生成する開発用スクリプト。

アプリの実行時には動かない。輪郭や都市の顔ぶれを変えたくなったときだけ、手元で

    python tools/build_prefecture_data.py

を実行してJSONを作り直し、生成物をコミットする(本番では出来上がったJSONを読むだけなので、
PythonAnywhereの外部通信ホワイトリストには影響しない)。

データ元:
  - 県の輪郭: dataofjapan/land の japan.geojson(国土交通省 国土数値情報の行政区域データ由来)
  - 市区町村の位置: code4fukui/localgovjp(全国地方公共団体コードと市区町村役場の緯度経度)
"""
import json
import math
import os
import sys
import urllib.request

GEOJSON_URL = 'https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson'
LOCALGOV_URL = 'https://raw.githubusercontent.com/code4fukui/localgovjp/master/localgovjp.json'
OUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'static', 'data', 'prefectures.json')

OUTLINE_POINTS = 90  # 1県あたりの輪郭の点数(多いほど本物に近いがJSONが重くなる)
CITIES_PER_PREF = 5

# 都道府県庁所在地。マップに必ず含める1都市目として使う。
# 東京都だけは都庁のある新宿区ではなく「東京23区」としてまとめて表示する。
CAPITALS = {
    '北海道': '札幌市', '青森県': '青森市', '岩手県': '盛岡市', '宮城県': '仙台市',
    '秋田県': '秋田市', '山形県': '山形市', '福島県': '福島市', '茨城県': '水戸市',
    '栃木県': '宇都宮市', '群馬県': '前橋市', '埼玉県': 'さいたま市', '千葉県': '千葉市',
    '東京都': '新宿区', '神奈川県': '横浜市', '新潟県': '新潟市', '富山県': '富山市',
    '石川県': '金沢市', '福井県': '福井市', '山梨県': '甲府市', '長野県': '長野市',
    '岐阜県': '岐阜市', '静岡県': '静岡市', '愛知県': '名古屋市', '三重県': '津市',
    '滋賀県': '大津市', '京都府': '京都市', '大阪府': '大阪市', '兵庫県': '神戸市',
    '奈良県': '奈良市', '和歌山県': '和歌山市', '鳥取県': '鳥取市', '島根県': '松江市',
    '岡山県': '岡山市', '広島県': '広島市', '山口県': '山口市', '徳島県': '徳島市',
    '香川県': '高松市', '愛媛県': '松山市', '高知県': '高知市', '福岡県': '福岡市',
    '佐賀県': '佐賀市', '長崎県': '長崎市', '熊本県': '熊本市', '大分県': '大分市',
    '宮崎県': '宮崎市', '鹿児島県': '鹿児島市', '沖縄県': '那覇市',
}
DISPLAY_NAME_OVERRIDES = {('東京都', '新宿区'): '東京23区'}


def fetch_json(url):
    print(f'  取得中: {url}', file=sys.stderr)
    with urllib.request.urlopen(url, timeout=300) as res:
        return json.loads(res.read().decode('utf-8'))


# ---- 輪郭(ポリゴン)まわり ----

def exterior_rings(geom):
    if geom['type'] == 'Polygon':
        return [geom['coordinates'][0]]
    return [poly[0] for poly in geom['coordinates']]


def ring_area(ring):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def perpendicular_distance(pt, start, end):
    (x, y), (x1, y1), (x2, y2) = pt, start, end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def douglas_peucker(points, epsilon):
    """折れ線を、元の形から epsilon 以上ずれない範囲で間引く。"""
    if len(points) < 3:
        return points
    index, farthest = 0, 0.0
    for i in range(1, len(points) - 1):
        d = perpendicular_distance(points[i], points[0], points[-1])
        if d > farthest:
            index, farthest = i, d
    if farthest <= epsilon:
        return [points[0], points[-1]]
    return douglas_peucker(points[:index + 1], epsilon)[:-1] + douglas_peucker(points[index:], epsilon)


def simplify_ring(ring, target_points):
    """目標の点数に収まる中で一番細かい簡略化を、許容誤差の二分探索で探す。"""
    closed = ring + [ring[0]]
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if len(douglas_peucker(closed, mid)) - 1 > target_points:
            low = mid
        else:
            high = mid
    return douglas_peucker(closed, high)[:-1]


def point_in_ring(lon, lat, ring):
    """点が多角形の内側かどうか(レイキャスティング法)。カードを陸地の上にだけ置くために使う。"""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) / (y2 - y1) * (x2 - x1):
            inside = not inside
    return inside


# ---- 都市の選び方 ----

def local_distance(a, b):
    """近距離用の簡易的な距離。経度は緯度に応じて縮めて、実際の見た目の距離に近づける。"""
    lat1, lon1 = a
    lat2, lon2 = b
    scale = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(lat1 - lat2, (lon1 - lon2) * scale)


def pick_cities(candidates, fallback, capital_name):
    """県庁所在地を起点に、「すでに選んだ都市から一番遠い都市」を繰り返し選ぶ。

    人口データを使わずに県内へまんべんなく散らすための選び方で、
    ニュースの天気図のように県の端から端までカバーできるうえ、カード同士も重なりにくい。
    candidatesは「市」だけ。5つに足りない県(鳥取など)でだけ、町村のfallbackから補う。
    """
    by_name = {c['name']: c for c in candidates}
    chosen = [by_name[capital_name]] if capital_name in by_name else [candidates[0]]
    for pool in (candidates, fallback):  # まず市を使い切ってから町村へ
        while len(chosen) < CITIES_PER_PREF:
            rest = [c for c in pool if c not in chosen]
            if not rest:
                break
            chosen.append(max(rest, key=lambda c: min(
                local_distance((c['lat'], c['lon']), (s['lat'], s['lon'])) for s in chosen)))
    return chosen


def main():
    print('データを取得します(数十MBあるため時間がかかります)', file=sys.stderr)
    geo = fetch_json(GEOJSON_URL)
    towns = fetch_json(LOCALGOV_URL)

    by_pref = {}
    for row in towns:
        # 「札幌市 中央区」のような政令市の行政区は、親の「札幌市」と重複するので除く
        if ' ' in row['city']:
            continue
        by_pref.setdefault(row['pref'], []).append({
            'name': row['city'], 'lat': float(row['lat']), 'lon': float(row['lng']),
        })

    result = {}
    for feature in geo['features']:
        pref_name = feature['properties']['nam_ja']  # 「福岡県」など
        # 末尾の「都/道/府/県」を1文字だけ落とす(rstripだと「京都府」が「京」になってしまう)
        key = pref_name if pref_name == '北海道' else pref_name[:-1]

        # 離島まで描くと形が読み取りにくいので、一番大きい島(=本土)だけを輪郭に使う
        mainland = max(exterior_rings(feature['geometry']), key=ring_area)
        if mainland[0] == mainland[-1]:
            mainland = mainland[:-1]
        outline = simplify_ring(mainland, OUTLINE_POINTS)

        # 候補は本土の上にある「市」。市が5つに満たない県(鳥取など)でだけ町村で補う
        on_mainland = [c for c in by_pref[pref_name] if point_in_ring(c['lon'], c['lat'], mainland)]
        cities = [c for c in on_mainland if c['name'].endswith('市')]
        if pref_name == '東京都':  # 23区を代表させるため、都庁のある新宿区だけ例外的に候補に含める
            cities += [c for c in on_mainland if c['name'] == CAPITALS['東京都']]
        towns = [c for c in on_mainland if c not in cities]

        chosen = pick_cities(cities, towns, CAPITALS[pref_name])
        result[key] = {
            'outline': [[round(lat, 4), round(lon, 4)] for lon, lat in outline],
            'cities': [{
                'name': DISPLAY_NAME_OVERRIDES.get((pref_name, c['name']), c['name']),
                'lat': round(c['lat'], 4), 'lon': round(c['lon'], 4),
            } for c in chosen],
        }
        print(f"  {key}: 輪郭{len(mainland)}→{len(outline)}点 / "
              f"{'・'.join(c['name'] for c in result[key]['cities'])}", file=sys.stderr)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    print(f'書き出しました: {os.path.abspath(OUT_PATH)} '
          f'({os.path.getsize(OUT_PATH) // 1024}KB / {len(result)}都道府県)', file=sys.stderr)


if __name__ == '__main__':
    sys.setrecursionlimit(20000)
    main()
