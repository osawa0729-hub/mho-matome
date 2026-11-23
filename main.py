import os
import re
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
import tweepy

# --- 設定 ---
JST = timezone(timedelta(hours=+9), 'JST')
POSTS_FILE = "docs/posts.json"
SITE_URL = os.environ.get("SITE_URL", "https://あなたのID.github.io/mho-matome/")
# 5chのモンハンアウトランダーズ現行スレURL（または板のsubback.htmlから自動検索）
THREAD_URL = os.environ.get("TARGET_THREAD_URL", "")

# 1. 5chスレッドからレスを取得
def fetch_5ch_thread(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as res:
        # 5chはShift_JIS (cp932) が基本
        html = res.read().decode("cp932", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    posts = []
    # 現行5chのHTML構造(.post)に対応
    for post in soup.select(".post")[:150]:  # 負荷軽減のため最大150レス
        num = post.get("data-id") or (post.select_one(".number").text if post.select_one(".number") else "")
        name = post.select_one(".name").text.strip() if post.select_one(".name") else "名無しのハンター"
        date = post.select_one(".date").text.strip() if post.select_one(".date") else ""
        uid = post.select_one(".uid").text.strip() if post.select_one(".uid") else ""
        msg_el = post.select_one(".message")
        if msg_el:
            for br in msg_el.find_all("br"):
                br.replace_with("\n")
            body = msg_el.text.strip()
            posts.append(f"{num}: {name} {date} {uid}\n{body}")
    return "\n\n".join(posts)

# 2. Gemini APIでまとめ記事とX投稿文を生成
def summarize_with_gemini(raw_thread_text):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
あなたは『モンスターハンターアウトランダーズ（MHO）』専門の5chまとめサイト編集長です。
以下のスレッドの書き込みデータ**のみ**を使い、読者が読みたくなるまとめ記事を1本作成してください。

【厳守ルール】
- 提供されたレスに書かれていない事実・キャラ名・数値は絶対に捏造・補完しないこと。
- 転載禁止の記載があるレスや個人情報・誹謗中傷は除外すること。
- レス番号（>>12 など）の掛け合いが自然につながるよう15〜25レス程度を厳選すること。
- 出力は必ず以下のJSON形式のみとすること。

{{
  "title": "【モンハンアウトランダーズ】思わずクリックしたくなる記事タイトル（45文字以内）",
  "category": "攻略・考察 / ガチャ・キャラ / アプデ・イベント / 雑談 のいずれか1つ",
  "summary": "記事の3行要約",
  "tweet_text": "X(Twitter)投稿用の煽り文＋要約＋ハッシュタグ #モンハンアウトランダーズ #MHO （URLを除いて110文字以内）",
  "picked_comments": [
    {{
      "number": "レス番号",
      "header": "名前 日付 ID",
      "body": "レス本文",
      "color": "#ffffff または #ff4d4d（重要・面白いレスは赤や青で強調）",
      "is_bold": true
    }}
  ]
}}

【スレッドデータ】
{raw_thread_text[:25000]}
"""
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.4,
        ),
    )
    return json.loads(response.text)

# 3. サイト用JSONの更新
def save_post(article_data):
    os.makedirs("docs", exist_ok=True)
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
    else:
        posts = []

    now = datetime.now(JST)
    new_entry = {
        "id": now.strftime("%Y%m%d%H%M%S"),
        "published_at": now.strftime("%Y/%m/%d %H:%M"),
        **article_data
    }
    posts.insert(0, new_entry)
    posts = posts[:50]  # 最新50記事を保持

    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    return new_entry

# 4. X (Twitter) への自動投稿
def post_to_x(article):
    # APIキーが未設定の場合はスキップ
    if not os.environ.get("X_API_KEY"):
        print("X APIキー未設定のため投稿をスキップします")
        return

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"]
    )
    # ※URL付きポストは単価が高いため、コスト重視なら {SITE_URL} を外してください
    tweet_body = f"{article['tweet_text']}\n{SITE_URL}#post-{article['id']}"
    res = client.create_tweet(text=tweet_body)
    print("X投稿完了 Tweet ID:", res.data["id"])

if __name__ == "__main__":
    if not THREAD_URL:
        raise ValueError("環境変数 TARGET_THREAD_URL に5chのスレURLを設定してください")
    raw_text = fetch_5ch_thread(THREAD_URL)
    article = summarize_with_gemini(raw_text)
    saved = save_post(article)
    post_to_x(saved)
