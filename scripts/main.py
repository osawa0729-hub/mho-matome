import os
import json
import time
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types
import tweepy

JST = timezone(timedelta(hours=+9), "JST")
SOURCE_FILE = "scripts/source.txt"
POSTS_FILE = "docs/posts.json"
SITE_URL = os.environ.get("SITE_URL", "").strip() or "https://osawa0729-hub.github.io/mho-matome/"


def load_source_text():
    if not os.path.exists(SOURCE_FILE):
        raise RuntimeError(f"{SOURCE_FILE} が見つかりません。元ネタのレスを貼り付けて作成してください。")
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        raise RuntimeError(f"{SOURCE_FILE} が空です。レスを貼り付けて保存してください。")
    return text


def summarize_with_gemini(raw_thread_text):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
あなたは『モンスターハンターアウトランダーズ（MHO）』専門の5chまとめサイト編集長です。
以下のレスデータ**のみ**を使い、読者が読みたくなるまとめ記事を1本作成してください。

【厳守ルール】
- 提供されたレスに書かれていない事実・キャラ名・数値は絶対に捏造・補完しないこと。
- 転載禁止の記載があるレスや個人情報・誹謗中傷は除外すること。
- レス番号の掛け合いが自然につながるよう15〜25レス程度を厳選すること。
- 出力は必ず以下のJSON形式のみとすること。

{{
  "title": "【モンハンアウトランダーズ】思わずクリックしたくなる記事タイトル（45文字以内）",
  "category": "攻略・考察 / ガチャ・キャラ / アプデ・イベント / 雑談 のいずれか1つ",
  "summary": "記事の3行要約",
  "tweet_text": "X投稿用の煽り文＋要約＋ハッシュタグ #モンハンアウトランダーズ #MHO （URLを除いて110文字以内）",
  "picked_comments": [
    {{
      "number": "レス番号",
      "header": "名前 日付 ID",
      "body": "レス本文",
      "color": "#222222 または #dc2626（重要・面白いレスは赤で強調）",
      "is_bold": true
    }}
  ]
}}

【レスデータ】
{raw_thread_text[:25000]}
"""
    last_err = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.4,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            last_err = e
            print(f"試行{attempt + 1}回目でエラー: {e}")
            time.sleep(2)
    raise last_err


def save_post(article_data, source_label):
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
        "source_url": source_label,
        **article_data,
    }
    posts.insert(0, new_entry)
    posts = posts[:50]

    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    return new_entry


def post_to_x(article):
    if not os.environ.get("X_API_KEY", "").strip():
        print("X APIキー未設定のため投稿をスキップします")
        return

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    tweet_body = f"{article['tweet_text']}\n{SITE_URL}#post-{article['id']}"
    res = client.create_tweet(text=tweet_body)
    print("X投稿完了 Tweet ID:", res.data["id"])


if __name__ == "__main__":
    raw_text = load_source_text()
    article = summarize_with_gemini(raw_text)
    saved = save_post(article, SOURCE_FILE)
    post_to_x(saved)
