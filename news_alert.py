"""
키워드 뉴스 모니터링 -> 텔레그램 알림 스크립트
------------------------------------------------
- 구글뉴스(RSS) + 네이버뉴스(검색결과)에서 지정 키워드가 포함된 새 기사를 찾아
  텔레그램으로 알림을 보냅니다.
- 이미 보낸 기사는 seen_links.json 에 기록해 중복 알림을 막습니다.

[사전 준비]
1) 텔레그램에서 @BotFather 검색 -> /newbot 으로 봇 생성 -> BOT TOKEN 발급
2) 만든 봇과 대화 시작(아무 메시지나 전송) 후,
   https://api.telegram.org/bot<발급받은토큰>/getUpdates 접속해서 "chat":{"id": ... } 값 확인 -> CHAT_ID
3) 아래 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 에 입력
4) pip install feedparser requests beautifulsoup4

[iPhone에서 쓰는 방법]
이 스크립트는 컴퓨터/서버(클라우드)에서 계속 돌아가야 합니다.
- 24시간 켜둘 PC가 없다면 GitHub Actions, PythonAnywhere, Replit 등
  무료 클라우드 서비스에 올려서 일정 주기로 실행하도록 스케줄을 걸면 됩니다.
- 아이폰에는 아무것도 설치할 필요 없이, 텔레그램 앱만 켜져 있으면
  새 기사가 나올 때마다 알림이 옵니다.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import calendar
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))  # 한국 시간 기준

# ==================== 설정 ====================
KEYWORDS = ["펩트론", "비만"]

# 로컬(내 컴퓨터)에서 테스트할 땐 아래 두 줄에 직접 입력해도 됩니다.
# GitHub Actions로 클라우드에서 돌릴 땐 Secrets 값이 자동으로 들어오므로 그대로 두세요.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "여기에_봇_토큰_입력")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "여기에_챗_아이디_입력")

CHECK_INTERVAL_SEC = 1800  # 로컬 반복 실행용 (GitHub Actions에서는 사용 안 함)
SEEN_FILE = "seen_links.json"
# ================================================


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"[텔레그램 전송 실패] {e}")


def is_today_kst(dt_utc):
    """UTC 시간을 한국 시간으로 바꿔서 오늘 날짜인지 확인"""
    dt_kst = dt_utc.astimezone(KST)
    now_kst = datetime.now(KST)
    return dt_kst.date() == now_kst.date()


def fetch_google_news(keyword):
    url = f"https://news.google.com/rss/search?q={keyword}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries[:15]:
        # 기사 작성 시각을 확인해서 오늘 기사가 아니면 건너뜀
        if getattr(entry, "published_parsed", None):
            pub_dt = datetime.fromtimestamp(
                calendar.timegm(entry.published_parsed), tz=timezone.utc
            )
            if not is_today_kst(pub_dt):
                continue
        results.append({"title": entry.title, "link": entry.link, "source": "구글뉴스"})
    return results


def is_naver_time_today(time_text):
    """네이버 검색결과에 뜨는 '3시간 전', '2일 전', '2026.09.15.' 같은 표시로 오늘 기사인지 판단"""
    text = time_text.strip()
    if not text:
        return True  # 시간 표시가 없으면 일단 포함 (아래 로직에서 걸러지지 않도록)

    # '2026.09.15.' 처럼 절대 날짜로 표시되는 경우
    date_match = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", text)
    if date_match:
        today_str = datetime.now(KST).strftime("%Y.%m.%d")
        return text.startswith(today_str)

    # '3일 전', '2주 전', '1개월 전', '1년 전' -> 오늘 기사 아님
    if any(unit in text for unit in ["일 전", "주 전", "개월 전", "년 전"]):
        return False

    # '방금', 'N분 전', 'N시간 전' -> 오늘 기사로 간주
    return True


def fetch_naver_news(keyword):
    url = f"https://search.naver.com/search.naver?where=news&query={keyword}&sort=1"
    headers = {"User-Agent": "Mozilla/5.0"}
    results = []
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        for area in soup.select("div.news_area"):
            a = area.select_one("a.news_tit")
            if not a:
                continue
            title = a.get("title") or a.text
            link = a.get("href")
            if not link:
                continue

            info_spans = area.select("span.info")
            time_text = info_spans[-1].get_text(strip=True) if info_spans else ""
            if not is_naver_time_today(time_text):
                continue

            results.append({"title": title, "link": link, "source": "네이버뉴스"})
    except Exception as e:
        print(f"[네이버 뉴스 가져오기 실패] {e}")
    return results


def check_news():
    seen = load_seen()
    new_seen = set(seen)

    for keyword in KEYWORDS:
        articles = fetch_google_news(keyword) + fetch_naver_news(keyword)
        for art in articles:
            if art["link"] not in seen:
                new_seen.add(art["link"])
                msg = f"[{art['source']}] '{keyword}' 관련 기사\n\n{art['title']}\n{art['link']}"
                send_telegram(msg)
                print(f"{datetime.now()} - 알림 전송: {art['title']}")

    save_seen(new_seen)


def main():
    # GitHub Actions에서는 스케줄(cron)이 반복 실행을 담당하므로 1회만 확인하고 종료합니다.
    # 내 컴퓨터에서 계속 켜두고 반복 실행하고 싶다면 아래 while 루프 주석을 해제하세요.
    print(f"{datetime.now()} - 뉴스 확인 중...")
    check_news()

    # while True:
    #     check_news()
    #     time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
