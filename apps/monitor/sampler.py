"""نمونه‌گیری وضعیت سرور از /proc و آمار دیسک (لینوکس). دو نمونه‌ی فاصله‌دار برای سرعت‌ها."""
import os
import shutil
import time

SECTOR = 512


def _cpu_times():
    with open("/proc/stat") as f:
        parts = f.readline().split()[1:]
    vals = [int(x) for x in parts[:8]]
    idle = vals[3] + vals[4]
    return sum(vals), idle


def _mem():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
    total = info["MemTotal"]
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    return total, (total - avail) / total * 100 if total else 0.0


def _net():
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, data = line.split(":", 1)
            if name.strip() == "lo":
                continue
            cols = data.split()
            rx += int(cols[0])
            tx += int(cols[8])
    return rx, tx


def _disk_io():
    """خواندن و نوشتن کل دیسک‌های فیزیکی (بایت)."""
    r = w = 0
    with open("/proc/diskstats") as f:
        for line in f:
            cols = line.split()
            name = cols[2]
            if name.startswith(("loop", "ram")) or name[-1:].isdigit() and not name.startswith("nvme"):
                continue
            r += int(cols[5]) * SECTOR
            w += int(cols[9]) * SECTOR
    return r, w


def sample(seconds=1.0):
    """وضعیت لحظه‌ای سرور؛ سرعت‌ها روی بازه‌ی seconds اندازه‌گیری می‌شوند."""
    t0 = _cpu_times(); n0 = _net(); d0 = _disk_io()
    time.sleep(seconds)
    t1 = _cpu_times(); n1 = _net(); d1 = _disk_io()
    total = t1[0] - t0[0]
    cpu = (1 - (t1[1] - t0[1]) / total) * 100 if total else 0.0
    ram_total, ram_pct = _mem()
    usage = shutil.disk_usage("/")
    return {
        "cpu": round(cpu, 1), "ram": round(ram_pct, 1), "ram_total_mb": ram_total // 1024,
        "rx_kbps": round((n1[0] - n0[0]) / seconds / 1024, 1), "tx_kbps": round((n1[1] - n0[1]) / seconds / 1024, 1),
        "disk_used_gb": round(usage.used / 1024 ** 3, 2), "disk_total_gb": round(usage.total / 1024 ** 3, 2),
        "read_kbps": round((d1[0] - d0[0]) / seconds / 1024, 1), "write_kbps": round((d1[1] - d0[1]) / seconds / 1024, 1),
    }
