import os
import re
import json
import urllib.request
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
import tweepy

JST = timezone(timedelta(hours=+9), "JST")
POSTS_FILE = "docs/posts.json"
STATE_FILE = "docs/state.json"
SITE_URL = os.environ.get("SITE_URL", "https://osawa0729-hub.github.io/mho-matome/")

BOARD_URL = os.environ.get("BOARD_URL", "https://krsw.5ch.net/gamesm/")
THREAD_KEYWORD = os.environ.get("THREAD_KEYWORD", "アウトランダーズ")
MIN_NEW_RES = int(os.environ.get("MIN_NEW_RES", "20"))


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"threads": {}}


def save_state(state):
    os.makedirs("docs", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def find_latest_thread_url(board_url, keyword):
    subback_url = urljoin(board_url.rstrip("/") + "/", "subback.html")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = urllib.request.Request(subback_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as res:
        html = res.read().decode("cp932", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    for a in soup.select("#trad a, .thread-list a, a"):
        title = a.text.strip()
        href = a.get("href", "")
        if keyword in title and href:
            match = re.search(r"/test/read\.cgi/([^/]+)/(\d+)", href)
            if match:
                server_host = urlparse(board_url).netloc
                board_key, thread_key = match.group(1), match.group(2)
                clean_url = f"https://{server_host}/test/read.cgi/{board_key}/{thread_key}/"
                count_match = re.search(r"\((\d+)\)\s*$", a.parent.text if a.parent else "")
                res_count = int(count_match.group(1)) if count_match else 0
                candidates.append((res_count, clean_url, title))

    if not candidates:
        raise RuntimeError(f"キーワード「{keyword}」に一致するスレッドが見つかりませんでした")

    active = [c for c in candidates if c[0] < 1000]
    chosen = active[0] if active else candidates[0]
    print(f"対象スレ検出: {chosen[2]} ({chosen[0]}レス) -> {chosen[1]}")
    return chosen[1]


def fetch_new_posts(thread_url, already_used_nums):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = urllib.request.Request(thread_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as res:
        html = res.read().decode("cp932", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    used_set = set(str(n) for n in already_used_nums)
    new_items = []
    all_nums = []

    for post in soup.select(".post"):
        num = str(post.get("data-id") or (post.select_one(".number").text.strip() if post.select_one(".number") else ""))
        if not num:
            continue
        all_nums.append(num)
        if num in used_set:
            continue

        name = post.select_one(".name").text.strip() if post.select_one(".name") else "名無しのハンター"
        date = post.select_one(".date").text.strip() if post.select_one(".date") else ""
        uid = post.select_one(".uid").text.strip() if post.select_one(".uid") else ""
        msg_el = post.select_one(".message")
        if msg_el:
            for br in msg_el.find_all("br"):
                br.replace_with("\n")
            body = msg_el.text.strip()
            new_items.append((num, f"{num}: {name} {date} {uid}\n{body}"))

    recent = new_items[-150:]
    formatted_text = "\n\n".join(text for _, text in recent)
    used_nums_in_batch = [num for num, _ in recent]
    return formatted_text, used_nums_in_batch, all_nums


def summarize_with_gemini(raw_thread_text):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
あなたは『モンスターハンターアウトランダーズ（MHO）』専門の5chまとめサイト編集長です。
以下の未まとめレスデータ**のみ**を使い、読者が読みたくなるまとめ記事を1本作成してください。

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

【未まとめレスデータ】
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


def save_post(article_data, source_url):
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
        "source_url": source_url,
        **article_data,
    }
    posts.insert(0, new_entry)
    posts = posts[:50]

    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    return new_entry


def post_to_x(article):
    if not os.environ.get("X_API_KEY"):
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
    state = load_state()
    env_thread = os.environ.get("TARGET_THREAD_URL", "").strip()
    thread_url = env_thread if env_thread else find_latest_thread_url(BOARD_URL, THREAD_KEYWORD)

    thread_state = state["threads"].get(thread_url, {"used_nums": []})
    raw_text, batch_nums, _ = fetch_new_posts(thread_url, thread_state["used_nums"])

    if len(batch_nums) < MIN_NEW_RES:
        print(f"未まとめレスが {len(batch_nums)} 件（基準: {MIN_NEW_RES} 件未満）のためスキップします")
        raise SystemExit(0)

    article = summarize_with_gemini(raw_text)
    saved = save_post(article, thread_url)
    post_to_x(saved)

    merged_nums = list(dict.fromkeys(thread_state["used_nums"] + batch_nums))[-2000:]
    state["threads"][thread_url] = {"used_nums": merged_nums}
    save_state(state)
