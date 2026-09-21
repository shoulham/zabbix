
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zabbix vCenter Daily Report v2.0 By Shoulham
- 2026-7-27 v1.0
- 全动态发现：vCenter/ESXi/Datastore 全部自动获取，无需硬编码
- --discover 模式：快速发现新增/移除
- LLM AI 分析：对接 OpenAI 兼容 API

- 2026-8-26 v2.0
- 新增ESXi主机内存占用率情况及趋势
- 配合zabbix插件-趋势报表
"""

import requests, json, sys, time, base64, os, smtplib, ssl, argparse
from email.mime.text import MIMEText
from email.header import Header
import urllib3
urllib3.disable_warnings()

# ===== ZABBIX =====
_Z_U = "https://localhost/api_jsonrpc.php"
_Z_T = "/tmp/zabbix_rpt_token.txt"
_Z_P = "xx"
_Z_UNAME = "xx"
_VC_GRP = "23"
_ESXI_GRP = "7"
_CACHE = "/tmp/zabbix_rpt_data.json"
_DISC_CACHE = "/tmp/zabbix_discovery_cache.json"

# ===== SMTP =====
_S_HOST = "smtp.xx.com"
_S_PORT = 465
_S_USER = "yy@xx.com"
_S_PASS = "xx"
_S_TO = ["zz@v.com"]

# ===== LLM =====
_L_URL = "大模型API"
_L_KEY = "API-KEY"
_L_MODEL = "模型名称"
_L_TO = 120
_L_MAX = 2048
_DAYS = 7

def _api(method, params):
    if not os.path.exists(_Z_T):
        return None
    with open(_Z_T) as f:
        tok = f.read().strip()
    r = requests.post(_Z_U, json={
        "jsonrpc": "2.0", "method": method, "params": params,
        "auth": tok, "id": int(time.time())
    }, verify=False, timeout=60)
    return r.json()

def _login():
    pw = base64.b64decode(_Z_P.encode()).decode()
    r = requests.post(_Z_U, json={
        "jsonrpc": "2.0", "method": "user.login",
        "params": {"username": _Z_UNAME, "password": pw},
        "id": int(time.time())
    }, verify=False, timeout=30)
    res = r.json()
    if "result" in res:
        with open(_Z_T, "w") as f:
            f.write(res["result"])
        return True
    print(f"Login failed: {res.get('error', 'unknown')}")
    return False

def _hist(iid, vt="0", days=None):
    if days is None:
        days = _DAYS
    now = int(time.time())
    r = _api("history.get", {
        "history": vt, "itemids": iid,
        "time_from": now - days * 86400, "time_till": now,
        "output": "extend", "sortfield": "clock", "sortorder": "DESC",
        "limit": 500})
    return r.get("result", []) if r else []

def _nums(iid, vt="0", days=None):
    vals = []
    for v in _hist(iid, vt, days):
        try:
            vals.append(float(v["value"]))
        except:
            pass
    return vals


def _hist7(iid, vt="0", days=None, per_day=200):
    """按天分段拉取 7 天历史，返回带 clock 的原始记录列表。
    每天划分为一个区间用 history.get 取 per_day 条（按时间翻转），
    汇总后即代表完整的 7 天趋势，避免只取最近数百条导致的偏差。
    """
    if days is None:
        days = _DAYS
    now = int(time.time())
    out = []
    day = 86400
    for k in range(days):
        t1 = now - (k + 1) * day
        t2 = now - k * day
        r = _api("history.get", {
            "history": vt, "itemids": iid,
            "time_from": t1, "time_till": t2,
            "output": "extend", "sortfield": "clock", "sortorder": "DESC",
            "limit": per_day})
        if r:
            out.extend(r.get("result", []))
    # 按时间升序，方便 later 用 [0] 取最新（若需要）
    out.sort(key=lambda x: x.get("clock", 0), reverse=True)
    return out

def _nums7(iid, vt="0", days=None, per_day=200):
    vals = []
    for v in _hist7(iid, vt, days, per_day):
        try:
            vals.append(float(v["value"]))
        except:
            pass
    return vals

def _items(hid, search=None, limit=100):
    params = {
        "hostids": hid,
        "output": ["itemid", "name", "key_", "value_type", "units", "lastvalue", "lastclock"],
        "sortfield": "name", "limit": limit}
    if search:
        params["search"] = search
    return _api("item.get", params)

def _stats(vals):
    if not vals:
        return {"avg": 0, "max": 0, "min": 0, "p95": 0, "n": 0}
    s = sorted(vals)
    return {
        "avg": round(sum(vals) / len(vals), 1),
        "max": round(max(vals), 1),
        "min": round(min(vals), 1),
        "p95": round(s[int(len(s) * 0.95)], 1),
        "n": len(vals)}

def discover_vcenters():
    res = _api("host.get", {
        "groupids": _VC_GRP,
        "output": ["hostid", "host", "name", "status"],
        "sortfield": "name"})
    vcs = []
    for h in res.get("result", []):
        vcs.append({
            "hostid": h["hostid"],
            "hostname": h["host"],
            "name": h.get("name", h["host"]),
            "status": h.get("status", "0")})
    return vcs

def discover_esxi():
    res = _api("host.get", {
        "groupids": _ESXI_GRP,
        "output": ["hostid", "host", "name", "status"],
        "sortfield": "name"})
    hosts = []
    for h in res.get("result", []):
        hosts.append({
            "hostid": h["hostid"],
            "hostname": h["host"],
            "name": h.get("name", h["host"]),
            "status": h.get("status", "0")})
    return hosts

def discover_ds(vcs):
    ds_map = {}
    for vc in vcs:
        vcn = vc["name"]
        vcid = vc["hostid"]
        its = _items(vcid, {"name": "Free space on datastore"}, 200)
        for it in its.get("result", []):
            nm = it["name"]
            dn = None
            for pfx in ["VMware: Free space on datastore ", "Free space on datastore "]:
                if pfx in nm:
                    dn = nm.replace(pfx, "").replace(" (percentage)", "").strip()
                    break
            if dn is None:
                dn = nm
            if dn and dn not in ds_map:
                ds_map[dn] = {"vc": vcn, "vcid": vcid}
    return ds_map

def run_discovery():
    print("=" * 60)
    print("  ZABBIX vCenter DISCOVERY")
    print("=" * 60)
    vcs = discover_vcenters()
    esxis = discover_esxi()
    ds_map = discover_ds(vcs)
    current = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "vcenters": {v["hostname"]: v["name"] for v in vcs},
        "esxi": {h["hostname"]: h["name"] for h in esxis},
        "ds": list(ds_map.keys())}
    print(f"\nCurrent ({len(vcs)} vCenters, {len(esxis)} ESXi, {len(ds_map)} Datastores):")
    print("  vCenters:")
    for v in vcs:
        st = "DISABLED" if v["status"] != "0" else "OK"
        print(f"    [{v['hostid']}] {v['name']} ({v['hostname']}) [{st}]")
    print("  Datastores:")
    for dn in sorted(ds_map.keys()):
        print(f"    {dn[:45]:45s} -> {ds_map[dn]['vc']}")
    if os.path.exists(_DISC_CACHE):
        try:
            with open(_DISC_CACHE) as f:
                prev = json.load(f)
            print(f"\nDiff from last cache ({prev.get('time','?')}):")
            changes = []
            for k in ["vcenters", "esxi"]:
                cset = set(current.get(k, {}).keys())
                pset = set(prev.get(k, {}).keys())
                added = cset - pset
                removed = pset - cset
                for a in added:
                    changes.append(f"  NEW {k}: {current[k][a]}")
                for r in removed:
                    changes.append(f"  REMOVED {k}: {prev[k][r]}")
            cds = set(current.get("ds", []))
            pds = set(prev.get("ds", []))
            for a in sorted(cds - pds):
                vc_name = ds_map.get(a, {}).get("vc", "?")
                changes.append(f"  NEW Datastore: {a} ({vc_name})")
            for r in sorted(pds - cds):
                changes.append(f"  REMOVED Datastore: {r}")
            if changes:
                for c in changes:
                    print(c)
            else:
                print("  No changes detected")
        except Exception as e:
            print(f"  Cache read error: {e}")
    else:
        print("\nFirst discovery - cache saved for next comparison")
    with open(_DISC_CACHE, "w") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    print(f"Done. Cache -> {_DISC_CACHE}")

def collect_cpu(esxis):
    print("  [CPU] Collecting ESXi CPU usage...")
    cpu = {}
    for h in esxis:
        hid, hn = h["hostid"], h.get("name", "")
        found = False
        for it in _items(hid, {"key_": "cpu.usage"}, 50).get("result", []):
            if "percent" in it.get("name", "").lower() or "perf" in it.get("key_", "").lower():
                vals = _nums7(it["itemid"], it.get("value_type", "0"))
                if vals:
                    s = _stats(vals)
                    s["now"] = round(vals[0], 1)
                    cpu[hn] = s
                    found = True
                    break
        if not found:
            for it in _items(hid, {"name": ["CPU", "cpu"]}, 50).get("result", []):
                if "percent" in it.get("name", "").lower():
                    vals = _nums7(it["itemid"], it.get("value_type", "0"))
                    if vals:
                        s = _stats(vals)
                        s["now"] = round(vals[0], 1)
                        cpu[hn] = s
                        break
    print(f"    -> {len(cpu)} ESXi CPU data points")
    return cpu

def collect_latency(vcs):
    print("  [Latency] Collecting datastore latency...")
    lat = []
    for vc in vcs:
        vcn = vc["name"]
        vcid = vc["hostid"]
        its = _items(vcid, {"key_": "vmware"}, 200)
        for it in its.get("result", []):
            if "latency" not in it.get("name", "").lower():
                continue
            vals = _nums(it["itemid"], it.get("value_type", "0"))
            if not vals:
                continue
            s = _stats(vals)
            rw = "R" if "read" in it["name"].lower() else "W"
            dn = it["name"]
            for pfx in ["VMware: Average read latency of the datastore ", "VMware: Average write latency of the datastore "]:
                if pfx in dn:
                    dn = dn.replace(pfx, "").strip()
                    break
            lat.append({
                "vc": vcn, "ds": dn, "rw": rw,
                "avg": s["avg"], "max": s["max"], "p95": s["p95"]})
    print(f"    -> {len(lat)} latency entries")
    return lat

def collect_capacity(vcs, ds_map):
    print("  [Capacity] Collecting datastore capacity...")
    ds = {}
    for vc in vcs:
        vcid = vc["hostid"]
        its = _items(vcid, {"name": "Free space on datastore"}, 200)
        for it in its.get("result", []):
            nm = it["name"]
            dn = None
            for pfx in ["VMware: Free space on datastore ", "Free space on datastore "]:
                if pfx in nm:
                    dn = nm.replace(pfx, "").replace(" (percentage)", "").strip()
                    break
            if dn is None:
                dn = nm
            if dn and dn not in ds:
                ds[dn] = {
                    "vc": ds_map.get(dn, {}).get("vc", vc["name"]),
                    "pct": float(it.get("lastvalue", "0")),
                    "total": 0, "iid": it["itemid"],
                    "vt": it.get("value_type", "0")}
    for vc in vcs:
        its = _items(vc["hostid"], {"name": "Total size of datastore"}, 200)
        for it in its.get("result", []):
            nm = it["name"]
            dn = None
            for pfx in ["VMware: Total size of datastore ", "Total size of datastore "]:
                if pfx in nm:
                    dn = nm.replace(pfx, "").strip()
                    break
            if dn is None:
                dn = nm
            if dn in ds:
                ds[dn]["total"] = float(it.get("lastvalue", "0"))
    for dn, d in ds.items():
        if d["iid"]:
            vals = _nums(d["iid"], d["vt"])
            if len(vals) > 5:
                r10 = sum(vals[:10]) / 10
                e10 = sum(vals[-10:]) / 10
                ch = r10 - e10
                d["dir"] = "down" if ch < -0.3 else ("up" if ch > 0.3 else "stable")
                d["chg"] = round(ch, 1)
    print(f"    -> {len(ds)} datastores")
    return ds

def collect_memory(esxis):
    print("  [Memory] Collecting ESXi memory usage...")
    mem = {}
    for h in esxis:
        hid, hn = h["hostid"], h.get("name", "")
        # memory usage percent
        pct_iid = None
        can_calc = False
        for it in _items(hid, {"key_": "vmware.mem.usage"}, 30).get("result", []):
            if it.get("key_", "").startswith("vmware.mem.usage"):
                pct_iid = it["itemid"]
                pct_vt = it.get("value_type", "0")
                break
        if pct_iid is None:
            for it in _items(hid, {"name": "Vmware: Hv.mem.usage"}, 30).get("result", []):
                pct_iid = it["itemid"]
                pct_vt = it.get("value_type", "0")
                break
        # total & used memory (bytes)
        total = used = 0
        for it in _items(hid, {"name": "Total memory"}, 30).get("result", []):
            if "total memory" in it.get("name", "").lower():
                try: total = float(it.get("lastvalue", 0))
                except: total = 0
                break
        for it in _items(hid, {"name": "Used memory"}, 30).get("result", []):
            if "used memory" in it.get("name", "").lower():
                try: used = float(it.get("lastvalue", 0))
                except: used = 0
                break
        # stats for usage percent
        if pct_iid:
            vals = _nums7(pct_iid, pct_vt)
            if vals:
                s = _stats(vals)
                s["now"] = round(vals[0], 1)
                s["total_gb"] = round(total / 1073741824, 1) if total else 0
                s["used_gb"] = round(used / 1073741824, 1) if used else 0
                # fallback: compute percent from total/used if no pct history
                if not s["avg"] and total:
                    s["avg"] = s["max"] = s["min"] = s["p95"] = s["now"] = round(used / total * 100, 1)
                # usage trend: compare recent vs earlier segment (same as storage)
                if len(vals) > 5:
                    r10 = sum(vals[:10]) / 10
                    e10 = sum(vals[-10:]) / 10
                    ch = r10 - e10
                    s["dir"] = "down" if ch < -0.3 else ("up" if ch > 0.3 else "stable")
                    s["chg"] = round(ch, 1)
                mem[hn] = s
    print(f"    -> {len(mem)} ESXi memory data points")
    return mem

def collect_all():
    print("=== COLLECTION START ===")
    if not _login():
        return None
    print("LOGIN OK")
    vcs = discover_vcenters()
    esxis = discover_esxi()
    ds_map = discover_ds(vcs)
    print(f"Found: {len(vcs)} vCenters, {len(esxis)} ESXi, {len(ds_map)} Datastores")
    cpu = collect_cpu(esxis)
    mem = collect_memory(esxis)
    lat = collect_latency(vcs)
    cap = collect_capacity(vcs, ds_map)
    data = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cpu": cpu, "memory": mem, "latency": lat, "capacity": cap}
    with open(_CACHE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("=== COLLECTION DONE ===")
    return data

def llm_analyze(data):
    if not _L_URL:
        print("  [LLM] LLM_API_URL not set, skipping analysis")
        return ""
    print("  [LLM] Sending data to LLM for analysis...")
    prompt = f"""You are a Vmware Expert. 
Analyze the following Zabbix monitoring data and provide:
1. Overall health status
2. Any anomalies or concerns
3. Recommendations

Data collected at: {data.get('time', 'N/A')}

=== CPU USAGE (7-day avg, max, min, p95, current) ===
{chr(10).join(f"{h}: avg={d['avg']}% max={d['max']}% min={d['min']}% p95={d['p95']}% now={d['now']}%" for h, d in sorted(data.get('cpu', {}).items(), key=lambda x: -x[1]['avg'])) if data.get('cpu') else 'No CPU data'}

=== MEMORY USAGE (7-day avg, max, min, p95, current, used/total) ===
{chr(10).join(f"{h}: avg={d['avg']}% max={d['max']}% min={d['min']}% p95={d['p95']}% now={d['now']}% (used {d.get('used_gb',0)}G/{d.get('total_gb',0)}G)" for h, d in sorted(data.get('memory', {}).items(), key=lambda x: -x[1]['avg'])) if data.get('memory') else 'No memory data'}

=== STORAGE LATENCY (datastores with avg > 2ms) ===
{chr(10).join(f"{x['vc']}/{x['ds']}: {x['rw']} avg={x['avg']}ms max={x['max']}ms p95={x['p95']}ms" for x in sorted([x for x in data.get('latency', []) if x['avg'] > 2], key=lambda x: -x['avg'])) if any(x['avg'] > 2 for x in data.get('latency', [])) else 'All latency normal'}

=== STORAGE CAPACITY ===
{chr(10).join(f"{dn}: {d['vc']} total={d.get('total',0)/(1073741824*1024):.1f}TB free={d['pct']}% trend={d.get('dir','?')} ({d.get('chg','?')})" for dn, d in sorted(data.get('capacity', {}).items(), key=lambda x: x[1]['pct'])) if data.get('capacity') else 'No capacity data'}

Please provide concise analysis in Chinese, max 500 words."""
    headers = {"Content-Type": "application/json"}
    if _L_KEY:
        headers["Authorization"] = f"Bearer {_L_KEY}"
    payload = {
        "model": _L_MODEL if _L_MODEL else None,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": _L_MAX,
        "temperature": 0.3}
    # Remove None model
    if payload["model"] is None:
        del payload["model"]
    try:
        url = _L_URL.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        r = requests.post(url, json=payload, headers=headers, timeout=_L_TO)
        res = r.json()
        if "choices" in res and len(res["choices"]) > 0:
            text = res["choices"][0]["message"]["content"]
            print(f"  [LLM] Analysis received ({len(text)} chars)")
            return text
        else:
            print(f"  [LLM] Unexpected response: {res}")
            return ""
    except Exception as e:
        print(f"  [LLM] Error: {e}")
        return ""

def gen_html(data, llm_text=""):
    t = time.strftime("%Y-%m-%d %H:%M")
    cpu = data.get("cpu", {})
    lat = data.get("latency", [])
    cap = data.get("capacity", {})
    cr = ""
    for h, s in sorted(cpu.items(), key=lambda x: -x[1]["avg"]):
        c = "red" if s["avg"] > 30 else ("orange" if s["avg"] > 15 else "green")
        cr += f"<tr><td>{h}</td><td align=center style=color:{c}><b>{s['avg']}%</b></td><td align=center>{s['max']}%</td><td align=center>{s['min']}%</td><td align=center>{s['p95']}%</td><td align=center>{s['now']}%</td></tr>"
    chi = f"<h3>CPU 占用率 (7d)</h3><table border=1 cellspacing=0 cellpadding=5 style='border-collapse:collapse;width:100%'><tr bgcolor=#2c3e50 style=color:white><th>ESXi</th><th>Avg</th><th>Max</th><th>Min</th><th>P95</th><th>Now</th></tr>{cr}</table>" if cr else "<p>No CPU data</p>"
    mh = data.get("memory", {})
    mr = ""
    for h, s in sorted(mh.items(), key=lambda x: -x[1]["avg"]):
        c = "red" if s["avg"] > 80 else ("orange" if s["avg"] > 70 else "green")
        di = s.get("dir", "?")
        ch = f"({s.get('chg','?')}%)" if "chg" in s else ""
        mr += f"<tr><td>{h}</td><td align=center style=color:{c}><b>{s['avg']}%</b></td><td align=center>{s['max']}%</td><td align=center>{s['min']}%</td><td align=center>{s['p95']}%</td><td align=center>{s['now']}%</td><td align=center>{s.get('used_gb',0)}G</td><td align=center>{s.get('total_gb',0)}G</td><td align=center>{di}{ch}</td></tr>"
    mhi = f"<h3>Memory 使用率 (7d)</h3><table border=1 cellspacing=0 cellpadding=5 style='border-collapse:collapse;width:100%'><tr bgcolor=#2c3e50 style=color:white><th>ESXi</th><th>Avg</th><th>Max</th><th>Min</th><th>P95</th><th>Now</th><th>Used</th><th>Total</th><th>Trend</th></tr>{mr}</table>" if mr else "<p>No memory data</p>"
    high = [x for x in lat if x["avg"] > 2]
    high.sort(key=lambda x: -x["avg"])
    lr = ""
    for x in high[:20]:
        c = "red" if x["avg"] > 10 else ("orange" if x["avg"] > 5 else "#b7950b")
        lr += f"<tr><td>{x['vc']}</td><td>{x['ds'][:30]}</td><td align=center>{x['rw']}</td><td align=center style=color:{c}><b>{x['avg']}ms</b></td><td align=center>{x['max']}ms</td><td align=center>{x['p95']}ms</td></tr>"
    lhi = f"<h3>Storage 延迟 (>2ms)</h3><table border=1 cellspacing=0 cellpadding=5 style='border-collapse:collapse;width:100%'><tr bgcolor=#2c3e50 style=color:white><th>vCenter</th><th>Datastore</th><th>R/W</th><th>Avg</th><th>Max</th><th>P95</th></tr>{lr}</table>" if lr else "<p>All latency normal</p>"
    capr = ""
    for dn, d in sorted(cap.items(), key=lambda x: x[1]["pct"]):
        tb = d["total"] / 1073741824 / 1024
        fb = tb * d["pct"] / 100
        ub = tb - fb
        unit, mul = ("GB", 1024) if tb < 1 else ("TB", 1)
        ut, uu, uf = tb * mul, ub * mul, fb * mul
        c = "red" if d["pct"] < 30 else ("orange" if d["pct"] < 50 else "green")
        di = d.get("dir", "?")
        ch = f"({d.get('chg','?')}%)" if "chg" in d else ""
        capr += f"<tr><td>{dn[:30]}</td><td>{d['vc']}</td><td align=center>{round(ut,1)}{unit}</td><td align=center>{round(uu,1)}{unit}</td><td align=center style=color:{c}><b>{round(d['pct'],1)}%</b></td><td align=center>{round(uf,1)}{unit}</td><td align=center>{di}{ch}</td></tr>"
    caphi = f"<h3>Storage 空间</h3><table border=1 cellspacing=0 cellpadding=5 style='border-collapse:collapse;width:100%'><tr bgcolor=#2c3e50 style=color:white><th>Datastore</th><th>vCenter</th><th>Total</th><th>Used</th><th>Free%</th><th>Free</th><th>Trend</th></tr>{capr}</table>"
    # ---------- Alerts: categorized by CPU / Memory / Storage ----------
    def _tblh():
        return f"<table border=1 cellspacing=0 cellpadding=5 style='border-collapse:collapse;width:100%'><tr bgcolor=#2c3e50 style=color:white><th>对象</th><th>级别</th><th>描述</th></tr>"

    # CPU alerts
    cpu_al = ""
    for h, s in sorted(cpu.items(), key=lambda x: -x[1]["avg"]):
        if s["avg"] > 90:
            cpu_al += f"<tr><td>{h}</td><td style=color:red><b>RED</b></td><td>CPU 平均使用率 {s['avg']:.1f}% (峰值 {s['max']:.1f}%)</td></tr>"
        elif s["avg"] > 80:
            cpu_al += f"<tr><td>{h}</td><td style=color:orange><b>YELLOW</b></td><td>CPU 平均使用率 {s['avg']:.1f}% (峰值 {s['max']:.1f}%)</td></tr>"
    cpu_sect = (f"<h4>CPU</h4>{_tblh()}{cpu_al}</table>" if cpu_al
                else "<h4>CPU</h4><p style=color:green>暂无异常</p>")

    # Memory alerts
    mem_al = ""
    for h, s in sorted(mh.items(), key=lambda x: -x[1]["avg"]):
        if s["avg"] > 90:
            mem_al += f"<tr><td>{h}</td><td style=color:red><b>RED</b></td><td>内存平均使用率 {s['avg']:.1f}% (峰值 {s['max']:.1f}%), 资源紧张</td></tr>"
        elif s["avg"] > 85:
            mem_al += f"<tr><td>{h}</td><td style=color:orange><b>YELLOW</b></td><td>内存平均使用率 {s['avg']:.1f}% (峰值 {s['max']:.1f}%), 重点观察</td></tr>"
    mem_sect = (f"<h4>内存</h4>{_tblh()}{mem_al}</table>" if mem_al
                else "<h4>内存</h4><p style=color:green>暂无异常</p>")

    # Storage alerts (capacity + latency)
    sto_al = ""
    for dn, d in sorted(cap.items(), key=lambda x: x[1]["pct"]):
        if d["pct"] < 20:
            sto_al += f"<tr><td>{dn}</td><td style=color:red><b>RED</b></td><td>剩余空间 {d['pct']:.1f}%, 存储空间紧张</td></tr>"
        elif d["pct"] < 30:
            sto_al += f"<tr><td>{dn}</td><td style=color:orange><b>YELLOW</b></td><td>剩余空间 {d['pct']:.1f}%, 重点观察</td></tr>"
    for x in lat:
        if x["avg"] > 10:
            sto_al += f"<tr><td>{x['ds']} ({x['vc']})</td><td style=color:red><b>RED</b></td><td>{x['rw']} 延时 {x['avg']:.1f}ms</td></tr>"
    sto_sect = (f"<h4>存储</h4>{_tblh()}{sto_al}</table>" if sto_al
                else "<h4>存储</h4><p style=color:green>暂无异常</p>")

    alhi = f"<h3>Alerts</h3>{cpu_sect}{mem_sect}{sto_sect}"
    llm_section = ""
    if llm_text:
        llm_section = f"<h3>AI辅助数据分析</h3><div style='background:#f0f8ff;border:1px solid #b8d4e8;border-radius:5px;padding:15px;margin:10px 0;white-space:pre-wrap'>{llm_text}</div>"
    return f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>Zabbix vCenter 日巡检报表 {t}</title>
<style>body{{font-family:Arial,sans-serif;margin:20px;color:#333}}h2{{color:#2c3e50;border-bottom:2px solid #3498db}}table{{font-size:13px;width:100%}}td,th{{padding:5px 8px}}tr:nth-child(even){{background:#f8f9fa}}</style></head><body>
<h2>Zabbix vCenter Daily Report - {t}</h2>
{llm_section}{alhi}{chi}{mhi}{lhi}{caphi}
<p style=color:#999;font-size:12px>Generated at {t} by Zabbix vCenter Daily Report (From Zabbix AI and support dynamic discovery) Auth: Shoulham</p></body></html>"""

def send_mail(html):
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(f"Zabbix vCenter 日巡检报表 {time.strftime('%Y-%m-%d')}", "utf-8")
    msg["From"] = _S_USER
    msg["To"] = ",".join(_S_TO)
    if not _S_PASS:
        print("No SMTP password, skip send")
        return False
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(_S_HOST, _S_PORT, context=ctx) as s:
            s.login(_S_USER, _S_PASS)
            s.send_message(msg)
        print("Email sent OK!")
        return True
    except Exception as e:
        print(f"Send failed: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zabbix vCenter Daily Report")
    parser.add_argument("--discover", action="store_true", help="Run discovery mode")
    parser.add_argument("--no-email", action="store_true", help="Collect but skip email")
    parser.add_argument("--llm", action="store_true", help="Enable LLM analysis")
    args = parser.parse_args()
    if args.discover:
        if not _login():
            sys.exit(1)
        run_discovery()
        sys.exit(0)
    data = collect_all()
    if not data:
        print("Collection failed")
        sys.exit(1)
    llm_text = ""
    if args.llm:
        llm_text = llm_analyze(data)
    html = gen_html(data, llm_text)
    with open("/tmp/zabbix_report.html", "w") as f:
        f.write(html)
    print(f"Report saved to /tmp/zabbix_report.html ({len(html)} chars)")
    # Publish to Zabbix web root so the report page is browsable / embeddable
    # 1) always write the fixed "latest" file; 2) also create a date-stamped copy;
    # 3) archive copies older than RETENTION_DAYS (default 15) into archive/YYYY-MM.tar.gz
    #    (one compressed tarball per month) and remove them from the live report dir,
    #    keeping the dropdown limited to the last half-month while history is preserved.
    try:
        _REPORT_DIR = "/usr/share/zabbix/reports"
        _RET_DAYS = int(os.environ.get("RPT_RETENTION_DAYS", "15"))
        if not os.path.isdir(_REPORT_DIR):
            os.makedirs(_REPORT_DIR, mode=0o755, exist_ok=True)
        os.chmod(_REPORT_DIR, 0o755)
        # fixed latest file
        rp = os.path.join(_REPORT_DIR, "zabbix_report.html")
        with open(rp, "w") as f:
            f.write(html)
        os.chmod(rp, 0o644)
        # date-stamped archive: zabbix_report_YYYY-MM-DD.html
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        ap = os.path.join(_REPORT_DIR, f"zabbix_report_{today}.html")
        with open(ap, "w") as f:
            f.write(html)
        os.chmod(ap, 0o644)
        # Archive: move report_YYYY-MM-DD.html older than RET_DAYS into local compressed
        # archive/YYYY-MM.tar.gz (one tarball per month), instead of deleting them.
        import re as _re, tarfile, io
        _ARCH_DIR = os.path.join(_REPORT_DIR, "archive")
        cutoff = datetime.now() - timedelta(days=_RET_DAYS)
        stale = {}  # month(YYYY-MM) -> list of (date_str, filepath)
        for fn in os.listdir(_REPORT_DIR):
            m = _re.match(r"^zabbix_report_(\d{4}-\d{2}-\d{2})\.html$", fn)
            if not m:
                continue
            try:
                fdate = datetime.strptime(m.group(1), "%Y-%m-%d")
            except ValueError:
                continue
            if fdate < cutoff:
                month = fdate.strftime("%Y-%m")
                stale.setdefault(month, []).append((m.group(1), os.path.join(_REPORT_DIR, fn)))
        if stale:
            os.makedirs(_ARCH_DIR, mode=0o755, exist_ok=True)
            os.chmod(_ARCH_DIR, 0o755)
            for month, files in stale.items():
                tarball = os.path.join(_ARCH_DIR, f"zabbix_report_{month}.tar.gz")
                # Python 3.6 tarfile has no append+gzip mode; rebuild the month tarball:
                # read existing members (if any), add the new files, rewrite whole archive.
                entries = {}  # name -> bytes
                if os.path.isfile(tarball):
                    try:
                        with tarfile.open(tarball, "r:gz") as tf:
                            for mem in tf.getmembers():
                                f = tf.extractfile(mem)
                                if f is not None:
                                    entries[mem.name] = f.read()
                    except Exception:
                        entries = {}
                try:
                    for dstr, fp in files:
                        fname = f"zabbix_report_{dstr}.html"
                        if fname in entries:
                            continue
                        with open(fp, "rb") as fh:
                            data = fh.read()
                        entries[fname] = data
                    tmp = tarball + ".tmp"
                    with tarfile.open(tmp, "w:gz") as tf:
                        for name, data in sorted(entries.items()):
                            ti = tarfile.TarInfo(name)
                            ti.size = len(data)
                            ti.mtime = datetime.strptime(name, "zabbix_report_%Y-%m-%d.html").timestamp()
                            tf.addfile(ti, io.BytesIO(data))
                    os.replace(tmp, tarball)
                    print(f"Archived {len(files)} report(s) -> {tarball}")
                except Exception as aerr:
                    # fallback: gzip each file individually into archive/
                    import gzip
                    for dstr, fp in files:
                        try:
                            with open(fp, "rb") as fh:
                                data = fh.read()
                            gz = os.path.join(_ARCH_DIR, f"zabbix_report_{dstr}.html.gz")
                            with gzip.open(gz, "wb") as gh:
                                gh.write(data)
                            print(f"Archived {dstr} -> {gz}")
                        except Exception:
                            pass
                # remove from live dir regardless (archived or fallback)
                for _dstr, _fp in files:
                    try:
                        os.remove(_fp)
                    except OSError:
                        pass
        print(f"Report published (latest + {today}), retention {_RET_DAYS}d, archived to {_ARCH_DIR}")
    except Exception as e:
        print(f"WARN: failed to publish web report: {e}")
    if not args.no_email:
        send_mail(html)
    else:
        print("--no-email, skip sending")
