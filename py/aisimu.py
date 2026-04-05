# coding=utf-8
import requests
import time
import os
import sys
import re
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
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
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "logged_in_expected_url": "http://zhibo.aisimu.cn/zhubo/index.php",
            "login_failed_check_text": "账号密码错误",
            "tg_token": "",
            "tg_chat_id": ""
        }

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.cfg["user_agent"]})
        # 更长的超时，避免网络波动
        self.session.timeout = (15, 30)
        adapter = requests.adapters.HTTPAdapter(max_retries=3, pool_connections=30, pool_maxsize=30)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        self.category_urls = {}
        self.group_results = defaultdict(dict)
        self.old_urls = set()
        self.new_urls = set()

        self.output_dir = "output"
        os.makedirs(self.output_dir, exist_ok=True)

        # 关闭断点续爬（因为之前抓不到，先全量抓）
        self.crawl_cache_path = os.path.join(self.output_dir, "cache_crawled.txt")
        self.crawled_set = set()  # 暂时禁用缓存

        self.MAX_KEEP_PER_GROUP = 999
        self.PLAY_CHECK_TIMEOUT = 5
        self.CRAWL_WORKERS = 5      # 降低并发，避免被封
        self.CHECK_WORKERS = 10
        self.SLEEP_INTERVAL = 0.3
        self._load_history()

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
                # 先获取登录页，提取可能的 CSRF
                resp = self.session.get(self.cfg["login_url"], timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
                payload = {
                    self.cfg["username_field"]: self.cfg["username"],
                    self.cfg["password_field"]: self.cfg["password"]
                }
                # 查找所有隐藏字段
                for inp in soup.find_all("input", {"type": "hidden"}):
                    name = inp.get("name")
                    value = inp.get("value", "")
                    if name:
                        payload[name] = value
                        if "csrf" in name.lower():
                            print(f"[🔑] 发现 CSRF 字段: {name}")

                post_resp = self.session.post(self.cfg["login_url"], data=payload,
                                              allow_redirects=True, timeout=15)
                # 检查是否登录成功
                if self.cfg["login_failed_check_text"] in post_resp.text:
                    print("[❌] 账号密码错误或登录失败")
                    return False
                # 额外检查：登录后是否跳转到预期页面
                if self.cfg["logged_in_expected_url"] in post_resp.url:
                    print("[✅] 登录成功，当前 session cookies:", len(self.session.cookies))
                    return True
                else:
                    print(f"[⚠️] 登录后未跳转到预期页面，当前URL: {post_resp.url}")
                    # 仍然认为成功，继续
                    return True
            except Exception as e:
                print(f"[⚠️] 登录重试 {retry+1}/5: {e}")
                time.sleep(3)
        return False

    def fetch_index(self):
        """获取所有分类链接，增强匹配规则"""
        try:
            r = self.session.get(self.cfg["logged_in_expected_url"], timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            # 打印页面标题，用于调试
            title = soup.title.string.strip() if soup.title else "无标题"
            print(f"[调试] 分类列表页标题: {title}")
            
            # 多种匹配模式
            patterns = [
                'zblist.php',
                'action=zblist',
                'zhubo/zblist',
                'list.php',
                'category'
            ]
            for a in soup.find_all('a', href=True):
                href = a['href']
                for pattern in patterns:
                    if pattern in href:
                        name = a.text.strip()
                        if not name:
                            name = "未命名分类"
                        # 补全URL
                        full_url = urljoin(self.cfg["logged_in_expected_url"], href)
                        if full_url not in self.category_urls:
                            self.category_urls[full_url] = name
                        break
            print(f"[✅] 总发现分类: {len(self.category_urls)} 个")
            if len(self.category_urls) == 0:
                # 如果没找到，打印前几个链接供调试
                print("[调试] 页面中部分链接:")
                for a in soup.find_all('a', href=True)[:10]:
                    print(f"  {a.get('href')} -> {a.text.strip()[:30]}")
            return len(self.category_urls) > 0
        except Exception as e:
            print(f"[❌] 分类列表失败: {e}")
            return False

    def extract_streams_from_page(self, html, page_url):
        """从页面HTML中提取所有可能的流地址，返回字典 {url: 房间名}"""
        soup = BeautifulSoup(html, "html.parser")
        streams = {}
        
        # 策略1：原表格解析 (td[3] 是直播源)
        for tr in soup.select("table tr"):
            tds = tr.find_all("td")
            if len(tds) >= 4:
                room = tds[2].get_text(strip=True)
                raw_url = tds[3].get_text(strip=True)
                if raw_url and not raw_url.startswith(('javascript:', '#')):
                    full_url = urljoin(page_url, raw_url)
                    if full_url.startswith(('http://', 'https://')):
                        streams[full_url] = room
        
        # 策略2：查找所有包含 .m3u8 或 .flv 的链接
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '.m3u8' in href or '.flv' in href or 'play' in href:
                full_url = urljoin(page_url, href)
                if full_url.startswith(('http://', 'https://')):
                    room = a.text.strip() or "未知"
                    streams[full_url] = room
        
        # 策略3：查找 video 标签的 src
        for video in soup.find_all(['video', 'source']):
            src = video.get('src')
            if src:
                full_url = urljoin(page_url, src)
                if full_url.startswith(('http://', 'https://')):
                    room = video.get('title') or "视频流"
                    streams[full_url] = room
        
        # 策略4：正则匹配直接出现在文本中的流地址
        text = html
        url_pattern = r'https?://[^\s"\']+\.(m3u8|flv|mp4)[^\s"\']*'
        for match in re.finditer(url_pattern, text):
            url = match.group(0)
            if url not in streams:
                streams[url] = "自动提取"
        
        return streams

    def fetch_category(self, url, cname, idx, total):
        """抓取单个分类页面"""
        try:
            print(f"[抓取] {idx}/{total} {cname} -> {url}")
            time.sleep(self.SLEEP_INTERVAL)
            r = self.session.get(url, timeout=20)
            if r.status_code != 200:
                print(f"[⚠️] {cname} 返回状态码 {r.status_code}")
                return False
            
            # 调试：打印页面标题
            soup = BeautifulSoup(r.text, "html.parser")
            page_title = soup.title.string.strip() if soup.title else "无标题"
            print(f"[调试] {cname} 页面标题: {page_title}")
            
            # 检查是否包含“请先登录”等字样
            if "请先登录" in r.text or "登录过期" in r.text:
                print(f"[❌] {cname} 需要重新登录，会话失效")
                return False
            
            streams = self.extract_streams_from_page(r.text, url)
            count = len(streams)
            if count == 0:
                print(f"[⚠️] {cname} 未提取到任何流地址")
                # 打印页面前500字符供调试
                print(f"[调试] {cname} 页面前500字符:\n{r.text[:500]}")
            else:
                for stream_url, room in streams.items():
                    self.group_results[cname][stream_url] = room
                print(f"[完成] {idx}/{total} {cname} 采集到 {count} 条")
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
        print("[🚀] 开始测速...")
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

            print(f"[开始] 共 {total} 个分类，开始抓取...")
            with ThreadPoolExecutor(max_workers=self.CRAWL_WORKERS) as pool:
                tasks = []
                for i, (url, name) in enumerate(self.category_urls.items(), 1):
                    tasks.append(pool.submit(self.fetch_category, url, name, i, total))
                for future in as_completed(tasks, timeout=600):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[⚠️] 抓取子任务异常: {e}")

            if not self.group_results:
                print("[❌] 未采集到任何有效源，请检查网站是否改版或登录失效")
                sys.exit(1)

            self.validate_streams()
        except Exception as e:
            print(f"[ERROR] 运行过程中出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.group_results:
                self.export_m3u()
                self._save_history()
                nowall = set()
                for d in self.group_results.values():
                    nowall.update(d.keys())
                self.new_urls = nowall - self.old_urls
                if self.new_urls:
                    self.tg(f"🆕 新增 {len(self.new_urls)} 条源")
                print("[🎉] 脚本执行完毕（可能部分失败，但已导出已有数据）")
            else:
                print("[跳过] 没有采集到任何数据，不生成 M3U")

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
