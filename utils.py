"""ルートやテンプレートから使う小さな共通処理。

いまはPC/スマホの出し分け判定だけを置いている。
"""
from flask import request, session

# 判定に使うUser-Agentのキーワード。
# iPadはiPadOS 13以降User-AgentがMacと同じになり判別できないため、当面PC扱いにしている。
MOBILE_USER_AGENT_KEYWORDS = ('iphone', 'android')

# テンプレートフォルダ名。この2つ以外は受け付けない(?uiで任意のパスを指定させないため)。
TEMPLATE_FOLDERS = ('pc', 'mobile')

# ?ui= の指定を覚えておくセッションのキー
UI_SESSION_KEY = 'ui_override'


def get_template_folder():
    """アクセス元の端末に合わせて 'pc' / 'mobile' のどちらのテンプレートを使うかを返す。

    優先順位は「URLの?ui= → セッションに覚えた指定 → User-Agent判定」。

    `?ui=mobile` `?ui=pc` を一度付ければ、その指定をセッションに覚えるので、
    リンクを辿ってもフォームを送信しても表示が固定されたままになる
    (PCのブラウザでスマホ版を通しで確認するため)。`?ui=auto` で自動判定に戻る。
    想定外の値は無視して、それまでの判定をそのまま使う。
    """
    override = request.args.get('ui')
    if override in TEMPLATE_FOLDERS:
        session[UI_SESSION_KEY] = override
        return override
    if override == 'auto':
        session.pop(UI_SESSION_KEY, None)

    remembered = session.get(UI_SESSION_KEY)
    if remembered in TEMPLATE_FOLDERS:
        return remembered

    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = any(keyword in user_agent for keyword in MOBILE_USER_AGENT_KEYWORDS)
    return 'mobile' if is_mobile else 'pc'
