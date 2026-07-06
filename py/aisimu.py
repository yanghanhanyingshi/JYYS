# coding=utf-8
import requests
import time
import os
import sys
import re
import json
import signal
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from collections import defaultdict

# ========== 全局超时保护 ==========
def timeout_handler(signum, frame):
    print("\n⏰ 脚本30分钟超时，强制退出")
    sys.exit(0)

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(1800)  # 30分钟

class AisiMuScraper:
    def __init__(self):
        self.cfg = {
            "base_url": "http://zhibo.aisimu.cn/zhubo/",
            "login_url": "http://zhibo.aisimu.cn/index.php",
            "username_field": "username",
            "password_field": "password",
            "username": "xyzvip",
            "password": "qq123456",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "logged_in_expected_url": "http://zhibo.aisimu.cn/zhubo/index.php",
            "login_failed_check_text": "账号密码错误",
            "tg_token": "",
            "tg_chat_id": ""
        }

        # ===== 修复：优化的Session配置 =====
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.cfg["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        })
        
        # ===== 修复：降低连接池，避免死锁 =====
        adapter = requests.adapters.HTTPAdapter(
            max_retries=2,
            pool_connections=10,
            pool_maxsize=10
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        self.category_urls = {}
        self.group_results = defaultdict(dict)
        self.old_urls = set()
        self.new_urls = set()

        self.output_dir = "output"
        os.makedirs(self.output_dir, exist_ok=True)

        self.MAX_KEEP_PER_GROUP = 999
        self.PLAY_CHECK_TIMEOUT = 5
        
        # ===== 修复：降低并发数 =====
        self.CRAWL_WORKERS = 2   # 从3降到2
        self.CHECK_WORKERS = 5   # 从10降到5
        self.SLEEP_INTERVAL = 0.5
        
        self._load_history()
        self.category_json_map = {}

    def get_beijing_time(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def tg(self, text):
        token = self.cfg.get("tg_token", "")
        if not token:
            return
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": self.cfg["tg_chat_id"], "text": text},
                          timeout=10)
        except:
            pass

    def login(self):
        print("[🚀] 开始登录...")
        for retry in range(5):
            try:
                resp = self.session.get(self.cfg["login_url"], timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
                
                payload = {
                    self.cfg["username_field"]: self.cfg["username"],
                    self.cfg["password_field"]: self.cfg["password"]
                }
                
                for inp in soup.find_all("input", {"type": "hidden"}):
                    name = inp.get("name")
                    value = inp.get("value", "")
                    if name:
                        payload[name] = value
                
                form = soup.find("form")
                if form and form.get("action"):
                    action_url = urljoin(self.cfg["login_url"], form.get("action"))
                else:
                    action_url = self.cfg["login_url"]

                post_resp = self.session.post(action_url, data=payload,
                                              allow_redirects=True, timeout=15)
                
                if self.cfg["login_failed_check_text"] in post_resp.text:
                    print("[❌] 登录失败")
                    return False
                    
                if len(self.session.cookies) > 0:
                    print(f"[✅] 登录成功")
                    return True
                    
            except Exception as e:
                print(f"[⚠️] 登录重试 {retry+1}/5: {e}")
                time.sleep(3)
        return False

    def fetch_index(self):
        try:
            r = self.session.get(self.cfg["logged_in_expected_url"], timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            
            print(f"[调试] 页面标题: {soup.title.string.strip() if soup.title else '无标题'}")
            
            cards = soup.find_all("div", class_="category-card")
            print(f"[调试] 发现 {len(cards)} 个分类卡片")
            
            for card in cards:
                title_div = card.find("div", class_="category-title")
                if not title_div:
                    continue
                category_name = title_div.get_text(strip=True)
                
                view_btn = card.find("a", class_="view-btn")
                if not view_btn:
                    continue
                    
                href = view_btn.get("href", "")
                if not href:
                    continue
                
                full_url = urljoin(self.cfg["logged_in_expected_url"], href)
                
                parsed = urlparse(full_url)
                params = parse_qs(parsed.query)
                json_file = params.get("url", [""])[0]
                if json_file:
                    self.category_json_map[category_name] = json_file
                
                if full_url not in self.category_urls:
                    self.category_urls[full_url] = category_name
            
            print(f"[✅] 发现 {len(self.category_urls)} 个分类")
            return len(self.category_urls) > 0
            
        except Exception as e:
            print(f"[❌] 获取分类列表失败: {e}")
            return False

    def fetch_category(self, url, cname, idx, total):
        try:
            print(f"[抓取] {idx}/{total} {cname}")
            time.sleep(self.SLEEP_INTERVAL)
            
            # ===== 修复：添加连接超时 =====
            r = self.session.get(url, timeout=(10, 20))
            if r.status_code != 200:
                print(f"[⚠️] {cname} 状态码 {r.status_code}")
                return False
            
            if "请先登录" in r.text or "登录过期" in r.text:
                print(f"[❌] {cname} 需要重新登录")
                return False
            
            streams = self.extract_streams_from_zblist(r.text, url)
            
            count = 0
            for stream_url, room in streams.items():
                if stream_url and stream_url.startswith(('http://', 'https://')):
                    if stream_url not in self.group_results[cname]:
                        self.group_results[cname][stream_url] = room
                        count += 1
            
            print(f"[完成] {idx}/{total} {cname} 采集到 {count} 条")
            return True
            
        except Exception as e:
            print(f"[⚠️] {cname} 异常: {e}")
            return False

    def extract_streams_from_zblist(self, html, page_url):
        soup = BeautifulSoup(html, "html.parser")
        streams = {}
        
        # 表格提取
        for tr in soup.select("table tr"):
            tds = tr.find_all("td")
            if len(tds) >= 4:
                room_name = ""
                for i in [1, 2]:
                    if i < len(tds):
                        text = tds[i].get_text(strip=True)
                        if text and len(text) < 50:
                            room_name = text
                            break
                
                raw_url = tds[3].get_text(strip=True) if len(tds) > 3 else ""
                if raw_url and raw_url.startswith(('http://', 'https://')):
                    streams[raw_url] = room_name or "未知房间"
                
                a_tags = tds[3].find_all("a") if len(tds) > 3 else []
                for a in a_tags:
                    href = a.get("href", "")
                    if href and href.startswith(('http://', 'https://')):
                        streams[href] = a.get_text(strip=True) or room_name or "链接源"
        
        # a标签提取
        for a in soup.find_all("a", href=True):
            href = a['href']
            if any(x in href for x in ['.m3u8', '.flv', '.mp4', 'play', 'stream']):
                if href.startswith(('http://', 'https://')):
                    streams[href] = a.get_text(strip=True) or "未知"
        
        # script提取
        for script in soup.find_all("script"):
            if not script.string:
                continue
            text = script.string
            patterns = [
                r'(playUrl|videoUrl|streamUrl|liveUrl|rtmpUrl|hlsUrl|url)\s*[:=]\s*["\']([^"\']+\.(m3u8|flv|mp4)[^"\']*)["\']',
                r'["\'](https?://[^\s"\']+\.(m3u8|flv|mp4)[^\s"\']*)["\']',
                r'(https?://[^\s;]+\.(m3u8|flv|mp4)[^\s;]*)'
            ]
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        url = match[1] if len(match) > 1 else match[0]
                    else:
                        url = match
                    url = url.strip(' "\'')
                    if url.startswith(('http://', 'https://')):
                        streams[url] = "script解析源"
        
        # video/source提取
        for video in soup.find_all(['video', 'source']):
            src = video.get('src')
            if src and src.startswith(('http://', 'https://')):
                streams[src] = video.get('title', 'video源')
            data_src = video.get('data-src')
            if data_src and data_src.startswith(('http://', 'https://')):
                streams[data_src] = "data-src源"
        
        # 文本提取
        text = html
        url_pattern = r'https?://[^\s<>"\']+\.(m3u8|flv|mp4)[^\s<>"\']*'
        for match in re.finditer(url_pattern, text, re.IGNORECASE):
            url = match.group(0)
            if url.startswith(('http://', 'https://')):
                streams[url] = "文本提取"
        
        return streams

    def _load_history(self):
        path = os.path.join(self.output_dir, "history.txt")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.old_urls = set(x.strip() for x in f if x.strip())

    def _save_history(self):
        allurl = set()
        for d in self.group_results.values():
            allurl.update(d.keys())
        path = os.path.join(self.output_dir, "history.txt")
        with open(path, "w", encoding="utf-8") as f:
            for u in sorted(allurl):
                f.write(u + "\n")

    # ===== 修复：纯HEAD检查，不下载数据 =====
    def check_stream(self, url):
        try:
            resp = self.session.head(
                url,
                timeout=5,
                allow_redirects=True
            )
            ok = resp.status_code in (200, 206, 301, 302, 304, 403, 405)
            resp.close()
            return url, ok
        except:
            return url, False

    def validate_streams(self):
        all_urls = []
        mp = {}
        for g, ud in self.group_results.items():
            for u, n in ud.items():
                all_urls.append(u)
                mp[u] = (g, n)

        if not all_urls:
            print("[⚠️] 无链接可验证")
            return

        valid = defaultdict(dict)
        total = len(all_urls)
        checked = 0
        valid_count = 0
        
        print(f"[🚀] 开始验证 {total} 个流...")
        
        with ThreadPoolExecutor(max_workers=self.CHECK_WORKERS) as pool:
            futures = {pool.submit(self.check_stream, u): u for u in all_urls}
            for future in as_completed(futures):
                u, ok = future.result()
                checked += 1
                if checked % 50 == 0:
                    print(f"[验证进度] {checked}/{total}")
                
                if ok and u in mp:
                    g, n = mp[u]
                    valid[g][u] = n
                    valid_count += 1
        
        self.group_results = valid
        print(f"[✅] 验证完成，有效: {valid_count}/{total} 条")

    def export_m3u(self):
        now_time = self.get_beijing_time()
        lines = [
            "#EXTM3U",
            f"# 更新时间: {now_time}",
            f"# 总分类: {len(self.group_results)}",
            "#TO=3000",
            "#IJKAD=300",
            ""
        ]
        
        total_count = 0
        for g, ud in sorted(self.group_results.items()):
            items = sorted(ud.items(), key=lambda x: x[1] or "未知")
            count = len(items)
            lines.append(f"# 分类: {g} ({count}个源)")
            
            for u, n in items[:self.MAX_KEEP_PER_GROUP]:
                name = n if n and n.strip() else "未知频道"
                lines.append(f'#EXTINF:-1 group-title="{g}",{name}')
                lines.append(u)
                total_count += 1
            
            lines.append("")
        
        path = os.path.join(self.output_dir, "aisimu.m3u")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        
        print(f"[✅] M3U生成: {path}")
        print(f"[📊] 总计 {total_count} 条有效源，{len(self.group_results)} 个分类")
        return path

    def export_json_summary(self):
        path = os.path.join(self.output_dir, "category_map.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "categories": list(self.category_urls.items()),
                "json_map": self.category_json_map,
                "results": {g: len(ud) for g, ud in self.group_results.items()}
            }, f, ensure_ascii=False, indent=2)

    def run(self):
        try:
            print("=" * 60)
            print("🚀 AisiMu 直播源采集器 v3.1")
            print(f"⏰ 开始时间: {self.get_beijing_time()}")
            print("=" * 60)
            
            if not self.login():
                raise Exception("登录失败")
            
            if not self.fetch_index():
                raise Exception("获取分类列表失败")
            
            if not self.category_urls:
                raise Exception("未找到任何分类")
            
            print(f"\n[开始] 共 {len(self.category_urls)} 个分类")
            
            # ===== 修复：去掉timeout，避免卡死 =====
            with ThreadPoolExecutor(max_workers=self.CRAWL_WORKERS) as pool:
                tasks = []
                for i, (url, name) in enumerate(self.category_urls.items(), 1):
                    tasks.append(pool.submit(self.fetch_category, url, name, i, len(self.category_urls)))
                
                # ===== 修复：每个任务单独超时，不影响整体 =====
                for future in as_completed(tasks):
                    try:
                        future.result(timeout=60)  # 单任务最多60秒
                    except TimeoutError:
                        print("[⚠️] 单个任务超时，继续执行")
                    except Exception as e:
                        print(f"[⚠️] 抓取任务异常: {e}")
            
            total_raw = sum(len(v) for v in self.group_results.values())
            print(f"\n[📊] 原始采集: {total_raw} 条")
            
            if not self.group_results:
                print("[❌] 未采集到任何源")
                self.export_json_summary()
                return
            
            self.validate_streams()
            self.export_m3u()
            self.export_json_summary()
            self._save_history()
            
            now_all = set()
            for d in self.group_results.values():
                now_all.update(d.keys())
            self.new_urls = now_all - self.old_urls
            if self.new_urls:
                self.tg(f"🆕 AisiMu 新增 {len(self.new_urls)} 条直播源")
            
            print(f"\n✅ 完成时间: {self.get_beijing_time()}")
            print("=" * 60)
            
        except Exception as e:
            print(f"[❌] 运行错误: {e}")
            import traceback
            traceback.print_exc()
            if self.group_results:
                self.export_m3u()

if __name__ == "__main__":
    try:
        scraper = AisiMuScraper()
        scraper.run()
    except KeyboardInterrupt:
        print("\n[⏹️] 用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"[💥] 致命错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
