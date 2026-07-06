# coding=utf-8
import requests
import time
import os
import sys
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, unquote
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
        self.session.headers.update({
            "User-Agent": self.cfg["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        })
        
        adapter = requests.adapters.HTTPAdapter(max_retries=3, pool_connections=30, pool_maxsize=30)
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
        self.CRAWL_WORKERS = 3      # 降低并发
        self.CHECK_WORKERS = 10
        self.SLEEP_INTERVAL = 0.5
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
                resp = self.session.get(self.cfg["login_url"], timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
                
                payload = {
                    self.cfg["username_field"]: self.cfg["username"],
                    self.cfg["password_field"]: self.cfg["password"]
                }
                
                # 提取所有隐藏字段
                for inp in soup.find_all("input", {"type": "hidden"}):
                    name = inp.get("name")
                    value = inp.get("value", "")
                    if name:
                        payload[name] = value
                
                # 提取 form action
                form = soup.find("form")
                if form and form.get("action"):
                    action_url = urljoin(self.cfg["login_url"], form.get("action"))
                else:
                    action_url = self.cfg["login_url"]

                post_resp = self.session.post(action_url, data=payload,
                                              allow_redirects=True, timeout=15)
                
                # 检查登录状态
                if self.cfg["login_failed_check_text"] in post_resp.text:
                    print("[❌] 登录失败")
                    return False
                    
                # 检查cookie是否有效
                if len(self.session.cookies) > 0:
                    print(f"[✅] 登录成功，Session cookies: {len(self.session.cookies)}")
                    return True
                    
            except Exception as e:
                print(f"[⚠️] 登录重试 {retry+1}/5: {e}")
                time.sleep(3)
        return False

    def fetch_index(self):
        """获取所有分类链接 - 全面增强版"""
        try:
            r = self.session.get(self.cfg["logged_in_expected_url"], timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            
            print(f"[调试] 页面标题: {soup.title.string.strip() if soup.title else '无标题'}")
            
            # 策略1: 遍历所有链接
            for a in soup.find_all('a', href=True):
                href = a['href']
                text = a.text.strip()
                
                # 匹配各种可能的分类链接模式
                patterns = [
                    'zblist', 'list', 'category', 'cate', 
                    'type', 'class', 'sort', 'channel'
                ]
                
                if any(p in href.lower() for p in patterns):
                    full_url = urljoin(self.cfg["logged_in_expected_url"], href)
                    if full_url not in self.category_urls:
                        name = text or f"分类_{len(self.category_urls)}"
                        self.category_urls[full_url] = name
            
            # 策略2: 查找iframe中的分类列表
            for iframe in soup.find_all("iframe"):
                src = iframe.get("src")
                if src and any(p in src for p in ['zblist', 'list', 'category']):
                    full_url = urljoin(self.cfg["logged_in_expected_url"], src)
                    self.category_urls[full_url] = f"iframe_{len(self.category_urls)}"
            
            # 策略3: 查找AJAX接口
            scripts = soup.find_all("script")
            for script in scripts:
                if script.string:
                    # 查找可能的API接口
                    api_matches = re.findall(r'url\s*[:=]\s*["\']([^"\']+\.php[^"\']*)["\']', script.string)
                    for api in api_matches:
                        if any(p in api for p in ['zblist', 'list', 'getlist']):
                            full_url = urljoin(self.cfg["logged_in_expected_url"], api)
                            self.category_urls[full_url] = f"api_{len(self.category_urls)}"
            
            print(f"[✅] 发现 {len(self.category_urls)} 个分类链接")
            
            # 调试输出
            for idx, (url, name) in enumerate(self.category_urls.items(), 1):
                print(f"  {idx}. {name} -> {url}")
            
            return len(self.category_urls) > 0
            
        except Exception as e:
            print(f"[❌] 获取分类列表失败: {e}")
            return False

    def extract_streams_from_page(self, html, page_url):
        """真正提取直播源 - 全面重构版"""
        soup = BeautifulSoup(html, "html.parser")
        streams = {}
        
        # ========== 1. 从 iframe 中提取 ==========
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src")
            if src:
                # 尝试解码参数中的URL
                decoded = unquote(src)
                urls_found = re.findall(r'https?://[^\s\'"]+\.(m3u8|flv|mp4)[^\s\'"]*', decoded)
                for u in urls_found:
                    streams[u] = "iframe解码源"
                
                # 直接加入iframe src
                full_url = urljoin(page_url, src)
                if any(x in full_url for x in ['.m3u8', '.flv', '.mp4', 'play']):
                    streams[full_url] = "iframe源"
        
        # ========== 2. 从 video 标签提取 ==========
        for video in soup.find_all(['video', 'source']):
            src = video.get('src')
            if src:
                full_url = urljoin(page_url, src)
                streams[full_url] = video.get('title', 'video源')
            
            # data-src 属性
            data_src = video.get('data-src')
            if data_src:
                full_url = urljoin(page_url, data_src)
                streams[full_url] = "data-src源"
        
        # ========== 3. 从 data-* 属性提取 ==========
        for tag in soup.find_all(True):
            for attr_name, attr_value in tag.attrs.items():
                if 'data' in attr_name.lower() and isinstance(attr_value, str):
                    if 'http' in attr_value and any(x in attr_value for x in ['.m3u8', '.flv', '.mp4']):
                        streams[attr_value] = f"data属性_{tag.name}"
        
        # ========== 4. 从 script 标签提取（核心） ==========
        for script in soup.find_all("script"):
            if not script.string:
                continue
            text = script.string
            
            # 提取 playUrl / videoUrl / streamUrl
            patterns = [
                r'(playUrl|videoUrl|streamUrl|liveUrl|rtmpUrl|hlsUrl)\s*[:=]\s*["\']([^"\']+)["\']',
                r'(url|src|link|path)\s*[:=]\s*["\']([^"\']+\.(m3u8|flv|mp4)[^"\']*)["\']',
                r'["\'](https?://[^\s"\']+\.(m3u8|flv|mp4)[^\s"\']*)["\']',
                r'(https?://[^\s;]+\.(m3u8|flv|mp4)[^\s;]*)'
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    # 处理不同的匹配格式
                    if isinstance(match, tuple):
                        url = match[-1] if len(match) > 1 else match[0]
                    else:
                        url = match
                    
                    # 清理URL
                    url = url.strip(' "\'')
                    if url.startswith(('http://', 'https://')):
                        # 解码URL
                        decoded_url = unquote(url)
                        streams[decoded_url] = "script解析源"
        
        # ========== 5. 从 table 表格提取 ==========
        for tr in soup.select("table tr"):
            tds = tr.find_all("td")
            if len(tds) >= 3:
                # 尝试找到包含URL的列
                for td in tds:
                    text = td.get_text(strip=True)
                    urls = re.findall(r'https?://[^\s<>"\']+\.(m3u8|flv|mp4)[^\s<>"\']*', text)
                    for url in urls:
                        # 获取房间名（通常在相邻列）
                        room_name = tds[0].get_text(strip=True) if len(tds) > 0 else "未知"
                        streams[url] = room_name
        
        # ========== 6. 从 a 标签提取 ==========
        for a in soup.find_all('a', href=True):
            href = a['href']
            if any(x in href for x in ['.m3u8', '.flv', '.mp4', 'play', 'stream']):
                full_url = urljoin(page_url, href)
                streams[full_url] = a.text.strip() or "链接源"
        
        # ========== 7. 从隐藏的 input 提取 ==========
        for inp in soup.find_all('input', {'type': 'hidden'}):
            value = inp.get('value', '')
            if 'http' in value and any(x in value for x in ['.m3u8', '.flv', '.mp4']):
                streams[value] = "hidden输入源"
        
        # ========== 8. 从 meta 标签提取 ==========
        meta = soup.find('meta', attrs={'http-equiv': 'refresh'})
        if meta and meta.get('content'):
            content = meta.get('content')
            url_match = re.search(r'url=([^;]+)', content, re.IGNORECASE)
            if url_match:
                url = url_match.group(1)
                if 'http' in url:
                    streams[url] = "meta刷新源"
        
        return streams

    def fetch_category(self, url, cname, idx, total):
        """抓取单个分类，支持翻页"""
        try:
            print(f"[抓取] {idx}/{total} {cname}")
            time.sleep(self.SLEEP_INTERVAL)
            
            # 尝试获取第一页
            r = self.session.get(url, timeout=20)
            if r.status_code != 200:
                print(f"[⚠️] {cname} 状态码 {r.status_code}")
                return False
            
            # 检查是否需要重定向到播放页
            if 'Location' in r.headers:
                redirect_url = r.headers['Location']
                if 'http' in redirect_url:
                    r = self.session.get(redirect_url, timeout=20)
            
            # 提取流地址
            streams = self.extract_streams_from_page(r.text, url)
            
            # 如果当前页没有，尝试查找分页
            if len(streams) == 0:
                soup = BeautifulSoup(r.text, "html.parser")
                # 查找下一页链接
                next_page = None
                for a in soup.find_all('a', href=True):
                    if any(p in a.text for p in ['下一页', '>', 'next']):
                        next_page = a['href']
                        break
                
                if next_page:
                    next_url = urljoin(url, next_page)
                    print(f"[翻页] {cname} -> {next_url}")
                    r2 = self.session.get(next_url, timeout=20)
                    streams = self.extract_streams_from_page(r2.text, next_url)
            
            # 保存结果
            count = 0
            for stream_url, room in streams.items():
                # 过滤无效URL
                if stream_url and stream_url.startswith(('http://', 'https://')):
                    # 去重
                    if stream_url not in self.group_results[cname]:
                        self.group_results[cname][stream_url] = room
                        count += 1
            
            print(f"[完成] {idx}/{total} {cname} 采集到 {count} 条")
            return True
            
        except Exception as e:
            print(f"[⚠️] {cname} 异常: {e}")
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
        """检查流是否可用"""
        try:
            # HEAD 请求
            resp = self.session.head(url, timeout=(3, self.PLAY_CHECK_TIMEOUT), allow_redirects=True)
            if resp.status_code in (200, 301, 302, 304, 403):
                resp.close()
                return url, True
            resp.close()
        except:
            pass
        
        try:
            # GET 请求
            resp = self.session.get(url, timeout=(3, self.PLAY_CHECK_TIMEOUT), stream=True)
            # 读取少量数据
            for chunk in resp.iter_content(chunk_size=1024):
                resp.close()
                return url, True
            resp.close()
        except:
            pass
        
        return url, False

    def validate_streams(self):
        """验证所有流"""
        print("[🚀] 开始验证流...")
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
        
        self.group_results = valid
        total_valid = sum(len(v) for v in valid.values())
        print(f"[✅] 验证完成，有效: {total_valid} 条")

    def export_m3u(self):
        """导出M3U文件"""
        now_time = self.get_beijing_time()
        lines = [
            "#EXTM3U",
            f"# 更新时间: {now_time}",
            f"# 总分类: {len(self.group_results)}",
            "#TO=3000",
            "#IJKAD=300",
            ""
        ]
        
        cnt = 0
        for g, ud in sorted(self.group_results.items()):
            # 按房间名排序
            items = sorted(ud.items(), key=lambda x: x[1] or "未知")
            lines.append(f"# 分类: {g} ({len(items)}个源)")
            
            for u, n in items[:self.MAX_KEEP_PER_GROUP]:
                name = n if n and n.strip() else "未知频道"
                lines.append(f'#EXTINF:-1 group-title="{g}",{name}')
                lines.append(u)
                cnt += 1
            
            lines.append("")
        
        path = os.path.join(self.output_dir, "aisimu.m3u")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        
        print(f"[✅] M3U生成: {path}")
        print(f"[📊] 总计 {cnt} 条有效源，{len(self.group_results)} 个分类")
        return path

    def run(self):
        """主运行函数"""
        try:
            print("=" * 60)
            print("🚀 AisiMu 直播源采集器 v2.0")
            print(f"⏰ 开始时间: {self.get_beijing_time()}")
            print("=" * 60)
            
            # 登录
            if not self.login():
                raise Exception("登录失败")
            
            # 获取分类
            if not self.fetch_index():
                raise Exception("获取分类列表失败")
            
            if not self.category_urls:
                raise Exception("未找到任何分类")
            
            # 抓取所有分类
            print(f"\n[开始] 共 {len(self.category_urls)} 个分类")
            
            with ThreadPoolExecutor(max_workers=self.CRAWL_WORKERS) as pool:
                tasks = []
                for i, (url, name) in enumerate(self.category_urls.items(), 1):
                    tasks.append(pool.submit(self.fetch_category, url, name, i, len(self.category_urls)))
                
                for future in as_completed(tasks, timeout=600):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[⚠️] 抓取任务异常: {e}")
            
            # 统计结果
            total_raw = sum(len(v) for v in self.group_results.values())
            print(f"\n[📊] 原始采集: {total_raw} 条")
            
            if not self.group_results:
                print("[❌] 未采集到任何源")
                return
            
            # 验证流
            self.validate_streams()
            
            # 导出
            self.export_m3u()
            self._save_history()
            
            # 推送通知
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
            # 出错时也尝试导出已有数据
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
        sys.exit(1)
