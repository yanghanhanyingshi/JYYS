import requests
import re
from concurrent.futures import ThreadPoolExecutor

# 只留2个最稳接口，减负防炸
SOURCE_URLS = [
    "https://cloud.7so.top/f/Bgw1H8/%E5%A4%A7%E6%94%B9.txt",
    "https://dsj-1312694395.cos.ap-guangzhou.myqcloud.com/dsj10.1.txt"
]
HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 2       # 超短超时，慢链直接弃
MAX_WORKERS = 8   # 低并发不被封
# ==============================================

# CDN国内替换
def replace_cdn(url):
    w1 = re.search(r"wget\.la/https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/[^/]+/(.+)",url)
    w2 = re.search(r"raw\.githubusercontent\.com/([^/]+)/([^/]+)/[^/]+/(.+)",url)
    w3 = re.search(r"github\.com/([^/]+)/([^/]+)/blob/[^/]+/(.+)",url)
    if w1: return f"https://cdn.jsdelivr.net/gh/{w1.group(1)}/{w1.group(2)}/{w1.group(3)}"
    if w2: return f"https://cdn.jsdelivr.net/gh/{w2.group(1)}/{w2.group(2)}/{w2.group(3)}"
    if w3: return f"https://cdn.jsdelivr.net/gh/{w3.group(1)}/{w3.group(2)}/{w3.group(3)}"
    return url

# 名称标准化
def standard_name(name):
    n = name.strip().replace(" ","")
    # 央视规整
    if any(k in n.upper() for k in ["CCTV","央视","中央"]):
        mp = {
            "CCTV1":"CCTV-1","央视1":"CCTV-1","CCTV2":"CCTV-2","央视2":"CCTV-2",
            "CCTV3":"CCTV-3","央视3":"CCTV-3","CCTV4":"CCTV-4","央视4":"CCTV-4",
            "CCTV5":"CCTV-5","央视5":"CCTV-5","CCTV6":"CCTV-6","央视6":"CCTV-6",
            "CCTV7":"CCTV-7","央视7":"CCTV-7","CCTV8":"CCTV-8","央视8":"CCTV-8"
        }
        for kw,v in mp.items():
            if kw in n: return v
        if "少儿" in n: return "CCTV-少儿"
    # 卫视规整
    if "卫视" in n or any(x in n for x in ["湖南","浙江","山东","江苏","广东"]):
        mp = {"湖南":"湖南卫视","浙江":"浙江卫视","山东":"山东卫视","江苏":"江苏卫视","广东":"广东卫视"}
        for kw,v in mp.items():
            if kw in n: return v
    return n

# 只保留：央视 + 卫视，过滤乱七八糟小台
def need_filter(name):
    if name.startswith("CCTV-") or "卫视" in name:
        return False
    return True

# 抓取解析
def fetch_txt(url):
    try:
        r = requests.get(replace_cdn(url), headers=HEADERS, timeout=10)
        r.encoding="utf-8"
        return r.text
    except:
        return ""

def parse_all(text):
    arr = []
    pat = re.compile(r"([^,#\n\r]+?),(http.+?(m3u8|ts|flv))")
    for name,url,_ in pat.findall(text):
        nm = standard_name(name)
        if need_filter(nm): continue # 过滤垃圾台
        arr.append( (nm, replace_cdn(url.strip())) )
    return list(dict.fromkeys(arr))

# 轻度测速
def quick_check(item):
    name,url = item
    try:
        resp = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code in (200,301,302,403):
            return item
    except:
        pass
    return None

# 分类排序
def sort_group(arr):
    cctv,ws = [],[]
    for n,u in arr:
        line = f"{n},{u}"
        if n.startswith("CCTV-"):
            cctv.append(line)
        elif "卫视" in n:
            ws.append(line)
    def ckey(x):
        m=re.search(r"CCTV-(\d+)",x)
        return int(m.group(1)) if m else 999
    cctv.sort(key=ckey)
    return ["#===央视==="]+cctv+["#===卫视==="]+ws

# 生成m3u
def make_m3u(lines):
    m3u=["#EXTM3U"]
    for L in lines:
        if L.startswith("#") or "," not in L: continue
        n,u = L.split(",",1)
        m3u.append(f'#EXTINF:-1,{n}\n{u}')
    return "\n".join(m3u)

if __name__ == "__main__":
    total = []
    for src in SOURCE_URLS:
        total.extend(parse_all(fetch_txt(src)))
    total = list(set(total))
    print(f"待测速优质台数量：{len(total)}")

    # 少量并发快测，绝不卡死
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        ok = [x for x in ex.map(quick_check,total) if x]

    res_lines = sort_group(ok)
    with open("iptv_live.txt","w",encoding="utf-8") as f:
        f.write("\n".join(res_lines))
    with open("iptv_live.m3u","w",encoding="utf-8") as f:
        f.write(make_m3u(res_lines))
    print("✅ 轻度测速完成，只留可用央视+卫视，不卡死")

