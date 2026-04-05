# coding=utf-8
import requests
import time
import os
import logging
import concurrent.futures
from collections import defaultdict
from logging.handlers import TimedRotatingFileHandler

# ===================== 核心配置区 =====================
BASE_URL = "http://api.hclyz.com:81/mf"
M3U_FILE = "sbjh.m3u"
LOG_FILE = "scraper.log"
CACHE_FILE = "cache_crawled.txt"   # 断点缓存记录
MAX_KEEP_PER_GROUP = 999           # 无上限不限制条数

# 已移除黑名单，全部源都要
BLACK_LIST = []

# Telegram 自行填密钥
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

HEADERS = {"User-Agent": "Mozilla/5.0"}
VALID_PREFIX = ("http://", "https://", "rtmp://")
REQ_TIMEOUT = 12                   # 加长请求超时防卡死
PLAY_CHECK_TIMEOUT = 3             # 测速3秒 = TO=3000
MAX_WORKERS = 25                   # 并发测速线程
SLEEP_INTERVAL = 0.1
# ======================================================

def setup_logging():
    logger = logging.getLogger("ScraperLogger")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    file_handler = TimedRotatingFileHandler(LOG_FILE, when="D", interval=7, backupCount=1, encoding="utf-8")
    file_handler.setFormatter(formatter)
    return logger

log = setup_logging()

# ---------------- 断点缓存读写 ----------------
def load_crawled_cache():
    crawled = set()
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            crawled = set(x.strip() for x in f if x.strip())
    log.info(f"[断点续爬]已缓存完成页面：{len(crawled)} 个")
    return crawled

def save_one_crawled(key):
    with open(CACHE_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")

def clear_crawled_cache():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
        log.info("[断点]已清空缓存，下次从头爬")

# ---------------- 请求工具 ----------------
def safe_get_json(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"请求异常 {url} -> {e}")
        return None

def is_valid_stream_url(url):
    if not url:
        return False
    url = url.lower()
    return url.startswith(VALID_PREFIX) and (".m3u8" in url or ".flv" in url or ".mp4" in url or url.startswith("rtmp://"))

def check_alive(url):
    """3秒存活测速"""
    try:
        resp = requests.head(url, timeout=PLAY_CHECK_TIMEOUT, headers=HEADERS, allow_redirects=True)
        if resp.status_code in (200,301,302,304):
            return url, True
    except:
        pass
    try:
        resp = requests.get(url, timeout=PLAY_CHECK_TIMEOUT, headers=HEADERS, stream=True)
        for _ in resp.iter_content(chunk_size=1024):
            return url, True
    except:
        pass
    return url, False

def batch_check_urls(url_list):
    """批量多线程测速"""
    alive_set = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
        res = exe.map(check_alive, url_list)
    for u, ok in res:
        if ok:
            alive_set.add(u)
    return alive_set

# ---------------- TG推送 ----------------
def send_tg_msg(bot_token, chat_id, msg):
    if not bot_token or not chat_id:
        return
    try:
        api = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(api, data={"chat_id":chat_id,"text":msg,"parse_mode":"Markdown"}, timeout=15)
    except Exception as e:
        log.error(f"TG消息失败:{e}")

def send_tg_file(path, bot_token, chat_id):
    if not bot_token or not chat_id or not os.path.exists(path):
        return
    try:
        api = f"https://api.telegram.org/bot{bot_token}/sendDocument"
        with open(path,"rb") as f:
            requests.post(api, files={"document":f}, data={"chat_id":chat_id}, timeout=30)
    except Exception as e:
        log.error(f"TG文件上传失败:{e}")

def main():
    import signal
    class GlobalTimeout(Exception):pass
    def timeout_handler(signum, frame):
        raise GlobalTimeout("全局运行超时强制退出")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(900)   # 全局15分钟超长超时防卡死

    total_error = 0
    total_filtered = 0
    # 🔴 核心修复：首次运行清空缓存，强制从头爬
    clear_crawled_cache()
    crawled_cache = load_crawled_cache()

    send_tg_msg(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "🚀 断点续爬+无黑名单+无上限+测速 任务启动")
    log.info("🚀 开始抓取数据源")

    home = safe_get_json(f"{BASE_URL}/json.txt")
    if not home:
        log.error("首页获取失败，终止采集")
        return

    data = home.get("pingtai", [])
    if len(data)>=2:
        data = data[1:]
    data = sorted(data, key=lambda x:int(x.get("Number",0)or 0), reverse=True)

    group_bucket = defaultdict(list)
    seen_raw_url = set()

    remain_count = 0
    for item in data:
        room_title = item.get("title","").strip()
        address = item.get("address","")
        crawl_key = address

        # 断点跳过已爬过（首次运行缓存为空，不会跳过）
        if crawl_key in crawled_cache:
            log.info(f"[断点跳过]{room_title}")
            continue
        remain_count += 1

        if not address:
            total_error += 1
            continue

        detail = safe_get_json(f"{BASE_URL}/{address}")
        if not detail:
            total_error += 1
            continue
        zhubo = detail.get("zhubo", [])
        if not isinstance(zhubo, list):
            total_error += 1
            continue

        group_name = f"-{room_title}"
        for vod in zhubo:
            name = vod.get("title","").strip()
            url = vod.get("address","").strip()

            # 已移除黑名单过滤
            if not is_valid_stream_url(url):
                total_error += 1
                continue
            if url in seen_raw_url:
                continue
            seen_raw_url.add(url)
            group_bucket[group_name].append((name, url))

        # 当前页面抓取完成写入断点
        save_one_crawled(crawl_key)
        crawled_cache.add(crawl_key)
        time.sleep(SLEEP_INTERVAL)

    log.info(f"[断点统计]本轮抓取页面：{remain_count} 个")

    # 提取全部URL批量测速
    all_urls = []
    tmp_map = {}
    for gname, lst in group_bucket.items():
        for title, u in lst:
            all_urls.append(u)
            tmp_map[u] = (gname, title)

    log.info(f"📦 待测速总数：{len(all_urls)}")
    alive_url_set = batch_check_urls(all_urls)
    log.info(f"✅ 测速存活总数：{len(alive_url_set)}")

    # 存活链接重新归集分组
    alive_group = defaultdict(list)
    for u in alive_url_set:
        gname, title = tmp_map.get(u, (None, None))
        if gname and title:
            alive_group[gname].append((title, u))

    # 组装M3U头部（3秒换源标签）
    m3u_lines = [
        "#EXTM3U",
        "#TO=3000",
        "#IJKAD=300"
    ]

    total_final = 0
    # 每组只保留前 MAX_KEEP_PER_GROUP 条（无上限=999）
    for gname, lst in alive_group.items():
        cut_lst = lst[:MAX_KEEP_PER_GROUP]
        for title, url in cut_lst:
            m3u_lines.append(f'#EXTINF:-1 group-title="{gname}",{title}')
            m3u_lines.append(url)
            total_final += 1

    # 写出最终文件
    try:
        with open(M3U_FILE,"w",encoding="utf-8") as f:
            f.write("\n".join(m3u_lines))
        log.info(f"📄 最终M3U生成完毕：{M3U_FILE}，共{total_final}条")
    except Exception as e:
        log.error(f"写入文件失败:{e}")

    log.info(f"💡汇总：最终留存{total_final}条 | 屏蔽{total_filtered} | 异常{total_error}")
    send_tg_msg(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        f"✅断点续爬采集完成\n每组无上限不限制\n最终存活：{total_final}\n解析异常：{total_error}")
    send_tg_file(M3U_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

if __name__ == "__main__":
    try:
        main()
    except GlobalTimeout:
        log.warning("[⏰]全局15分钟超时，本轮结束，下次自动断点续爬")
    except Exception as e:
        log.error(f"[💥]全局异常终止：{e}")
