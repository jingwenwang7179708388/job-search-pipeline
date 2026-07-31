import requests, json

H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

print("=== 测试 1: 直接 POST 一个已知的 Workday 站点 ===")
url = "https://hp.wd5.myworkdayjobs.com/wday/cxs/hp/ExternalCareerSite/jobs"
try:
    r = requests.post(url, json={"appliedFacets": {}, "limit": 20,
                                 "offset": 0, "searchText": ""},
                      headers={**H, "Content-Type": "application/json"}, timeout=15)
    print("状态码:", r.status_code)
    print("Server:", r.headers.get("Server"))
    print("前 300 字:", r.text[:300])
    if r.ok:
        d = r.json()
        print(">>> total =", d.get("total"), " 返回岗位数 =", len(d.get("jobPostings", [])))
except Exception as e:
    print("异常:", type(e).__name__, e)

print("\n=== 测试 2: 访问根路径看跳转 ===")
try:
    r = requests.get("https://hp.wd5.myworkdayjobs.com/", headers=H,
                     timeout=15, allow_redirects=True)
    print("状态码:", r.status_code)
    print("最终网址:", r.url)
    print("跳转链:", [x.status_code for x in r.history])
except Exception as e:
    print("异常:", type(e).__name__, e)

print("\n=== 测试 3: 一个肯定不存在的租户（对照组）===")
try:
    r = requests.get("https://zzzznotreal.wd5.myworkdayjobs.com/", headers=H,
                     timeout=15, allow_redirects=True)
    print("状态码:", r.status_code, " 最终网址:", r.url)
except Exception as e:
    print("异常:", type(e).__name__, e)