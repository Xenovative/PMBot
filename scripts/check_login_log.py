import json
import sys
from pathlib import Path

LOG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("login_audit.log")

if not LOG_PATH.exists():
    print(f"Log file not found: {LOG_PATH}")
    sys.exit(1)

# Collect stats
entries = []
with LOG_PATH.open("r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue

if not entries:
    print("No entries parsed.")
    sys.exit(0)

# Summary
success = sum(1 for e in entries if e.get("success"))
fail = len(entries) - success
by_reason = {}
by_ip = {}
for e in entries:
    reason = e.get("reason", "?")
    by_reason[reason] = by_reason.get(reason, 0) + 1
    ip = e.get("ip", "?")
    by_ip[ip] = by_ip.get(ip, 0) + 1

print(f"Log: {LOG_PATH}")
print(f"Total entries: {len(entries)} | Success: {success} | Fail: {fail}")
print("Top failure reasons:")
for reason, count in sorted(by_reason.items(), key=lambda x: x[1], reverse=True):
    if reason == "ok":
        continue
    print(f"  {reason}: {count}")
print("Top IPs:")
for ip, count in sorted(by_ip.items(), key=lambda x: x[1], reverse=True)[:10]:
    print(f"  {ip}: {count}")

print("\nRecent entries (last 10):")
for e in entries[-10:]:
    print(json.dumps(e, ensure_ascii=False))
