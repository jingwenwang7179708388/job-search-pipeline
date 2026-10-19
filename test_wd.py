import requests, json

H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

print("=== Test 1: POST directly to a known Workday site ===")
url = "https://hp.wd5.myworkdayjobs.com/wday/cxs/hp/ExternalCareerSite/jobs"
try:
    r = requests.post(url, json={"appliedFacets": {}, "limit": 20,
                                 "offset": 0, "searchText": ""},
                      headers={**H, "Content-Type": "application/json"}, timeout=15)
    print("Status:", r.status_code)
    print("Server:", r.headers.get("Server"))
    print("First 300 chars:", r.text[:300])
    if r.ok:
        d = r.json()
        print(">>> total =", d.get("total"), " postings returned =", len(d.get("jobPostings", [])))
except Exception as e:
    print("Exception:", type(e).__name__, e)

print("\n=== Test 2: request the root path and look at redirects ===")
try:
    r = requests.get("https://hp.wd5.myworkdayjobs.com/", headers=H,
                     timeout=15, allow_redirects=True)
    print("Status:", r.status_code)
    print("Final URL:", r.url)
    print("Redirect chain:", [x.status_code for x in r.history])
except Exception as e:
    print("Exception:", type(e).__name__, e)

print("\n=== Test 3: a tenant that definitely does not exist (control) ===")
try:
    r = requests.get("https://zzzznotreal.wd5.myworkdayjobs.com/", headers=H,
                     timeout=15, allow_redirects=True)
    print("Status:", r.status_code, " Final URL:", r.url)
except Exception as e:
    print("Exception:", type(e).__name__, e)