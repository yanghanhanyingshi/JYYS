#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源采集器（多源合并 + 固定分类排序 + 央视数字排序 + 测速）
- 支持多个采集源，自动合并去重
- 输出分类固定顺序：央视 → 卫视 → 卡通动漫 → 香港台 → 其他频道
- 央视分类内按频道名中的数字顺序排列（CCTV1, CCTV2, ...）
- 其他分类可按速度或名称排序
- 保留“更新时间”频道（灵鹿整合分类）
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

# ==================== 配置区域 ====================
# 多源采集列表（按顺序依次采集，合并去重）
SOURCE_URLS = [
    "https://wget.la/https://github.com/yanghanhanyingshi/GYYS/blob/main/best_sorted.m3u",
    "https://proxy.api.030101.xyz/kuyun.814555752.workers.dev",
    "https://proxy.api.030101.xyz/linglu.814555752.workers.dev",
    "https://kuyutv.ccwu.cc",
    "https://lingluzhibo.ccwu.cc",
    # 可以添加更多源，例如：
    # "https://另一个代理地址/live.txt",
]

OUTPUT_FILE = "live_sources.m3u"
OUTPUT_FILE_TXT = "live_sources.txt"  # 新增 TXT 输出
BACKUP_FILE = "live_sources.m3u.bak"
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
RETRY_TIMES = 2

# 测速配置
SPEED_TEST_ENABLE = True          # 是否启用测速
SPEED_TIMEOUT = 5                 # 单个源测速超时（秒）
MAX_WORKERS = 10                  # 并发测速线程数
SPEED_KEEP_RATIO = 0.8            # 保留速度前80%的源
SORT_BY_SPEED = True              # True: 非央视分类按速度排序（快→慢）；False: 按频道名排序

# 未匹配频道处理：True=保留并归入“其他频道”，False=丢弃
KEEP_UNMATCHED = True

# 更新时间频道配置
UPDATE_CHANNEL_NAME = "更新时间"
UPDATE_CHANNEL_URL = "https://d.kstore.dev/download/7547/20260401003530.mp4"
INFO_GROUP_TITLE = "灵鹿整合"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 分类关键词（匹配规则）
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
# 固定分类顺序（输出时严格按照此顺序）
CATEGORY_ORDER = ["央视", "卫视", "卡通动漫", "香港台"]
# “其他频道”将放在最后

def get_beijing_time() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d %H:%M")

def classify_channel(name: str) -> str:
    """返回分类名，未匹配时根据 KEEP_UNMATCHED 返回 '其他频道' 或 None"""
    name_lower = name.lower()
    for group, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw.lower() in name_lower:
                return group
    if KEEP_UNMATCHED:
        return "其他频道"
    else:
        return None

def extract_cctv_number(channel_name: str) -> int:
    """
    从央视频道名中提取数字，用于排序。
    例如：CCTV1 -> 1, CCTV5+ -> 5, 中央一台 -> 1, 中央十五台 -> 15
    无法提取的返回 9999（排在最后）
    """
    name = channel_name.lower()
    # 匹配 "cctv" 后的数字（可能带加号或减号）
    m = re.search(r'cctv[-]?(\d+)', name)
    if m:
        return int(m.group(1))
    # 匹配 "中央" 后的数字（中央一台 -> 1）
    m = re.search(r'中央[一二三四五六七八九十\d]+台', name)
    if m:
        # 简单处理：提取数字或中文数字
        text = m.group()
        # 中文数字映射
        chinese_num = {'一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}
        for ch, num in chinese_num.items():
            if ch in text:
                return num
        # 直接数字
        num_match = re.search(r'(\d+)', text)
        if num_match:
            return int(num_match.group(1))
    # 匹配 "cctv" 后无数字的（如 cctv）返回 0
    if 'cctv' in name and not re.search(r'cctv\s*\d', name):
        return 0
    return 9999

def fetch_single_source(url: str) -> Optional[str]:
    """从单个源获取原始数据"""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(RETRY_TIMES + 1):
        try:
            logger.info(f"请求源: {url} (尝试 {attempt+1}/{RETRY_TIMES+1})")
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
            content = resp.text.strip()
            if content:
                logger.info(f"成功，数据长度 {len(content)} 字符")
                return content
            logger.warning("返回内容为空")
        except Exception as e:
            logger.warning(f"请求失败: {e}")
    return None

def parse_content(content: str) -> List[Tuple[str, str]]:
    """自动识别格式并解析"""
    channels = []
    if content.strip().startswith('#EXTM3U'):
        channels = parse_m3u(content)
    if not channels and (content.strip().startswith('{') or content.strip().startswith('[')):
        channels = parse_json(content)
    if not channels:
        channels = parse_generic(content)
    return channels

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
            except Exception:
                pass
    valid = [(url_to_name[url], url) for url in results if url in results]
    if not valid:
        logger.warning("测速后无有效源，保留全部原始源")
        return channels
    valid_sorted = sorted(valid, key=lambda x: results[x[1]])
    keep_count = max(1, int(len(valid_sorted) * SPEED_KEEP_RATIO))
    kept = valid_sorted[:keep_count]
    logger.info(f"有效 {len(valid)} 个，保留最快 {keep_count} 个 ({SPEED_KEEP_RATIO*100:.0f}%)")
    return kept

def generate_m3u(channels: List[Tuple[str, str]], update_time: str) -> str:
    """
    生成M3U，分类顺序固定：
    央视 → 卫视 → 卡通动漫 → 香港台 → 其他频道（如果存在） → 灵鹿整合（更新时间）
    央视分类内按数字顺序排序（CCTV1, CCTV2, ...）
    其他分类：如果 SORT_BY_SPEED=True 且测速启用，则保持速度顺序（传入列表已按速度排序）；
             否则按频道名排序。
    """
    # 先对每个频道分类
    classified = []
    for name, url in channels:
        cat = classify_channel(name)
        if cat is not None:
            classified.append((name, url, cat))
        else:
            logger.debug(f"丢弃未匹配频道: {name}")
    
    # 分组（保持传入顺序，这样非央视分类若已按速度排序则顺序保留）
    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for name, url, cat in classified:
        grouped.setdefault(cat, []).append((name, url))
    
    # 对央视分类进行数字排序
    if "央视" in grouped:
        grouped["央视"].sort(key=lambda x: extract_cctv_number(x[0]))
        logger.info("央视频道已按数字顺序排序")
    
    # 对其他分类，若未启用速度排序或未测速，则按名称排序
    if not SORT_BY_SPEED or not SPEED_TEST_ENABLE:
        for cat, chs in grouped.items():
            if cat != "央视":  # 央视已经排过序了
                chs.sort(key=lambda x: x[0])
    
    lines = ["#EXTM3U", ""]
    # 按固定顺序输出分类
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            lines.append(f"# 分类: {cat} ({len(grouped[cat])}个频道)")
            for name, url in grouped[cat]:
                safe_name = name.replace(',', ' ').strip()
                lines.append(f'#EXTINF:-1 group-title="{cat}",{safe_name}')
                lines.append(url)
            lines.append("")
    # 输出其他分类（比如“其他频道”或其他动态分类）
    for cat, chs in grouped.items():
        if cat not in CATEGORY_ORDER and cat != INFO_GROUP_TITLE:
            lines.append(f"# 分类: {cat} ({len(chs)}个频道)")
            for name, url in chs:
                safe_name = name.replace(',', ' ').strip()
                lines.append(f'#EXTINF:-1 group-title="{cat}",{safe_name}')
                lines.append(url)
            lines.append("")
    # 最后添加更新时间频道
    lines.append(f"# 分类: {INFO_GROUP_TITLE} (1个频道)")
    lines.append(f'#EXTINF:-1 group-title="{INFO_GROUP_TITLE}",{UPDATE_CHANNEL_NAME}')
    lines.append(UPDATE_CHANNEL_URL)
    lines.append("")
    return "\n".join(lines)

# 新增：生成 TXT 格式内容（频道名,链接）
def generate_txt(channels: List[Tuple[str, str]]) -> str:
    lines = []
    for name, url in channels:
        lines.append(f"{name},{url}")
    return "\n".join(lines)

def main():
    logger.info("=== 直播源采集器启动（多源合并 + 央视数字排序）===")
    beijing_time = get_beijing_time()
    logger.info(f"北京时间: {beijing_time}")

    all_channels = []
    # 依次采集每个源
    for url in SOURCE_URLS:
        raw = fetch_single_source(url)
        if raw:
            channels = parse_content(raw)
            logger.info(f"从 {url} 解析到 {len(channels)} 个频道")
            all_channels.extend(channels)
        else:
            logger.warning(f"源 {url} 采集失败")
    
    # 如果所有源都失败，尝试从备份恢复
    if not all_channels:
        logger.warning("所有源均未采集到频道，尝试使用上次备份")
        backup_m3u = load_backup()
        if backup_m3u:
            url_pat = re.compile(r'(https?://[^\s<>"\'()]+)')
            name_pat = re.compile(r'#EXTINF:.*?,([^\n]+)')
            names = name_pat.findall(backup_m3u)
            urls = url_pat.findall(backup_m3u)
            min_len = min(len(names), len(urls))
            all_channels = [(names[i].strip(), urls[i]) for i in range(min_len)]
            logger.info(f"从备份恢复 {len(all_channels)} 个频道")
        else:
            logger.error("没有可用备份")
            sys.exit(1)
    
    # 去重（基于URL）
    seen = set()
    unique = []
    for name, url in all_channels:
        if url not in seen:
            seen.add(url)
            unique.append((name, url))
    logger.info(f"去重后剩余 {len(unique)} 个频道")
    
    # 测速（返回按速度排序后的列表）
    if unique and SPEED_TEST_ENABLE:
        unique = filter_by_speed(unique)
        # filter_by_speed 返回的列表已经是按速度从快到慢排序
    elif not SORT_BY_SPEED:
        # 如果不按速度排序，按频道名称排序（但央视后续会再按数字排序，所以整体排序也可）
        unique.sort(key=lambda x: x[0])
    
    # 生成 M3U
    m3u_content = generate_m3u(unique, beijing_time)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(m3u_content)
    logger.info(f"已写入 {OUTPUT_FILE}")

    # 生成 TXT 【新增】
    txt_content = generate_txt(unique)
    with open(OUTPUT_FILE_TXT, 'w', encoding='utf-8') as f:
        f.write(txt_content)
    logger.info(f"已写入 {OUTPUT_FILE_TXT}")
    
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
