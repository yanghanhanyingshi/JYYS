import requests
import re
from concurrent.futures import ThreadPoolExecutor

# ===================== 1. 配置区（仅在这里改源，其他不动）=====================
SOURCE_URLS = [
    "https://cloud.7so.top/f/Bgw1H8/%E5%A4%A7%E6%94%B9.txt",
    "https://wget.la/https://raw.githubusercontent.com/Jsnzkpg/Jsnzkpg/Jsnzkpg/Jsnzkpg1.m3u",
    "https://dsj-1312694395.cos.ap-guangzhou.myqcloud.com/dsj10.1.txt",
    "https://wget.la/https://github.com/fafa002/yf2025/blob/main/yiyifafa.txt"
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TIMEOUT = 8
# ============================================================================

# ===================== 2. 国内CDN替换（自动转jsdelivr，免翻墙）=====================
def replace_cdn(url):
    # 替换 wget.la 代理的 GitHub raw
    wget_match = re.search(r"wget\.la/https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/[^/]+/(.+)", url)
    if wget_match:
        return f"https://cdn.jsdelivr.net/gh/{wget_match.group(1)}/{wget_match.group(2)}/{wget_match.group(3)}"
    # 替换原生 GitHub raw
    raw_match = re.search(r"raw\.githubusercontent\.com/([^/]+)/([^/]+)/[^/]+/(.+)", url)
    if raw_match:
        return f"https://cdn.jsdelivr.net/gh/{raw_match.group(1)}/{raw_match.group(2)}/{raw_match.group(3)}"
    # 替换 GitHub blob 页面
    blob_match = re.search(r"github\.com/([^/]+)/([^/]+)/blob/[^/]+/(.+)", url)
    if blob_match:
        return f"https://cdn.jsdelivr.net/gh/{blob_match.group(1)}/{blob_match.group(2)}/{blob_match.group(3)}"
    return url

# ===================== 3. 频道名称标准化（央视/卫视统一格式）=====================
def standardize_name(name):
    name = name.strip().replace(" ", "")
    # 央视统一为 CCTV-1 格式
    if any(k in name.upper() for k in ["CCTV", "央视", "中央"]):
        # 数字频道映射
        cctv_map = {
            "CCTV1": "CCTV-1", "央视1": "CCTV-1", "中央1": "CCTV-1",
            "CCTV2": "CCTV-2", "央视2": "CCTV-2", "中央2": "CCTV-2",
            "CCTV3": "CCTV-3", "央视3": "CCTV-3", "中央3": "CCTV-3",
            "CCTV4": "CCTV-4", "央视4": "CCTV-4", "中央4": "CCTV-4",
            "CCTV5": "CCTV-5", "央视5": "CCTV-5", "中央5": "CCTV-5",
            "CCTV6": "CCTV-6", "央视6": "CCTV-6", "中央6": "CCTV-6",
            "CCTV7": "CCTV-7", "央视7": "CCTV-7", "中央7": "CCTV-7",
            "CCTV8": "CCTV-8", "央视8": "CCTV-8", "中央8": "CCTV-8",
            "CCTV9": "CCTV-9", "央视9": "CCTV-9", "中央9": "CCTV-9",
            "CCTV10": "CCTV-10", "央视10": "CCTV-10", "中央10": "CCTV-10",
            "CCTV11": "CCTV-11", "央视11": "CCTV-11", "中央11": "CCTV-11",
            "CCTV12": "CCTV-12", "央视12": "CCTV-12", "中央12": "CCTV-12",
            "CCTV13": "CCTV-13", "央视13": "CCTV-13", "中央13": "CCTV-13"
        }
        for key, std in cctv_map.items():
            if key in name.upper():
                return std
        # 特殊频道
        if "少儿" in name:
            return "CCTV-少儿"
        if "综艺" in name:
            return "CCTV-综艺"
        if "体育" in name:
            return "CCTV-体育"
        if "电影" in name:
            return "CCTV-电影"
        return name
    # 卫视统一为 XX卫视 格式
    if "卫视" in name or any(k in name for k in ["湖南", "浙江", "江苏", "东方", "广东", "北京", "山东"]):
        weishi_map = {
            "湖南": "湖南卫视", "浙江": "浙江卫视", "江苏": "江苏卫视",
            "东方": "东方卫视", "广东": "广东卫视", "北京": "北京卫视",
            "山东": "山东卫视", "上海": "东方卫视"
        }
        for key, std in weishi_map.items():
            if key in name:
                return std
        return name
    return name

# ===================== 4. 采集源内容抓取 =====================
def fetch_source(url):
    try:
        url = replace_cdn(url)
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        return resp.text
    except Exception:
        return ""

# ===================== 5. 解析TXT/M3U格式，提取频道 =====================
def parse_content(text):
    channels = []
    # 解析TXT格式（名称,链接）
    txt_pattern = re.compile(r"([^,#\n\r]+?),(http.+?)(?=$|\n|\r)")
    for name, url in txt_pattern.findall(text):
        std_name = standardize_name(name)
        fixed_url = replace_cdn(url.strip())
        channels.append((std_name, fixed_url))
    # 解析M3U格式
    lines = text.splitlines()
    current_name = ""
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            name_match = re.search(r",(.+)$", line)
            if name_match:
                current_name = standardize_name(name_match.group(1))
        elif line.startswith(("http://", "https://")):
            fixed_url = replace_cdn(line)
            if any(ext in fixed_url for ext in ["m3u8", "flv", "ts", "mp4"]):
                channels.append((current_name or "未知频道", fixed_url))
                current_name = ""
    # 去重
    return list(dict.fromkeys(channels))

# ===================== 6. 多线程测速，过滤失效源 =====================
def check_channel(item):
    name, url = item
    try:
        resp = requests.head(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code in [200, 301, 302, 403]:
            return (name, url)
    except Exception:
        pass
    return None

# ===================== 7. 自动分类排序 =====================
def sort_channels(channels):
    cctv_list = []
    weishi_list = []
    local_list = []
    other_list = []
    for name, url in channels:
        line = f"{name},{url}"
        if name.startswith("CCTV-"):
            cctv_list.append(line)
        elif "卫视" in name:
            weishi_list.append(line)
        elif any(city in name for city in ["山东", "济南", "青岛", "烟台", "潍坊"]):
            local_list.append(line)
        else:
            other_list.append(line)
    # 央视按数字排序
    def cctv_sort_key(x):
        num_match = re.search(r"CCTV-(\d+)", x)
        return int(num_match.group(1)) if num_match else 999
    cctv_list.sort(key=cctv_sort_key)
    # 拼接最终结果
    result = [
        "# ========== 央视频道 ==========", *cctv_list,
        "# ========== 卫视频道 ==========", *weishi_list,
        "# ========== 地方频道 ==========", *local_list,
        "# ========== 其他频道 ==========", *other_list
    ]
    return result

# ===================== 8. 生成M3U格式文件 =====================
def generate_m3u(lines):
    m3u_content = ["#EXTM3U"]
    for line in lines:
        if line.startswith("#") or "," not in line:
            continue
        name, url = line.split(",", 1)
        m3u_content.append(f'#EXTINF:-1 tvg-name="{name}",{name}')
        m3u_content.append(url)
    return "\n".join(m3u_content)

# ===================== 9. 主程序入口 =====================
if __name__ == "__main__":
    # 1. 采集所有源
    all_channels = []
    for source in SOURCE_URLS:
        content = fetch_source(source)
        all_channels.extend(parse_content(content))
    # 2. 去重
    all_channels = list(set(all_channels))
    # 3. 多线程测速
    with ThreadPoolExecutor(max_workers=20) as executor:
        valid_channels = [ch for ch in executor.map(check_channel, all_channels) if ch]
    # 4. 分类排序
    sorted_lines = sort_channels(valid_channels)
    # 5. 输出TXT文件（TVBox用）
    with open("iptv_live.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(sorted_lines))
    # 6. 输出M3U文件（通用播放器用）
    m3u_text = generate_m3u(sorted_lines)
    with open("iptv_live.m3u", "w", encoding="utf-8") as f:
        f.write(m3u_text)
    print("✅ 采集完成！已生成：iptv_live.txt / iptv_live.m3u")
    print("✅ 央视统一CCTV-1格式，卫视统一XX卫视格式，已完成CDN优化与测速过滤")

