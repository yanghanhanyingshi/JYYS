import requests
import re
import time
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ========== 你的豆包AI官方配置 ==========
DOUBAO_API_KEY = "a5c4504e-8146-4681-80d3-a206ea1f79ce"
DOUBAO_EP_ID    = "ep-20260330071637-v4ldt"
DOUBAO_API_URL  = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MAX_KEEP_PER_CH = 5   # 每个频道保留最优5条活源
# ======================================

# CCTV完整别名映射
CCTV_NAME_FULL = {
    "CCTV1": "CCTV1综合",
    "CCTV2": "CCTV2财经",
    "CCTV3": "CCTV3综艺",
    "CCTV4": "CCTV4中文国际",
    "CCTV5": "CCTV5体育",
    "CCTV5+": "CCTV5+体育赛事",
    "CCTV6": "CCTV6电影",
    "CCTV7": "CCTV7国防军事",
    "CCTV8": "CCTV8电视剧",
    "CCTV9": "CCTV9纪录",
    "CCTV10": "CCTV10科教",
    "CCTV11": "CCTV11戏曲",
    "CCTV12": "CCTV12社会与法",
    "CCTV13": "CCTV13新闻",
    "CCTV14": "CCTV14少儿",
    "CCTV15": "CCTV15音乐",
    "CCTV17": "CCTV17农业农村"
}

# 排序模板：央视+卫视固定顺序
CCTV_ORDER = [
    "CCTV1", "CCTV2", "CCTV3", "CCTV4", "CCTV5", "CCTV5+", "CCTV6", "CCTV7",
    "CCTV8", "CCTV9", "CCTV10", "CCTV11", "CCTV12", "CCTV13", "CCTV14", "CCTV15", "CCTV17"
]

WEISHI_ORDER = [
    "北京卫视", "天津卫视", "河北卫视", "山西卫视", "内蒙古卫视",
    "辽宁卫视", "吉林卫视", "黑龙江卫视", "东方卫视", "江苏卫视",
    "浙江卫视", "安徽卫视", "福建卫视", "江西卫视", "山东卫视",
    "河南卫视", "湖北卫视", "湖南卫视", "广东卫视", "广西卫视",
    "重庆卫视", "四川卫视", "贵州卫视", "云南卫视", "陕西卫视",
    "甘肃卫视", "青海卫视", "宁夏卫视", "新疆卫视", "旅游卫视"
]
ALL_ORDER = CCTV_ORDER + WEISHI_ORDER
ALL_CHANNEL_NAMES = list(CCTV_NAME_FULL.values()) + WEISHI_ORDER

# 采集源地址
SOURCES = [
    "https://cloud.7so.top/f/Bgw1H8/%E5%A4%A7%E6%94%B9.txt",
    "https://wget.la/https://raw.githubusercontent.com/Jsnzkpg/Jsnzkpg/Jsnzkpg/Jsnzkpg1.m3u",
    "https://wget.la/https://github.com/fafa002/yf2025/blob/main/yiyifafa.txt",
    "https://dsj-1312694395.cos.ap-guangzhou.myqcloud.com/dsj10.1.txt"
]

# 测速超时配置
TEST_TIMEOUT = 3
MAX_WORKERS = 30

# 北京时间时区（UTC+8）
BEIJING_TZ = timezone(timedelta(hours=8))

def doubao_ai_keep_best_urls(url_list, keep_num=5):
    """AI智能去重同源链接，筛选最优存活5条"""
    if len(url_list) <= keep_num:
        return url_list
    prompt = f"""下面是同一个频道多条播放URL，有大量同源重复、冗余线路，请智能去重，只挑选质量最优、域名不同源的{keep_num}条，按顺序只返回url每行一条，不要多余文字：
{chr(10).join(url_list)}"""
    payload = {
        "model": DOUBAO_EP_ID,
        "messages": [{"role":"user", "content": prompt}],
        "temperature": 0.2
    }
    try:
        res = requests.post(
            DOUBAO_API_URL,
            headers={
                "Authorization": f"Bearer {DOUBAO_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=12
        )
        lines = [x.strip() for x in res.json()["choices"][0]["message"]["content"].splitlines() if x.strip()]
        return lines[:keep_num]
    except Exception as e:
        print("AI精选线路失败，降级普通去重:", e)
        return list(dict.fromkeys(url_list))[:keep_num]

def doubao_ai_fix_channel_name(channel_name):
    """AI自动修正乱码/不规范频道名，返回标准名称"""
    prompt = f"""请将不规范直播频道名修正为标准官方名，只返回结果无多余内容：
待修正：{channel_name}
标准库：{','.join(ALL_CHANNEL_NAMES)}
无匹配返回原名"""
    payload = {
        "model": DOUBAO_EP_ID,
        "messages": [{"role":"user", "content": prompt}],
        "temperature": 0.3
    }
    try:
        res = requests.post(
            DOUBAO_API_URL,
            headers={
                "Authorization": f"Bearer {DOUBAO_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10
        )
        return res.json()["choices"][0]["message"]["content"].strip()
    except:
        return channel_name

def doubao_ai_summary(ok_count, time_str):
    """AI生成文末简短播报"""
    payload = {
        "model": DOUBAO_EP_ID,
        "messages": [{"role":"user","content":f"生成30字内直播源更新备注：更新{time_str}，有效{ok_count}条，AI去重每条保留5条最优线路"}],
        "temperature": 0.7
    }
    try:
        res = requests.post(
            DOUBAO_API_URL,
            headers={
                "Authorization": f"Bearer {DOUBAO_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=15
        )
        return res.json()["choices"][0]["message"]["content"].strip()
    except:
        return "AI摘要生成失败"

def fetch_text(url):
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"抓取失败: {url} | {e}")
        return ""

def parse_channels(text):
    channels = []
    pat = re.compile(r"^(.+?),\s*(https?://.+)", re.IGNORECASE)
    for line in text.splitlines():
        line = line.strip()
        m = pat.match(line)
        if m:
            name, uri = m.groups()
            channels.append((name.strip(), uri.strip()))
    return channels

def normalize_name(name):
    raw = name.upper().replace(" ", "")
    if "CCTV5+" in raw:
        return "CCTV5+"
    cctv_mat = re.search(r"CCTV[-_]?(\d+)", raw)
    if cctv_mat:
        return f"CCTV{cctv_mat.group(1)}"
    ai_fixed = doubao_ai_fix_channel_name(name)
    for ws in WEISHI_ORDER:
        if ws in ai_fixed:
            return ws
    return ai_fixed

def check_url_alive(uri):
    try:
        r = requests.head(uri, timeout=TEST_TIMEOUT, headers=headers, allow_redirects=True)
        if r.status_code in (200,301,302,304):
            return True,uri
    except:
        pass
    try:
        r = requests.get(uri, timeout=TEST_TIMEOUT, headers=headers, stream=True)
        for _ in r.iter_content(chunk_size=1024):
            return True,uri
    except:
        pass
    return False,uri

def batch_filter_urls(uri_list):
    valid=[]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
        res=exe.map(check_url_alive,uri_list)
    for ok,u in res:
        if ok:
            valid.append(u)
    return valid

def save_file(content_list,fname):
    with open(fname,"w",encoding="utf-8")as f:
        f.write("\n".join(content_list))

def get_beijing_time():
    return datetime.now(BEIJING_TZ).strftime("%Y%m%d %H:%M")

def main():
    CURRENT_BJ_TIME=get_beijing_time()
    print(f"运行北京时间：{CURRENT_BJ_TIME}")
    time_url="https://d.kstore.dev/download/7547/20260401003530.mp4"

    all_raw=[]
    for src in SOURCES:
        print(f"抓取：{src}")
        txt=fetch_text(src)
        all_raw.extend(parse_channels(txt))

    channel_map={k:[]for k in ALL_ORDER}
    for nm,url in all_raw:
        std_nm=normalize_name(nm)
        for key in ALL_ORDER:
            if std_nm in (key,CCTV_NAME_FULL.get(key,key)):
                channel_map[key].append(url)
                break

    print("测速过滤死链+AI智能去重精选每条保留5条...")
    valid_map={}
    for chn,uris in channel_map.items():
        if not uris:
            valid_map[chn]=[]
            continue
        unique_uris=list(dict.fromkeys(uris))
        ok_uris=batch_filter_urls(unique_uris)
        # AI同源去重+精选保留最优5条
        best_uris=doubao_ai_keep_best_urls(ok_uris,MAX_KEEP_PER_CH)
        valid_map[chn]=best_uris

    out_lines=["家用频道,#genre#"]
    raw_all_lines=["家用频道,#genre#"]
    for chn in ALL_ORDER:
        show_name=CCTV_NAME_FULL.get(chn,chn)
        for idx,vu in enumerate(valid_map[chn],1):
            out_lines.append(f"{show_name},{vu}$LR•IPV4•29『线路{idx}』")
        for idx,ru in enumerate(channel_map[chn],1):
            raw_all_lines.append(f"{show_name},{ru}$LR•IPV4•29『线路{idx}』")

    # 时间放末尾
    out_lines.append(f"{CURRENT_BJ_TIME},{time_url}")
    raw_all_lines.append(f"{CURRENT_BJ_TIME},{time_url}")

    ai_text=doubao_ai_summary(len(out_lines)-2,CURRENT_BJ_TIME)
    out_lines.append(f"# 豆包AI播报：{ai_text}")
    raw_all_lines.append(f"# 豆包AI播报：{ai_text}")

    save_file(out_lines,"live.txt")
    save_file(raw_all_lines,"result.txt")

    print(f"✅ 时间戳：{CURRENT_BJ_TIME}")
    print(f"✅ AI每条保留5条最优活源，已完成纠错+去重+测速")

if __name__=="__main__":
    main()
