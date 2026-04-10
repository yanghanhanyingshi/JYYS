#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源采集器（带测速 + 更新时间频道）
- 自动采集、分类、去重、测速
- 删除“其他频道”分类（未匹配的频道不保留）
- 增加一个固定的“更新时间”频道，放在“灵鹿整合”分类下
"""

import re
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional, Dict
import logging
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ==================== 配置 ====================
SOURCE_URL = "https://proxy.api.030101.xyz/kuyun.814555752.workers.dev/"
OUTPUT_FILE = "live_sources.m3u"
BACKUP_FILE = "live_sources.m3u.bak"
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
RETRY_TIMES = 2

# 测速配置
SPEED_TEST_ENABLE = True
SPEED_TIMEOUT = 5
MAX_WORKERS = 10
SPEED_KEEP_RATIO = 0.8

# 更新时间频道配置（固定显示）
UPDATE_CHANNEL_NAME = "更新时间"
UPDATE_CHANNEL_URL = "https://d.kstore.dev/download/7547/20260401003530.mp4"
INFO_GROUP_TITLE = "灵鹿整合"   # 放置更新频道的分类名

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 分类关键词（不含“其他频道”）
CATEGORY_RULES = {
    "央视": ["cctv", "中央", "央视", "CCTV", "CCTV-", "中央一台", "中央二台", "中央三台", "中央四台", "中央五台",
             "中央六台", "中央七台", "中央八台", "中央九台", "中央十台", "中央十一台", "中央十二台", "中央十三台",
             "中央十四台", "中央十五台", "中央少儿", "中央新闻", "中央体育", "中央综艺", "CCTV1", "CCTV2", "CCTV3",
             "CCTV4", "CCTV5", "CCTV6", "CCTV7", "CCTV8", "CCTV9", "CCTV10", "CCTV11", "CCTV12", "CCTV13", "CCTV14", "CCTV15"],
    "卫视": ["卫视", "湖南卫视", "浙江卫视", "江苏卫视", "东方卫视", "北京卫视", "深圳卫视", "广东卫视", "天津卫视",
             "山东卫视", "安徽卫视", "辽宁卫视", "河南卫视", "湖北卫视", "江西卫视", "四川卫视", "重庆卫视",
             "黑龙江卫视", "贵州卫视", "云南卫视", "广西卫视", "陕西卫视", "甘肃卫视", "新疆卫视", "东南卫视",
             "凤凰卫视", "海峡卫视", "厦门卫视"],
    "卡通动漫": ["卡通", "动漫", "少儿", "动画", "儿童", "kids", "cartoon", "anime", "金鹰卡通", "卡酷少儿",
                 "炫动卡通", "优漫卡通", "CCTV-少儿", "CCTV少儿", "少儿频道", "动漫秀场", "新动漫"],
    "香港台": ["香港", "TVB", "翡翠台", "明珠台", "凤凰香港", "港台", "无线", "星河频道", "TVB8", "TVB星河",
               "香港开电视", "香港国际", "香港卫视", "now新闻", "有线新闻", "RTHK"]
}
# 不再使用 DEFAULT_GROUP，未匹配的频道将被丢弃

def get_beijing_time() -> str:
    """返回北京时间字符串（仅用于日志）"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d %H:%M")

def classify_channel(name: str) -> Optional[str]:
    """
    根据频道名称返回分类名，若未匹配任何关键词则返回 None（表示丢弃）
    """
    name_lower = name.lower()
    for group, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw.lower() in name_lower:
                return group
    return None   # 未匹配，丢弃

def fetch_data() -> Optional[str]:
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(RETRY_TIMES + 1):
        try:
            logger.info(f"请求数据 (尝试 {attempt+1}/{RETRY_TIMES+1}): {SOURCE_URL}")
            resp = requests.get(SOURCE_URL, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
            content = resp.text.strip()
            if content:
                logger.info(f"获取成功，数据长度 {len(content)} 字符")
                return content
            logger.warning("返回内容为空")
        except Exception as e:
            logger.warning(f"请求失败: {e}")
    return None

def parse_m3u(content: str) -> List[Tuple[str, str]]:
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF'):
            name = ""
            if ',' in line:
                name = line.split(',')[-1].strip()
            else:
                m = re.search(r'tvg-name="([^"]+)"', line)
                if m:
                    name = m.group(1)
            if i+1 < len(lines):
                url = lines[i+1].strip()
                if url.startswith(('http://', 'https://')):
                    if not name:
                        name = url.split('/')[-1].split('.')[0] or "未知频道"
                    channels.append((name, url))
            i += 1
        i += 1
    return channels

def parse_json(content: str) -> List[Tuple[str, str]]:
    channels = []
    try:
        data = json.loads(content)
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ['data', 'list', 'channels', 'result', 'items']:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
        for item in items:
            if not isinstance(item, dict):
                continue
            name = None
            for nk in ['name', 'title', 'channel_name', 'cn']:
                if nk in item:
                    name = item[nk]
                    break
            url = None
            for uk in ['url', 'stream', 'play_url', 'link']:
                if uk in item:
                    url = item[uk]
                    break
            if name and url and isinstance(url, str) and url.startswith(('http://', 'https://')):
                channels.append((str(name).strip(), url))
    except:
        pass
    return channels

def parse_generic(content: str) -> List[Tuple[str, str]]:
    channels = []
    url_pattern = re.compile(r'(https?://[^\s<>"\'()]+)')
    lines = content.splitlines()
    for i, line in enumerate(lines):
        urls = url_pattern.findall(line)
        for url in urls:
            name = ""
            name_candidate = re.sub(r'https?://[^\s]+', '', line).strip()
            if name_candidate and len(name_candidate) < 50:
                name = name_candidate.strip('# ,')
            if not name and i > 0:
                prev = lines[i-1].strip()
                if prev and not prev.startswith('http') and len(prev) < 50:
                    name = prev.strip('# ,')
            if not name:
                name = url.split('/')[-1].split('.')[0] or "未知频道"
            if not any(url == u for _, u in channels):
                channels.append((name, url))
    return channels

def load_backup() -> Optional[str]:
    if os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, 'r', encoding='utf-8') as f:
                return f.read()
        except:
            pass
    return None

def save_backup(content: str):
    try:
        with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info("备份已保存")
    except Exception as e:
        logger.warning(f"备份失败: {e}")

def speed_test_single(url: str) -> Tuple[str, Optional[float]]:
    headers = {"User-Agent": USER_AGENT}
    start = time.time()
    try:
        resp = requests.head(url, headers=headers, timeout=SPEED_TIMEOUT, allow_redirects=True)
        if resp.status_code < 400:
            elapsed = (time.time() - start) * 1000
            return url, elapsed
        resp = requests.get(url, headers=headers, timeout=SPEED_TIMEOUT, stream=True)
        if resp.status_code < 400:
            for _ in resp.iter_content(1024):
                break
            elapsed = (time.time() - start) * 1000
            return url, elapsed
    except Exception:
        pass
    return url, None

def filter_by_speed(channels: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    if not SPEED_TEST_ENABLE:
        logger.info("测速已禁用，保留全部源")
        return channels

    logger.info(f"开始测速，共 {len(channels)} 个源，并发数 {MAX_WORKERS}")
    url_to_name = {url: name for name, url in channels}
    results: Dict[str, float] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(speed_test_single, url): url for _, url in channels}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                _, elapsed = future.result()
                if elapsed is not None:
                    results[url] = elapsed
                    logger.debug(f"✓ {url}  {elapsed:.0f}ms")
                else:
                    logger.debug(f"✗ {url} 超时/失败")
            except Exception:
                logger.debug(f"✗ {url} 异常")

    valid = [(url_to_name[url], url) for url in results if url in results]
    if not valid:
        logger.warning("测速后无有效源，将保留全部原始源")
        return channels

    valid_sorted = sorted(valid, key=lambda x: results[x[1]])
    keep_count = max(1, int(len(valid_sorted) * SPEED_KEEP_RATIO))
    kept = valid_sorted[:keep_count]
    logger.info(f"测速完成：有效 {len(valid)} 个，保留速度最快的 {keep_count} 个（{SPEED_KEEP_RATIO*100:.0f}%）")
    return kept

def generate_m3u(channels: List[Tuple[str, str]], update_time: str) -> str:
    """
    生成 M3U 文件：
    - 只输出有分类的频道（丢弃 classify_channel 返回 None 的频道）
    - 最后固定添加“更新时间”频道，放在“灵鹿整合”分类下
    - 不再输出文件末尾的时间注释
    """
    # 先过滤掉未匹配分类的频道
    filtered = []
    for name, url in channels:
        cat = classify_channel(name)
        if cat is not None:
            filtered.append((name, url, cat))
        else:
            logger.debug(f"丢弃未匹配频道: {name}")

    # 按分类分组
    grouped = {}
    for name, url, cat in filtered:
        grouped.setdefault(cat, []).append((name, url))

    lines = ["#EXTM3U", ""]
    order = ["央视", "卫视", "卡通动漫", "香港台"]
    for cat in order:
        if cat in grouped:
            lines.append(f"# 分类: {cat} ({len(grouped[cat])}个频道)")
            for name, url in grouped[cat]:
                safe_name = name.replace(',', ' ').strip()
                lines.append(f'#EXTINF:-1 group-title="{cat}",{safe_name}')
                lines.append(url)
            lines.append("")
    # 输出其他分类（不在 order 中的）
    for cat, chs in grouped.items():
        if cat not in order:
            lines.append(f"# 分类: {cat} ({len(chs)}个频道)")
            for name, url in chs:
                safe_name = name.replace(',', ' ').strip()
                lines.append(f'#EXTINF:-1 group-title="{cat}",{safe_name}')
                lines.append(url)
            lines.append("")

    # 固定添加更新时间频道（放在“灵鹿整合”分类下）
    lines.append(f"# 分类: {INFO_GROUP_TITLE} (1个频道)")
    lines.append(f'#EXTINF:-1 group-title="{INFO_GROUP_TITLE}",{UPDATE_CHANNEL_NAME}')
    lines.append(UPDATE_CHANNEL_URL)
    lines.append("")

    return "\n".join(lines)

def main():
    logger.info("=== 直播源采集器启动（带测速 + 更新时间频道）===")
    beijing_time = get_beijing_time()
    logger.info(f"北京时间: {beijing_time}")

    raw = fetch_data()
    channels = []
    if raw:
        if raw.strip().startswith('#EXTM3U'):
            channels = parse_m3u(raw)
        if not channels and (raw.strip().startswith('{') or raw.strip().startswith('[')):
            channels = parse_json(raw)
        if not channels:
            channels = parse_generic(raw)
        logger.info(f"解析到 {len(channels)} 个频道")
    else:
        logger.error("获取原始数据失败")

    # 若本次无频道，尝试从备份恢复（但备份中可能包含其他频道分类，恢复后也会被过滤）
    if not channels:
        logger.warning("当前采集频道数为0，尝试使用上次备份")
        backup_m3u = load_backup()
        if backup_m3u:
            url_pat = re.compile(r'(https?://[^\s<>"\'()]+)')
            name_pat = re.compile(r'#EXTINF:.*?,([^\n]+)')
            names = name_pat.findall(backup_m3u)
            urls = url_pat.findall(backup_m3u)
            min_len = min(len(names), len(urls))
            channels = [(names[i].strip(), urls[i]) for i in range(min_len)]
            logger.info(f"从备份恢复 {len(channels)} 个频道")
        else:
            logger.error("没有可用备份，将生成空列表")

    # 去重
    seen = set()
    unique = []
    for name, url in channels:
        if url not in seen:
            seen.add(url)
            unique.append((name, url))
    logger.info(f"去重后 {len(unique)} 个频道")

    # 测速过滤
    if unique and SPEED_TEST_ENABLE:
        unique = filter_by_speed(unique)

    # 生成 M3U（内部会丢弃未匹配分类的频道）
    m3u_content = generate_m3u(unique, beijing_time)

    # 写入文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(m3u_content)
    logger.info(f"已写入 {OUTPUT_FILE}")

    # 备份（只备份非空且有效）
    if unique:
        save_backup(m3u_content)
    else:
        logger.warning("频道数为0，未更新备份")

    logger.info("=== 采集完成 ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"脚本异常: {e}")
        sys.exit(1)
