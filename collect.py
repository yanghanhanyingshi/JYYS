import requests
import re
from concurrent.futures import ThreadPoolExecutor

# 只留2个稳源减负
SOURCE_URLS = [
    "https://cloud.7so.top/f/Bgw1H8/%E5%A4%A7%E6%94%B9.txt",
    "https://dsj-1312694395.cos.ap-guangzhou.myqcloud.com/dsj10.1.txt"
]
HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 2
MAX_WORKERS = 6

# CDN替换
def replace_cdn(url):
    a = re.search(r"wget\.la/https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/[^/]+/(.+)", url)
    b = re.search(r"raw\.githubusercontent\.com/([^/]+)/([^/]+)/[^/]+/(.+)", url)
    c = re.search(r"github\.com/([^/]+)/([^/]+)/blob/[^/]+/(.+)", url)
    if a:
        return f"https://cdn.jsdelivr.net/gh/{a.group(1)}/{a.group(2)}/{a.group(3)}"
    if b:
        return f"https://cdn.jsdelivr.net/gh/{b.group(1)}/{b.group(2)}/{b.group(3)}"
    if c:
        return f"https://cdn.jsdelivr.net/gh/{c.group(1)}/{c.group(2)}/{c.group(3)}"
    return url

# 名称修正
def fix_name(name):
    n = name.strip().replace(" ", "")
    cctv_map = {
        "CCTV1":"CCTV-1","央视1":"CCTV-1",
        "CCTV2":"CCTV-2","央视2":"CCTV-2",
        "CCTV3":"CCTV-3","央视3":"CCTV-3",
        "CCTV4":"CCTV-4","央视4":"CCTV-4",
        "CCTV5":"CCTV-5","央视5":"CCTV-5",
        "CCTV6":"CCTV-6","央视6":"CCTV-6",
        "CCTV7":"CCTV-7","央视7":"CCTV-7",
        "CCTV8":"CCTV-8","央视8":"CCTV-8"
    }
    for k,v in cctv_map.items():
        if k in n:
            return v
    if "少儿" in n:
        return "CCTV-少儿"
    ws_map = {
        "湖南":"湖南卫视","浙江":"浙江卫视","山东":"山东卫视"
    }
    for k,v in ws_map.items():
        if k in n:
            return v
    return n

# 只保留央视+卫视
def is_keep(name):
    if name.startswith("CCTV-") or "卫视" in name:
        return True
    return False

# 抓取
def get_text(url):
    try:
        r = requests.get(replace_cdn(url), headers=HEADERS, timeout=8)
        r.encoding = "utf-8"
        return r.text
    except:
        return ""

# 解析
def parse(text):
    arr = []
    pat = re.compile(r"([^,#\n\r]{1,50}),(https?://.{30,200}(m3u8|ts|flv))")
    for nm,url,_ in pat.findall(text):
        new_nm = fix_name(nm)
        if not is_keep(new_nm):
            continue
        arr.append((new_nm, replace_cdn(url)))
    return list(dict.fromkeys(arr))

# 快速测速
def check_ok(item):
    name,url = item
    try:
        res = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
        if res.status_code in (200,301,302,403):
            return item
    except:
        pass
    return None

# 排序
def sort_list(arr):
    cctv = []
    ws = []
    for n,u in arr:
        line = f"{n},{u}"
        if n.startswith("CCTV-"):
            cctv.append(line)
        else:
            ws.append(line)
    def ckey(x):
        m = re.search(r"CCTV-(\d+)",x)
        return int(m.group(1)) if m else 999
    cctv.sort(key=ckey)
    return ["#========央视========"]+cctv+["#========卫视========"]+ws

# 生成m3u
def build_m3u(lines):
    m3u = ["#EXTM3U"]
    for line in lines:
        if line.startswith("#") or "," not in line:
            continue
        n,u = line.split(",",1)
        m3u.append(f"#EXTINF:-1,{n}")
        m3u.append(u)
    return "\n".join(m3u)

if __name__ == "__main__":
    all_data = []
    for src in SOURCE_URLS:
        txt = get_text(src)
        all_data.extend(parse(txt))
    all_data = list(set(all_data))
    print("待测速条数:", len(all_data))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        valid = [x for x in pool.map(check_ok, all_data) if x]

    final = sort_list(valid)
    with open("iptv_live.txt","w",encoding="utf-8") as f:
        f.write("\n".join(final))
    with open("iptv_live.m3u","w",encoding="utf-8") as f:
        f.write(build_m3u(final))
    print("✅运行完成，只输出可用央视卫视")
