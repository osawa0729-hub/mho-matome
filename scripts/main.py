import json
import os
import re
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types

# パス設定
SOURCE_FILE = "scripts/source.txt"
POSTS_FILE = "docs/posts.json"

# タイムゾーン（日本時間）
JST = timezone(timedelta(hours=9))


def load_existing_posts():
    if not os.path.exists(POSTS_FILE):
        return []
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def extract_used_info(posts):
    used_titles = []
    used_res_nums = set()

    for post in posts[:30]:  # 直近30記事分をチェック
        title = post.get("title", "")
        if title:
            used_titles.append(title)

        for c in post.get("comments", []):
            if isinstance(c, dict):
                num = str(c.get("num", "")).strip()
                if num:
                    used_res_nums.add(num)
            elif isinstance(c, str):
                m = re.match(r"^(\d+)\s*[:：]", c.strip())
                if m:
                    used_res_nums.add(m.group(1))

    return used_titles, sorted(list(used_res_nums))


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set.")

    if not os.path.exists(SOURCE_FILE):
        raise FileNotFoundError(f"{SOURCE_FILE} not found.")

    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        raw_thread_text = f.read().strip()

    if not raw_thread_text:
        print("source.txt is empty. Exiting.")
        return

    existing_posts = load_existing_posts()
    used_titles, used_res_nums = extract_used_info(existing_posts)

    client = genai.Client(api_key=api_key)

    prompt = f"""
あなたは『モンスターハンターアウトランダーズ』専門の5chまとめサイト編集者です。
以下のスレッド本文から、まだ記事化されていない「1つの明確な話題」だけを抽出し、まとめ記事用のJSONを1件作成してください。

【厳守ルール】
1. タイトルは【20文字前後（15〜24文字）】で簡潔にまとめること。
2. タイトルの語尾は「〜なのか？」「〜どう思う？」「〜すぎない？」「〜って本当？」などの【問いかけ調】を基本にすること。
3. 要約文・解説文・前置きは一切不要。記事は「タイトル」と「厳選レス」だけで構成すること。
4. 既存記事と話題やレスが被らないようにすること。以下の既存タイトル・使用済みレス番号は絶対に使わないこと。
   - 既存タイトル一覧: {json.dumps(used_titles, ensure_ascii=False)}
   - 使用済みレス番号: {json.dumps(used_res_nums, ensure_ascii=False)}
5. レスは選択した1つの話題の流れ・オチが分かるものだけを【6〜12レス程度】厳選すること。無関係な雑談・単発の煽り・重複レスは省くこと。
6. レスの `header` は「レス番号: 名前（IDなどは省略してスッキリさせる）」の形式にし、`body` は読みやすく改行を整えること。

【出力JSONフォーマット（この構造のみ出力）】
{{
  "title": "20字前後の問いかけタイトル",
  "comments": [
    {{
      "num": "380",
      "header": "380: 名無しですよ、名無し！",
      "body": "レス本文"
    }}
  ]
}}

【スレッド本文】
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

    result_text = response.text.strip()
    new_article = json.loads(result_text)

    if not new_article.get("title") or not new_article.get("comments"):
        raise ValueError("Generated JSON is missing 'title' or 'comments'.")

    # 記事メタデータ整形（summaryは持たせない）
    now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    formatted_post = {
        "id": datetime.now(JST).strftime("%Y%m%d%H%M%S"),
        "title": new_article["title"].strip(),
        "date": now_str,
        "comments": new_article["comments"],
    }

    # 先頭に追加して保存
    updated_posts = [formatted_post] + existing_posts

    os.makedirs(os.path.dirname(POSTS_FILE), exist_ok=True)
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(updated_posts, f, ensure_ascii=False, indent=2)

    print(f"Successfully added article: {formatted_post['title']}")


if __name__ == "__main__":
    main()
