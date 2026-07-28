"""ルートやテンプレートから使う小さな共通処理。

いまはPC/スマホの出し分け判定だけを置いている。
"""
from flask import request

# 判定に使うUser-Agentのキーワード。
# iPadはiPadOS 13以降User-AgentがMacと同じになり判別できないため、当面PC扱いにしている。
MOBILE_USER_AGENT_KEYWORDS = ('iphone', 'android')

# テンプレートフォルダ名。この2つ以外は受け付けない(?uiで任意のパスを指定させないため)。
TEMPLATE_FOLDERS = ('pc', 'mobile')


def get_template_folder():
    """アクセス元の端末に合わせて 'pc' / 'mobile' のどちらのテンプレートを使うかを返す。

    `?ui=mobile` `?ui=pc` を付けると強制的に切り替えられる(PCのブラウザからスマホ版を
    確認するため)。想定外の値のときは無視して、通常のUser-Agent判定に戻す。
    """
    override = request.args.get('ui')
    if override in TEMPLATE_FOLDERS:
        return override
    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = any(keyword in user_agent for keyword in MOBILE_USER_AGENT_KEYWORDS)
    return 'mobile' if is_mobile else 'pc'
