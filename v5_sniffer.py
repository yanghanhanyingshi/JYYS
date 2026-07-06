import json

def save(self):
    data = []

    for url in self.streams:
        data.append({
            "name": url.split("/")[-1][:12],
            "group": "直播",
            "url": url
        })

    with open("streams.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
