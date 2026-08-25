"""测试语义缓存命中：先发送基准问题，再发送变体问法，观察是否命中缓存。"""

import time
import requests

QUESTIONS = [
    ("本月产电量多少", "BASELINE — 首次查询，应走 sql_generator → 存入缓存"),
    ("这个月发电量是多少", "变体1: 同义词'发电量' + '这个月' 重述 → 应命中缓存"),
    ("本月发了多少电", "变体2: 口语化'发了多少电' → 应命中缓存"),
    ("本月各电站的产电量", "变体3: 加'各电站'(group_by变化) → 应MISS(槽位不同)"),
    ("这个月总共发了多少电", "变体4: '总共'强调 → 应命中缓存"),
    ("本月发电量查询", "变体5: 尾缀'查询' → 应命中缓存"),
]

URL = "http://localhost:8000/v1/query"

print("=" * 70)
print("语义缓存命中测试")
print("=" * 70)

for i, (q, desc) in enumerate(QUESTIONS):
    print(f"\n[{i}] {desc}")
    print(f"    Q: {q}")
    try:
        r = requests.post(URL, json={"question": q}, timeout=60)
        if r.status_code == 200:
            d = r.json()
            cached = d.get("cached")
            sql = d.get("sql") or ""
            error = d.get("error")
            if error:
                print(f"    ❌ error: {error}")
            elif cached:
                print(f"    ✅ CACHE HIT")
                print(f"    SQL: {sql[:80]}...")
            else:
                print(f"    🔄 CACHE MISS (走 sql_generator)")
                print(f"    SQL: {sql[:80]}...")
        else:
            print(f"    ❌ HTTP {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"    ❌ Exception: {e}")
    time.sleep(2)  # 等待 AOSS 最终一致性

print("\n" + "=" * 70)
print("测试完成")
print("=" * 70)
