# coding=utf-8
import requests
import time
import os
import sys
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

class AisiMuScraper:
    def __init__(self):
        self.cfg = {
            "base_url": "http://zhibo.aisimu.cn/zhubo/",
            "login_url": "http://zhibo.aisimu.cn/index.php",
            "username_field": "username",
            "password_field": "password",
            "username": "xyzvip",
            "password": "qq123456",
            "csrf_token_field": "",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            "logged_in_expected_url": "http://zhibo.aisimu.cn/zhubo/index.php",
            "login_failed_check_text": "账号密码错误",
            "tg_token": "",
            "tg_chat_id": ""
        }

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.cfg["user_agent"]})
        self.session.timeout = (10, 20)
        adapter = requests.adapters.HTTPAdapter(max_retries=3, pool_connections=30, pool_maxsize=30)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        self.category_urls = {}
        self.group_results = defaultdict(dict)
        self.old_urls = set()
        self.new_urls = set()

        self.output_dir = "output"
        os.makedirs(self.output_dir, exist_ok=True)

        self.crawl_cache_path = os.path.join(self.output_dir, "cache_crawled.txt")
        self.crawled_set = self._load_crawl_cache()

        self.MAX_KEEP_PER_GROUP = 999
        self.PLAY_CHECK_TIMEOUT = 5
        self.CRAWL_WORKERS = 8
        self.CHECK_WORKERS = 10
        self.SLEEP_INTERVAL = 0.1
        self._load_history()

    def get_beijing_time(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _load_crawl_cache(self):
        s = set()
        if os.path.exists(self.crawl_cache_path):
            with open(self.crawl_cache_path, "r", encoding="utf-8") as f:
                s = set(x.strip() for x in f if x.strip())
        print(f"[断点续爬] 已加载上次完成分类: {len(s)} 个")
        return s

    def _save_one_crawled(self, url_key):
        with open(self.crawl_cache_path, "a", encoding="utf-8") as f:
            f.write(url_key + "\n")

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
                    if name and "csrf" in name.lower():
                        payload[name] = value
                        print(f"[🔑] 发现 CSRF 字段: {name}")
                        break

                post_resp = self.session.post(self.cfg["login_url"], data=payload,
                                              allow_redirects=True, timeout=15)
                if self.cfg["login_failed_check_text"] in post_resp.text:
                    print("[❌] 账号密码错误或登录失败")
                    return False
                print("[✅] 登录成功")
                return True
            except Exception as e:
                print(f"[⚠️] 登录重试 {retry+1}/5: {e}")
                time.sleep(3)
        return False

    def fetch_index(self):
        try:
            r = self.session.get(self.cfg["logged_in_expected_url"], timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'zblist.php' in href or 'action=zblist' in href:
                    name = a.text.strip()
                    if not name:
                        name = "未命名分类"
                    url = urljoin(self.cfg["logged_in_expected_url"], href)
                    self.category_urls[url] = name
            print(f"[✅] 总发现分类: {len(self.category_urls)} 个")
            return len(self.category_urls) > 0
        except Exception as e:
            print(f"[❌] 分类列表失败: {e}")
            return False

    def fetch_category(self, url, cname, idx, total):
        try:
            url_key = url
            if url_key in self.crawled_set:
                print(f"[断点跳过] {idx}/{total} {cname}")
                return True

            time.sleep(self.SLEEP_INTERVAL)
            r = self.session.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")

            for tr in soup.select("table tr"):
                tds = tr.find_all("td")
                if len(tds) < 4:
                    continue
                room = tds[2].get_text(strip=True)
                raw_url = tds[3].get_text(strip=True)
                if not raw_url:
                    continue

                if not raw_url.startswith(('http://', 'https://')):
                    full_url = urljoin(url, raw_url)
                else:
                    full_url = raw_url

                if full_url.startswith(('http://', 'https://')):
                    self.group_results[cname][full_url] = room

            self._save_one_crawled(url_key)
            self.crawled_set.add(url_key)
            print(f"[完成] {idx}/{total} {cname} 采集到 {len(self.group_results[cname])} 条")
            return True
        except Exception as e:
            print(f"[⚠️] {cname} 抓取异常: {e}")
            return False

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

    def check_stream(self, url):
        try:
            resp = self.session.head(url, timeout=(3, self.PLAY_CHECK_TIMEOUT), allow_redirects=True)
            if resp.status_code in (200, 301, 302, 304, 403):
                resp.close()
                return url, True
            resp.close()
        except:
            pass
        try:
            resp = self.session.get(url, timeout=(3, self.PLAY_CHECK_TIMEOUT), stream=True)
            for _ in resp.iter_content(chunk_size=1024):
                resp.close()
                return url, True
            resp.close()
        except:
            pass
        return url, False

    def validate_streams(self):
        print("[🚀] 分批次测速...")
        all_urls = []
        mp = {}
        for g, ud in self.group_results.items():
            for u, n in ud.items():
                all_urls.append(u)
                mp[u] = (g, n)

        if not all_urls:
            print("[⚠️] 无链接，跳过测速")
            return

        batch_size = 200
        valid = defaultdict(dict)
        total_batches = (len(all_urls) + batch_size - 1) // batch_size

        for i in range(total_batches):
            start = i * batch_size
            end = start + batch_size
            batch_urls = all_urls[start:end]
            print(f"[测速批次] {i+1}/{total_batches}，{len(batch_urls)} 个链接")

            with ThreadPoolExecutor(max_workers=self.CHECK_WORKERS) as pool:
                futures = {pool.submit(self.check_stream, u): u for u in batch_urls}
                for future in as_completed(futures):
                    u, ok = future.result()
                    if ok and u in mp:
                        g, n = mp[u]
                        valid[g][u] = n

        self.group_results = valid
        total_valid = sum(len(v) for v in valid.values())
        print(f"[✅] 测速完成，有效源: {total_valid} 条")

    def export_m3u(self):
        now_time = self.get_beijing_time()
        lines = [
            "#EXTM3U",
            f"# 更新时间: {now_time}",
            "#TO=3000",
            "#IJKAD=300"
        ]
        cnt = 0
        for g, ud in self.group_results.items():
            items = sorted(ud.items(), key=lambda x: x[1])
            for u, n in items[:self.MAX_KEEP_PER_GROUP]:
                lines.append(f'#EXTINF:-1 group-title="{g}",{n if n else "未知频道"}')
                lines.append(u)
                cnt += 1
        path = os.path.join(self.output_dir, "aisimu.m3u")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[✅] M3U生成完毕｜更新时间: {now_time}｜共 {cnt} 条")
        return path

    def run(self):
        try:
            if not self.login():
                raise Exception("登录失败")
            if not self.fetch_index():
                raise Exception("获取分类列表失败")

            total = len(self.category_urls)
            if total == 0:
                raise Exception("未找到任何分类")

            print(f"[断点续爬] 待抓取剩余分类: {total - len(self.crawled_set)} 个")
            with ThreadPoolExecutor(max_workers=self.CRAWL_WORKERS) as pool:
                tasks = []
                for i, (url, name) in enumerate(self.category_urls.items(), 1):
                    tasks.append(pool.submit(self.fetch_category, url, name, i, total))
                for future in as_completed(tasks, timeout=600):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[⚠️] 抓取子任务异常: {e}")

            self.validate_streams()
        except Exception as e:
            print(f"[ERROR] 运行过程中出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 无论如何，只要抓到了数据就导出 M3U
            if self.group_results:
                # 如果还没测速（比如在抓取阶段就异常了），确保测速
                # 但为了效率，只在未测速时执行
                if not hasattr(self, '_validated') or not self._validated:
                    self.validate_streams()
                    self._validated = True
                self.export_m3u()
                self._save_history()
                # 统计新增
                nowall = set()
                for d in self.group_results.values():
                    nowall.update(d.keys())
                self.new_urls = nowall - self.old_urls
                if self.new_urls:
                    self.tg(f"🆕 新增 {len(self.new_urls)} 条源")
                print("[🎉] 脚本执行完毕（可能部分失败，但已导出已有数据）")
            else:
                print("[跳过] 没有采集到任何数据，不生成 M3U")
                sys.exit(1)

if __name__ == "__main__":
    try:
        import signal
        class TimeoutEx(Exception):
            pass
        def _timeout_handler(signum, frame):
            raise TimeoutEx("全局超时")
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(1800)
        AisiMuScraper().run()
    except TimeoutEx:
        print("[💥] 脚本30分钟超时，下次断点续爬")
        sys.exit(1)
    except Exception as e:
        print(f"[💥] 全局异常: {e}")
        sys.exit(1)
