from flask import Flask, Response, jsonify
import json

app = Flask(__name__)

DATA_FILE = "streams.json"


# =========================
# 读取数据
# =========================
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


# =========================
# IPTV M3U输出
# =========================
@app.route("/api/iptv")
def iptv():
    data = load_data()

    m3u = ["#EXTM3U"]

    for item in data:
        m3u.append(f'#EXTINF:-1 group-title="{item.get("group","其他")}",{item["name"]}')
        m3u.append(item["url"])

    return Response("\n".join(m3u), mimetype="text/plain")


# =========================
# JSON输出
# =========================
@app.route("/api/json")
def api_json():
    return jsonify(load_data())


# =========================
# 状态检查
# =========================
@app.route("/")
def index():
    return {
        "status": "ok",
        "count": len(load_data())
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
