import requests, json

addr = "0x0D1C4C4C8Dfd6706Ae549Fa788974A02B79F01B5"

urls = [
    f"https://gamma-api.polymarket.com/balances?addresses[]={addr}",
    f"https://clob.polymarket.com/balances?address={addr}",
]

for url in urls:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    print("URL", url)
    print("status", resp.status_code)
    body = resp.text[:1000]
    print(body)
    if resp.ok:
        try:
            print(json.loads(body))
        except Exception:
            pass
    print("---")