import html
import json
import os
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types

# 指定スレURL（空文字 "" にすると板一覧から「アウトランダーズ」の現行スレを自動検索）
TARGET_THREAD_URL = "https://krsw.5ch.io/test/read.cgi/gamesm/1795777624/l50"
BOARD_SUBBACK_URL = "https://krsw.5ch.io/gamesm/subback.html"

SOURCE_FILE = "scripts/source.txt"
POSTS_FILE = "docs/posts.json"
JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def fetch_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as res:
        raw = res.read()
        for enc in ("utf-8", "cp932", "shift_jis", "euc-jp"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")


def find_latest_thread_url():
    """板のsubback.htmlから「アウトランダーズ」の現行スレURLを自動取得"""
    subback_html = fetch_html(BOARD_SUBBACK_URL)
    matches = re.findall(r'<a href="(\d+)/l50">([^<]+)</a>', subback_html)
    for thread_id, title in matches:
        if "アウトランダーズ" in title:
            return f"https://krsw.5ch.io/test/read.cgi/gamesm/{thread_id}/"
    return None


def parse_5ch_thread(thread_html):
    """5chのHTMLからレス番号・名前・日時・本文を抽出してテキスト化"""
    posts_text = []

    blocks = re.findall(
        r'<div class="(?:post|thread)"[^>]*data-id="(\d+)"[^>]*>(.*?)</div>\s*</div>',
        thread_html,
        re.DOTALL,
    )

    if blocks:
        for num, block in blocks:
            name_m = re.search(r'class="name"[^>]*>(.*?)</(?:span|div)>', block, re.DOTALL)
            date_m = re.search(r'class="date"[^>]*>(.*?)</(?:span|div)>', block, re.DOTALL)
            msg_m = re.search(r'class="(?:message|escaped)"[^>]*>(.*?)</div>', block, re.DOTALL)
            if msg_m:
                name = re.sub(r"<[^>]+>", "", name_m.group(1)).strip() if name_m else "名無しですよ、名無し！"
                date = re.sub(r"<[^>]+>", "", date_m.group(1)).strip() if date_m else ""
                body = msg_m.group(1).replace("<br>", "\n").replace("<br />", "\n")
                body = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
                posts_text.append(f"{num}: {name} {date}\n{body}")
    else:
        dt_dd = re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", thread_html, re.DOTALL)
        for dt, dd in dt_dd:
            header = html.unescape(re.sub(r"<[^>]+>", "", dt)).strip()
            body = dd.replace("<br>", "\n").replace("<br />", "\n")
            body = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
            posts_text.append(f"{header}\n{body}")

    return "\n\n".join(posts_text)


def get_thread_content():
    """5chから直接取得を試み、失敗時はsource.txtへフォールバック"""
    url = TARGET_THREAD_URL.strip()
    try:
        if not url:
            url = find_latest_thread_url() or ""
        if url:
            clean_url = re.sub(r"/l\d+$", "/", url)
            if not clean_url.endswith("/"):
                clean_url += "/"
            raw_html = fetch_html(url)
            parsed_text = parse_5ch_thread(raw_html)
            if len(parsed_text) > 200:
                print(f"Fetched thread directly from: {clean_url}")
                return parsed_text, clean_url
    except Exception as e:
        print(f"Direct 5ch fetch failed ({e}), falling back to {SOURCE_FILE}...")

    if os.path.exists(SOURCE_FILE):
        with open(SOURCE_FILE, "r", encoding="utf-8") as f:
            fallback_url = re.sub(r"/l\d+$", "/", TARGET_THREAD_URL.strip()) if TARGET_THREAD_URL else ""
            return f.read().strip(), fallback_url

    return "", ""


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

    for post in posts[:30]:
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

    raw_thread_text, source_url = get_thread_content()
    if not raw_thread_text:
        print("No thread text available. Exiting.")
        return

    existing_posts = load_existing_posts()
    used_titles, used_res_nums = extract_used_info(existing_posts)

    client = genai.Client(api_key=api_key)

    prompt = f"""
あなたは『FGO攻略まとめ隊（fgo.news）』のような人気ゲーム系5chまとめサイトの熟練編集者です。
以下のスレッド本文から、まだ記事化されていない「1つの明確な話題」だけを抽出し、読者がテンポよく読めるまとめ記事JSONを作成してください。

【絶対厳守ルール】
1. **1つの話題に絞る（別話題の混在禁止）**：
   - スレッド内の別の質問や無関係な雑談を穴埋めで混ぜないこと。選んだテーマに直接関係するレスだけを抜粋すること（4〜10レス程度で十分）。
2. **1レス目とタイトルの完全一致**：
   - `comments` の **1番目（先頭）** には、必ずその話題の起点（質問・報告・ネタ振り）となるレスを置くこと。
   - `title` は **1レス目の内容・主張をそのまま反映した見出し** にすること（20〜30文字程度、問いかけやリアクション調）。
3. **既存記事との重複禁止**：
   - 以下の既存タイトル・使用済みレス番号と話題やレスが被らないようにすること。
     - 既存タイトル一覧: {json.dumps(used_titles, ensure_ascii=False)}
     - 使用済みレス番号: {json.dumps(used_res_nums, ensure_ascii=False)}
4. **fgo.news風の文字強調（highlight）を必ず設定**：
   - 各レスの `highlight` は必ず以下の4種類から選び、記事全体の半分程度にメリハリをつけること（すべて `"none"` にするのは禁止）：
     - `"red"`：一番の核心・重要攻略情報・衝撃レス（大文字・赤太字）
     - `"blue"`：記事のオチ・秀逸なボケやツッコミ・笑い（中大文字・青太字）
     - `"bold"`：話の要点・共感を集める意見（黒太字）
     - `"none"`：通常の繋ぎレス（通常文字）

【スレッド本文】
{raw_thread_text[-25000:]}
"""

    response_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(type=types.Type.STRING),
            "comments": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "num": types.Schema(type=types.Type.STRING),
                        "name": types.Schema(type=types.Type.STRING),
                        "date": types.Schema(type=types.Type.STRING),
                        "body": types.Schema(type=types.Type.STRING),
                        "highlight": types.Schema(
                            type=types.Type.STRING,
                            enum=["red", "blue", "bold", "none"],
                        ),
                    },
                    required=["num", "name", "body", "highlight"],
                ),
            ),
        },
        required=["title", "comments"],
    )

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.4,
        ),
    )

    new_article = json.loads(response.text.strip())

    if not new_article.get("title") or not new_article.get("comments"):
        raise ValueError("Generated JSON is missing 'title' or 'comments'.")

    now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    formatted_post = {
        "id": datetime.now(JST).strftime("%Y%m%d%H%M%S"),
        "title": new_article["title"].strip(),
        "date": now_str,
        "source_url": source_url,
        "comments": new_article["comments"],
    }

    updated_posts = [formatted_post] + existing_posts

    os.makedirs(os.path.dirname(POSTS_FILE), exist_ok=True)
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(updated_posts, f, ensure_ascii=False, indent=2)

    print(f"Successfully added article: {formatted_post['title']}")


if __name__ == "__main__":
    main()
