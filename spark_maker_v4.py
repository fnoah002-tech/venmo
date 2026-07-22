# ============================================================
#  SPARK MAKER
#  - Asset-Kategorien (Unterordner wie "girl holding money")
#  - Manueller Upload direkt im Browser (Drag & Drop)
#  - Live-Vorschau vor dem Batch
#  - Mehrere Varianten pro Hook
#  http://127.0.0.1:5016
# ============================================================

import os, io, json, time, random, zipfile, subprocess, threading, webbrowser, shutil
from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps
import requests as http

BASE   = os.path.dirname(os.path.abspath(__file__))
import tempfile
# On Railway: use /tmp for rendering, DATA_DIR for persistent JSON
DATA   = os.environ.get("DATA_DIR", BASE)
TMP    = tempfile.gettempdir()
OUT    = os.path.join(TMP, "creatives")
GIRLS  = os.path.join(TMP, "assets", "girls")
PROOFS = os.path.join(TMP, "assets", "proofs")
FONTS  = os.path.join(DATA, "fonts")
PREV   = os.path.join(TMP, "_preview")
CFG    = os.path.join(DATA, "maker_config.json")
LIB    = os.path.join(DATA, "hook_library.json")
OFFERS = os.path.join(DATA, "offers.json")
for d in (OUT, GIRLS, PROOFS, FONTS, PREV): os.makedirs(d, exist_ok=True)

# Cloudflare R2 (optional — only active when env vars are set)
R2_ENABLED = all(os.environ.get(k) for k in
    ["R2_ACCOUNT_ID","R2_ACCESS_KEY","R2_SECRET_KEY","R2_BUCKET"])

def get_r2():
    if not R2_ENABLED: return None
    import boto3
    return boto3.client("s3",
        endpoint_url="https://"+os.environ["R2_ACCOUNT_ID"]+".r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY"],
        aws_secret_access_key=os.environ["R2_SECRET_KEY"],
        region_name="auto")

def r2_upload(local_path, key):
    s3 = get_r2()
    if not s3 or not os.path.exists(local_path): return
    try: s3.upload_file(local_path, os.environ["R2_BUCKET"], key)
    except Exception as e: print(f"R2 upload: {e}")

def r2_download(key, local_path):
    s3 = get_r2()
    if not s3: return False
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        s3.download_file(os.environ["R2_BUCKET"], key, local_path)
        return True
    except Exception: return False

def r2_list(prefix):
    s3 = get_r2()
    if not s3: return []
    try:
        r = s3.list_objects_v2(Bucket=os.environ["R2_BUCKET"], Prefix=prefix)
        return [o["Key"] for o in r.get("Contents", [])]
    except Exception: return []

# Standard-Kategorien: werden beim Start angelegt, damit Nutzer nur noch auswaehlen muessen
DEFAULT_GIRL_CATS = ["girl-holding-money", "girl-in-car", "girl-crying", "girl-smiling",
                     "girl-shopping", "girl-in-bed", "mirror-selfie", "girl-on-phone"]
DEFAULT_PROOF_CATS = ["cashout-screenshots", "game-screenshots", "bank-app"]
for c in DEFAULT_GIRL_CATS:  os.makedirs(os.path.join(GIRLS, c), exist_ok=True)
for c in DEFAULT_PROOF_CATS: os.makedirs(os.path.join(PROOFS, c), exist_ok=True)

app = Flask(__name__)
W, H = 1080, 1920
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
VID_EXT = (".mp4", ".mov", ".m4v", ".webm")
MAX_SETS = 150

def load_cfg():
    try:
        with open(CFG) as f: return json.load(f)
    except Exception: return {}
def save_cfg(c):
    with open(CFG, "w") as f: json.dump(c, f)

# ----------------------------- Schrift -----------------------------

TIKTOK_FONT = os.path.join(FONTS, "Montserrat.ttf")
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf"

def ensure_tiktok_font():
    if os.path.exists(TIKTOK_FONT) and os.path.getsize(TIKTOK_FONT) > 50000:
        return TIKTOK_FONT
    try:
        r = http.get(FONT_URL, timeout=60)
        if r.status_code == 200 and len(r.content) > 50000:
            with open(TIKTOK_FONT, "wb") as f: f.write(r.content)
            return TIKTOK_FONT
    except Exception:
        pass
    return None

FONT_PATHS = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
CJK_FONT_PATHS = ["C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/meiryob.ttc",
                  "C:/Windows/Fonts/msgothic.ttc", "C:/Windows/Fonts/malgunbd.ttf",
                  "C:/Windows/Fonts/malgun.ttf",
                  "/System/Library/Fonts/AppleSDGothicNeo.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                  "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"]

def needs_cjk(t):
    return any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff'
               or '\uac00' <= c <= '\ud7af' for c in t)

def get_font(size, text=""):
    if not needs_cjk(text):
        p = ensure_tiktok_font()
        if p:
            try:
                f = ImageFont.truetype(p, size)
                try: f.set_variation_by_name("SemiBold")
                except Exception: pass
                return f
            except Exception: pass
    for p in (CJK_FONT_PATHS + FONT_PATHS) if needs_cjk(text) else FONT_PATHS:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: continue
    return ImageFont.load_default()

# ----------------------------- Rendern -----------------------------

def cover_fit(img):
    return ImageOps.fit(img.convert("RGB"), (W, H), Image.LANCZOS)

def wrap_lines(draw, text, font, max_w):
    lines = []
    for raw in text.split("\n"):
        cur = ""
        for w_ in raw.split(" "):
            trial = (cur + " " + w_).strip()
            if draw.textlength(trial, font=font) <= max_w: cur = trial
            else:
                if cur: lines.append(cur)
                cur = w_
        lines.append(cur)
    return lines

def draw_text_layer(text, style="outline", position="middle", font_size=64):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = get_font(font_size, text)
    lines = wrap_lines(d, text, font, int(W * 0.82))
    line_h = int(font_size * 1.22)
    total_h = line_h * len(lines)
    y0 = {"top": int(H * 0.16), "middle": (H - total_h) // 2,
          "bottom": int(H * 0.74) - total_h}[position]
    stroke = max(2, round(font_size / 14))
    for i, line in enumerate(lines):
        lw = d.textlength(line, font=font)
        x, y = (W - lw) / 2, y0 + i * line_h
        if style == "box":
            px, py = 22, 10
            d.rounded_rectangle([x - px, y - py, x + lw + px, y + font_size + py],
                                radius=10, fill=(255, 255, 255, 240))
            d.text((x, y), line, font=font, fill=(22, 22, 22, 255))
        else:
            d.text((x, y + 2), line, font=font, fill=(0, 0, 0, 70),
                   stroke_width=stroke + 1, stroke_fill=(0, 0, 0, 70))
            d.text((x, y), line, font=font, fill=(255, 255, 255, 255),
                   stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    return layer

def render_image(path, text, style, position, font_size, out_path):
    with open(path, "rb") as fh:
        base = cover_fit(Image.open(io.BytesIO(fh.read())))
    if text.strip():
        base = Image.alpha_composite(base.convert("RGBA"),
               draw_text_layer(text, style, position, font_size)).convert("RGB")
    base.save(out_path, quality=92)

def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception: return None

def render_video(path, text, style, position, font_size, out_path):
    ff = get_ffmpeg()
    if not ff: return "Video support missing (imageio-ffmpeg not installed)"
    ov = out_path + ".ov.png"
    draw_text_layer(text, style, position, font_size).save(ov)
    r = subprocess.run([ff, "-y", "-i", path, "-i", ov, "-filter_complex",
        "[0]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[v];[v][1]overlay=0:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy", "-movflags", "+faststart", out_path], capture_output=True, text=True)
    try: os.remove(ov)
    except OSError: pass
    return None if r.returncode == 0 else (r.stderr or "ffmpeg failed")[-300:]

# ----------------------------- Assets -----------------------------

def scan(root, exts):
    """{kategorie: [relativer pfad, ...]} — Dateien direkt im Ordner = 'unsorted'."""
    cats = {}
    loose = [f for f in os.listdir(root)
             if f.lower().endswith(exts) and os.path.isfile(os.path.join(root, f))]
    if loose: cats["unsorted"] = sorted(loose)
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            cats[d] = [os.path.join(d, f).replace("\\", "/") for f in sorted(os.listdir(p))
                       if f.lower().endswith(exts)]
    return cats

def safe_name(s):
    keep = "-_ abcdefghijklmnopqrstuvwxyz0123456789"
    s = "".join(c for c in s.strip().lower() if c in keep).strip()
    return s.replace(" ", "-")[:40] or "new-folder"

@app.route("/api/assets")
def api_assets():
    g = scan(GIRLS, IMG_EXT + VID_EXT)
    p = scan(PROOFS, IMG_EXT)
    return jsonify({
        "girls":  [{"name": k, "count": len(v), "sample": v[0] if v else None} for k, v in g.items()],
        "proofs": [{"name": k, "count": len(v), "sample": v[0] if v else None} for k, v in p.items()],
        "girls_path": GIRLS, "proofs_path": PROOFS})

@app.route("/thumb/<kind>/<path:rel>")
def thumb(kind, rel):
    root = GIRLS if kind == "girls" else PROOFS
    full = os.path.join(root, rel)
    if not os.path.abspath(full).startswith(os.path.abspath(root)) or not os.path.exists(full):
        return "", 404
    if full.lower().endswith(VID_EXT):
        return "", 204
    im = Image.open(full).convert("RGB")
    im.thumbnail((160, 280))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=80); buf.seek(0)
    return send_file(buf, mimetype="image/jpeg")

@app.route("/api/upload", methods=["POST"])
def api_upload():
    kind = request.form.get("kind", "girls")
    root = GIRLS if kind == "girls" else PROOFS
    cat = request.form.get("category", "").strip()
    target = os.path.join(root, safe_name(cat)) if cat and cat != "unsorted" else root
    os.makedirs(target, exist_ok=True)
    exts = (IMG_EXT + VID_EXT) if kind == "girls" else IMG_EXT
    saved, skipped = 0, 0
    for f in request.files.getlist("files"):
        name = os.path.basename(f.filename or "")
        if not name.lower().endswith(exts):
            skipped += 1; continue
        dest = os.path.join(target, name)
        stem, ext = os.path.splitext(name); n = 1
        while os.path.exists(dest):
            dest = os.path.join(target, f"{stem}-{n}{ext}"); n += 1
        f.save(dest); saved += 1
    return jsonify({"saved": saved, "skipped": skipped,
                    "folder": os.path.basename(target) if target != root else "unsorted"})


# ----------------------------- Hook-Bibliothek -----------------------------

def load_lib():
    try:
        with open(LIB, encoding="utf-8") as f: d = json.load(f)
    except Exception: d = {}
    d.setdefault("winners", []); d.setdefault("losers", [])
    return d

def save_lib(d):
    with open(LIB, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)

@app.route("/api/rate", methods=["POST"])
def api_rate():
    b = request.get_json()
    text = (b.get("text") or "").strip()
    if not text: return jsonify({"error": "empty"}), 400
    lib = load_lib()
    key = "winners" if b.get("rating") == "up" else "losers"
    other = "losers" if key == "winners" else "winners"
    lib[other] = [x for x in lib[other] if x.get("text") != text]
    if not any(x.get("text") == text for x in lib[key]):
        lib[key].append({"text": text, "lang": b.get("lang", ""), "at": int(time.time())})
    lib[key] = lib[key][-300:]
    save_lib(lib)
    return jsonify({"winners": len(lib["winners"]), "losers": len(lib["losers"])})

@app.route("/api/library")
def api_library():
    lib = load_lib()
    return jsonify({"winners": [x["text"] for x in lib["winners"]][::-1],
                    "losers":  [x["text"] for x in lib["losers"]][::-1]})

@app.route("/api/library/remove", methods=["POST"])
def api_library_remove():
    b = request.get_json(); lib = load_lib()
    for k in ("winners", "losers"):
        lib[k] = [x for x in lib[k] if x.get("text") != b.get("text")]
    save_lib(lib)
    return jsonify({"winners": len(lib["winners"]), "losers": len(lib["losers"])})


# ----------------------------- Performance / Metrics -----------------------------

PERF = os.path.join(BASE, "performance.json")
HREG = os.path.join(BASE, "hook_registry.json")  # id -> {hook, angle, slide2}

def load_perf():
    try:
        with open(PERF, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_perf(d):
    with open(PERF, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)

def load_registry():
    try:
        with open(HREG, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_registry(d):
    with open(HREG, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)

@app.route("/api/registry")
def api_registry():
    return jsonify(load_registry())


@app.route("/api/perf/upload", methods=["POST"])
def perf_upload():
    """CSV vom TikTok Ads Manager einlesen. Gibt Spalten + Zeilen zurueck."""
    f = request.files.get("csv")
    if not f: return jsonify({"error": "No file received."}), 400
    import csv, io as _io
    text = f.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(_io.StringIO(text))
    rows = [r for r in reader if any((v or '').strip() for v in r.values())]
    if not rows: return jsonify({"error": "CSV is empty or wrong format."}), 400
    cols = list(rows[0].keys())
    return jsonify({"cols": cols, "rows": rows[:500], "total": len(rows)})

@app.route("/api/perf/save", methods=["POST"])
def perf_save():
    """
    Speichert verknuepfte Performance-Daten.
    body: {
      epc_ios: float, epc_android: float,
      mappings: [{hook_text, spend, clicks_ios, clicks_android, angle}]
    }
    """
    b = request.get_json()
    perf = load_perf()
    lib  = load_lib()
    auto_good, auto_bad = [], []
    reg = load_registry()
    for m in b.get("mappings", []):
        hook = (m.get("hook_text") or "").strip()
        if not hook: continue
        spend       = float(m.get("spend") or 0)
        revenue     = float(m.get("revenue") or 0)
        conversions = float(m.get("conversions") or 0)
        clicks      = float(m.get("clicks") or 0)
        roi         = round((revenue - spend) / spend * 100, 1) if spend > 0 else 0
        cpa         = round(spend / conversions, 2) if conversions > 0 else 0
        rpc         = round(revenue / clicks, 4) if clicks > 0 else 0
        angle = (m.get("angle") or "").strip()
        if not angle:
            for rid, rdata in reg.items():
                if rdata.get("hook","").strip().lower() == hook.lower():
                    angle = rdata.get("angle","")
                    break
        entry = {"hook": hook, "angle": angle, "run": m.get("run_name",""),
                 "spend": spend, "revenue": revenue, "conversions": int(conversions),
                 "clicks": int(clicks), "roi": roi, "cpa": cpa, "rpc": rpc,
                 "at": int(time.time())}
        perf[hook] = entry
        # Auto-bewertung: mind. 20€ Spend fuer valide Aussage
        if spend >= 20:
            existing = [v["roi"] for v in perf.values() if v.get("roi") is not None and v.get("spend",0) >= 20]
            if len(existing) >= 2:
                median_roi = sorted(existing)[len(existing)//2]
                if roi >= median_roi + 15:
                    auto_good.append(hook)
                elif roi <= median_roi - 20:
                    auto_bad.append(hook)
    save_perf(perf)
    # Auto-Bibliothek updaten
    for hook in auto_good:
        lib["losers"] = [x for x in lib["losers"] if x.get("text") != hook]
        if not any(x.get("text") == hook for x in lib["winners"]):
            lib["winners"].append({"text": hook, "lang": "", "at": int(time.time()), "auto": True})
    for hook in auto_bad:
        lib["winners"] = [x for x in lib["winners"] if x.get("text") != hook]
        if not any(x.get("text") == hook for x in lib["losers"]):
            lib["losers"].append({"text": hook, "lang": "", "at": int(time.time()), "auto": True})
    save_lib(lib)
    return jsonify({"saved": len(b.get("mappings",[])), "auto_good": auto_good, "auto_bad": auto_bad})

@app.route("/api/perf")
def perf_get():
    perf = load_perf()
    rows = sorted(perf.values(), key=lambda x: x.get("cpa") or 9999)
    return jsonify(rows)


# ----------------------------- Offer-Bibliothek -----------------------------

OFFERS = os.path.join(BASE, "offers.json")

def load_offers():
    try:
        with open(OFFERS, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_offers(d):
    with open(OFFERS, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)

@app.route("/api/offers")
def api_offers():
    return jsonify(load_offers())

@app.route("/api/offers/save", methods=["POST"])
def api_offers_save():
    b = request.get_json()
    offers = load_offers()
    name = b.get("name","").strip()
    if not name: return jsonify({"error": "Name required"}), 400
    offers[name] = {"epc_ios": float(b.get("epc_ios") or 0), "epc_android": float(b.get("epc_android") or 0)}
    save_offers(offers)
    return jsonify({"saved": name, "offers": offers})

@app.route("/api/offers/delete", methods=["POST"])
def api_offers_delete():
    b = request.get_json()
    offers = load_offers()
    offers.pop(b.get("name",""), None)
    save_offers(offers)
    return jsonify({"offers": offers})


# ----------------------------- Everflow / MABAC API -----------------------------

@app.route("/api/everflow/test", methods=["POST"])
def everflow_test():
    b = request.get_json()
    key = (b.get("api_key") or "").strip()
    if not key: return jsonify({"error": "No API key provided"}), 400
    cfg = load_cfg(); cfg["everflow_key"] = key; save_cfg(cfg)
    try:
        import datetime
        today = datetime.date.today().isoformat()
        r = http.post("https://api.eflow.team/v1/affiliates/reporting/entity",
            headers={"Content-Type": "application/json", "x-eflow-api-key": key},
            json={"timezone_id": 80, "currency_id": "USD", "from": today, "to": today,
                  "columns": [{"column": "offer"}],
                  "query": {"filters": [], "exclusions": [], "metric_filters": [],
                            "user_metrics": [], "settings": {}}}, timeout=30)
        d = r.json()
        if "table" not in d:
            return jsonify({"error": d.get("message", str(d)[:200])}), 400
        return jsonify({"ok": True, "rows": len(d["table"])})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/everflow/fetch", methods=["POST"])
def everflow_fetch():
    """Fetch clicks + revenue from Everflow for a date range, split by OS."""
    b = request.get_json()
    cfg = load_cfg()
    key = b.get("api_key") or cfg.get("everflow_key", "")
    if not key: return jsonify({"error": "No Everflow API key saved"}), 400

    date_from = b.get("from", "")
    date_to   = b.get("to", "")
    if not date_from or not date_to:
        return jsonify({"error": "Provide from and to dates"}), 400

    base_payload = {
        "timezone_id": 80, "currency_id": "USD",
        "from": date_from, "to": date_to,
        "query": {"filters": [], "exclusions": [], "metric_filters": [],
                  "user_metrics": [], "settings": {}}
    }

    try:
        # Total clicks + revenue (no OS split)
        p_total = {**base_payload, "columns": [{"column": "offer"}]}
        r_total = http.post("https://api.eflow.team/v1/affiliates/reporting/entity",
            headers={"Content-Type": "application/json", "x-eflow-api-key": key},
            json=p_total, timeout=30).json()
        if "table" not in r_total:
            return jsonify({"error": r_total.get("message", "API error")}), 400

        # OS split
        p_os = {**base_payload, "columns": [{"column": "os_version"}]}
        r_os = http.post("https://api.eflow.team/v1/affiliates/reporting/entity",
            headers={"Content-Type": "application/json", "x-eflow-api-key": key},
            json=p_os, timeout=30).json()

        # Parse totals
        # Sum ALL offer rows (not just first)
        total_clicks  = sum(row.get("reporting", {}).get("total_click", 0) for row in r_total["table"])
        total_revenue = sum(row.get("reporting", {}).get("revenue", 0) for row in r_total["table"])
        total_cv      = sum(row.get("reporting", {}).get("cv", 0) for row in r_total["table"])
        rpc           = round(total_revenue / total_clicks, 4) if total_clicks > 0 else 0

        # Parse OS split — sum all rows per OS
        ios_clicks = 0; android_clicks = 0
        if "table" in r_os:
            for row in r_os["table"]:
                label = (row.get("columns") or [{}])[0].get("label","").lower()
                clicks = row.get("reporting", {}).get("total_click", 0)
                if "ios" in label or "iphone" in label or "ipad" in label:
                    ios_clicks += clicks
                elif "android" in label:
                    android_clicks += clicks

        if ios_clicks + android_clicks == 0:
            ios_clicks = total_clicks

        return jsonify({
            "total_clicks": total_clicks,
            "ios_clicks": ios_clicks,
            "android_clicks": android_clicks,
            "conversions": total_cv,
            "revenue": total_revenue,
            "rpc": rpc,
            "from": date_from,
            "to": date_to
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/everflow/keycheck")
def everflow_keycheck():
    cfg = load_cfg()
    return jsonify({"has_key": bool(cfg.get("everflow_key"))})

# ----------------------------- Hooks (KI) -----------------------------

def build_prompt(n, lang, desc, examples="", angle="mix"):
    ANGLES = {"mix": "Mix the angles freely.",
      "friend": "All from a friend's point of view, noticing or commenting on something.",
      "secret": "All in a 'I'm not telling anyone my secret' style.",
      "reaction": "All as a quote or reaction from someone else.",
      "boredom": "All about boredom / killing time / being on your phone in bed at night."}
    ex = ""
    if examples.strip():
        ex += ("\n\nTHESE ARE MY BEST-PERFORMING HOOKS — copy this exact tone, "
               "sentence structure and length:\n" + examples.strip() + "\n")
    lib = load_lib()
    if lib["winners"]:
        picks = random.sample(lib["winners"], min(8, len(lib["winners"])))
        ex += ("\n\nHOOKS I RATED GOOD — match this quality and voice, but do NOT reuse them:\n"
               + "\n".join(p["text"] for p in picks) + "\n")
    if lib["losers"]:
        picks = random.sample(lib["losers"], min(6, len(lib["losers"])))
        ex += ("\n\nHOOKS I REJECTED — avoid this style, wording and sentence shape completely:\n"
               + "\n".join(p["text"] for p in picks) + "\n")
    return (
      f"You write hook text for TikTok slideshow ads. Write everything in {lang} "
      f"(natural, native-sounding, how real people post — not translated-sounding).\n"
      f"Context: {desc}.\n"
      f"Produce {n} PAIRS. Each pair is:\n"
      f"  SLIDE 1 = the hook (max 11 words). Curiosity gap, sounds like a real organic post, "
      f"not an ad. It must NOT reveal what the thing is.\n"
      f"  SLIDE 2 = the payoff (max 8 words), matching the hook, casual and offhand.\n"
      f"{ANGLES.get(angle, ANGLES['mix'])}\n"
      f"RULES: no specific money amounts, no income promises, no emojis, no numbering, "
      f"no hashtags, no ad language. Lowercase is fine. Every pair clearly different.\n"
      f"{ex}\n"
      f"OUTPUT FORMAT — exactly one line per triplet, separated by ||, nothing else:\n"
      f"hook for slide 1 || text for slide 2 || angle tag\n"
      f"The angle tag is ONE short label (2-4 words, lowercase, no quotes) that describes the core angle, e.g.: friend notices, secret income, boredom win, job comparison, reaction quote, family discovers, mirror selfie flex")

def call_gemini(key, prompt):
    cfg = load_cfg(); cached = cfg.get("gemini_model")
    r = http.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200", timeout=30).json()
    if "models" not in r:
        raise RuntimeError(r.get("error", {}).get("message", str(r)[:200]))
    usable = [m["name"].split("/")[-1] for m in r["models"]
              if "generateContent" in m.get("supportedGenerationMethods", [])]
    def score(nm):
        s = 0
        if "flash" in nm: s += 100
        if "lite" in nm: s -= 5
        if any(x in nm for x in ("image", "tts", "audio", "embedding", "live", "veo", "imagen")): s -= 1000
        if any(x in nm for x in ("preview", "exp")): s -= 20
        import re
        m = re.search(r"(\d+)\.?(\d*)", nm)
        if m: s += int(m.group(1)) * 10 + int(m.group(2) or 0)
        return s
    order = ([cached] if cached else []) + [m for m in sorted(usable, key=score, reverse=True) if m != cached]
    last = "no model tried"
    for model in order[:10]:
        rr = http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                       json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60).json()
        if "candidates" in rr:
            cfg["gemini_model"] = model; save_cfg(cfg)
            return "".join(p.get("text", "") for p in rr["candidates"][0]["content"]["parts"])
        last = f"{model}: " + rr.get("error", {}).get("message", str(rr)[:150])
    raise RuntimeError("All models rejected. Last error — " + last)

PROVIDERS = {"gemini": call_gemini}

@app.route("/api/keystatus")
def keystatus():
    return jsonify({"keys": {p: bool(load_cfg().get("keys", {}).get(p)) for p in PROVIDERS}})

@app.route("/api/hooks", methods=["POST"])
def api_hooks():
    b = request.get_json(); cfg = load_cfg()
    provider = b.get("provider", "gemini")
    keys = cfg.get("keys", {})
    key = (b.get("api_key") or keys.get(provider) or "").strip()
    if not key:
        return jsonify({"error": "Add an API key for this provider first."}), 400
    keys[provider] = key; cfg["keys"] = keys; save_cfg(cfg)
    try:
        text = PROVIDERS[provider](key, build_prompt(
            max(1, min(int(b.get("n", 20)), 60)), b.get("language", "English"),
            b.get("desc", ""), b.get("examples", ""), b.get("angle", "mix")))
        raw = [h.strip().lstrip("-*0123456789. ").strip() for h in text.split("\n") if h.strip()]
        parsed = []
        for line in raw:
            if len(line) < 4: continue
            parts = [p.strip() for p in line.split("||")]
            parsed.append({
                "slide1": parts[0] if len(parts) > 0 else line,
                "slide2": parts[1] if len(parts) > 1 else "",
                "angle": parts[2] if len(parts) > 2 else ""
            })
        return jsonify({"hooks": parsed})
    except Exception as e:
        return jsonify({"error": f"{provider}: {e}"}), 400

# ----------------------------- Vorschau + Batch -----------------------------

def pools(girl_cats, proof_cats):
    g = scan(GIRLS, IMG_EXT + VID_EXT); p = scan(PROOFS, IMG_EXT)
    gsel = [f for k, v in g.items() if not girl_cats or k in girl_cats for f in v]
    psel = [f for k, v in p.items() if not proof_cats or k in proof_cats for f in v]
    return gsel, psel

@app.route("/api/preview", methods=["POST"])
def api_preview():
    b = request.get_json()
    gsel, psel = pools(b.get("girl_cats", []), b.get("proof_cats", []))
    imgs = [f for f in gsel if f.lower().endswith(IMG_EXT)]
    if not imgs:
        return jsonify({"error": "No image assets in the selected folders. "
                                "Preview needs at least one photo (videos can't be previewed)."}), 400
    style, pos = b.get("style", "outline"), b.get("position", "middle")
    fs = int(b.get("font_size", 64))
    shutil.rmtree(PREV, ignore_errors=True); os.makedirs(PREV, exist_ok=True)
    stamp = str(int(time.time()))
    out = []
    render_image(os.path.join(GIRLS, random.choice(imgs)), b.get("hook", "your hook goes here") + " 😢",
                 style, pos, fs, os.path.join(PREV, f"a{stamp}.jpg"))
    out.append(f"_preview/a{stamp}.jpg")
    if psel:
        _prev_s2 = (b.get("slide2","") + " 😢").strip() if b.get("slide2","").strip() else "😢"
        render_image(os.path.join(PROOFS, random.choice(psel)), _prev_s2,
                     style, "bottom" if pos == "middle" else pos, fs,
                     os.path.join(PREV, f"b{stamp}.jpg"))
        out.append(f"_preview/b{stamp}.jpg")
    return jsonify({"files": out})

@app.route("/api/batch", methods=["POST"])
def api_batch():
    b = request.get_json()
    lines = [h.strip() for h in b.get("hooks", "").split("\n") if h.strip()]
    batch_angle = b.get("batch_angle", "").strip()
    if not lines: return jsonify({"error": "Add at least one hook line."}), 400
    gsel, psel = pools(b.get("girl_cats", []), b.get("proof_cats", []))
    if not gsel:
        return jsonify({"error": "No assets in the selected girl folders. "
                                "Upload files or pick another folder."}), 400
    variants = max(1, min(int(b.get("variants", 1)), 10))
    style, pos = b.get("style", "outline"), b.get("position", "middle")
    fs = int(b.get("font_size", 64))
    jobs = []
    for line in lines:
        parts = [p.strip() for p in line.split("||")]
        hook = parts[0] if len(parts) > 0 else line
        s2   = parts[1] if len(parts) > 1 else ""
        hook_angle = batch_angle or (parts[2] if len(parts) > 2 else "")
        # hook_angle is stored only, not rendered on slide
        for _ in range(variants):
            jobs.append((hook, s2))
    jobs = jobs[:MAX_SETS]

    random.shuffle(gsel); random.shuffle(psel)
    ts = time.strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(OUT, ts); os.makedirs(folder, exist_ok=True)
    files, errors = [], []
    registry = load_registry()

    for i, (hook, s2) in enumerate(jobs):
        girl  = gsel[i % len(gsel)]
        proof = psel[i % len(psel)] if psel else None
        n = i + 1
        try:
            gpath = os.path.join(GIRLS, girl)
            if girl.lower().endswith(VID_EXT):
                f1 = f"set{n:03d}_slide1.mp4"
                err = render_video(gpath, hook + " 😢", style, pos, fs, os.path.join(folder, f1))
                if err: errors.append(f"Set {n} ({os.path.basename(girl)}): {err}"); continue
            else:
                f1 = f"set{n:03d}_slide1.jpg"
                render_image(gpath, hook + " 😢", style, pos, fs, os.path.join(folder, f1))
            set_id = f"SM-{ts}-{n:03d}"
            registry[set_id] = {"hook": hook, "angle": hook_angle, "slide2": s2, "ts": ts}
            files.append(f"{ts}/{f1}")
            if proof:
                f2 = f"set{n:03d}_slide2.jpg"
                s2_text = (s2 + " 😢").strip() if s2.strip() else "😢"
                render_image(os.path.join(PROOFS, proof), s2_text, style,
                             "bottom" if pos == "middle" else pos, fs, os.path.join(folder, f2))
                files.append(f"{ts}/{f2}")
        except Exception as e:
            errors.append(f"Set {n}: {e}")

    zip_name = f"{ts}/all_creatives.zip"
    with zipfile.ZipFile(os.path.join(OUT, zip_name), "w") as z:
        for f in files: z.write(os.path.join(OUT, f), arcname=os.path.basename(f))
    save_registry(registry)
    # Upload to R2 if configured
    for f in files + [zip_name]:
        r2_upload(os.path.join(OUT, f), f"creatives/{f}")
    note = f"{len(jobs)} sets requested (cap {MAX_SETS} per run)." if len(lines) * variants > MAX_SETS else ""
    return jsonify({"files": files, "zip": zip_name, "errors": errors, "note": note, "registry_ids": list(registry.keys())})

@app.route("/api/batches")
def api_batches():
    out = []
    for name in sorted(os.listdir(OUT), reverse=True):
        d = os.path.join(OUT, name)
        if not os.path.isdir(d) or name.startswith("_"): continue
        media = [f for f in os.listdir(d) if f.lower().endswith((".jpg", ".mp4"))]
        if not media: continue
        out.append({"name": name, "count": len(media),
                    "zip": f"{name}/all_creatives.zip"
                    if os.path.exists(os.path.join(d, "all_creatives.zip")) else None})
    return jsonify(out[:30])

@app.route("/files/<path:p>")
def files_route(p):
    return send_from_directory(OUT, p, as_attachment=("zip" in p))

@app.route("/font")
def font_route():
    p = ensure_tiktok_font()
    return send_file(p, mimetype="font/ttf") if p else ("", 404)

# ----------------------------- Oberflaeche -----------------------------

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spark Maker</title><style>
@font-face{font-family:'Mont';src:url('/font') format('truetype');font-weight:100 900;font-display:swap}
:root{
 --bg:#0b0d12;--panel:#141824;--panel2:#1b2030;--line:#262c3d;
 --ink:#e9ecf4;--mut:#8b92a8;--pink:#ff3d71;--cyan:#22d3ee;--ok:#34d399;--bad:#fb7185;
}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:0 20px 60px}
.wrap{max-width:1180px;margin:0 auto}
header{display:flex;align-items:center;gap:0;padding:20px 0 0;margin-bottom:20px;border-bottom:1px solid var(--line)}
header h1{font-family:'Mont',system-ui;font-weight:700;font-size:22px;margin:0 24px 0 0;padding-bottom:16px;letter-spacing:-.5px}
.tabs{display:flex;gap:0}
.tab{padding:12px 24px 16px;cursor:pointer;font-size:14.5px;font-weight:600;color:var(--mut);border-bottom:2px solid transparent;margin-bottom:-1px;transition:.15s}
.tab:hover{color:var(--ink)}
.tab.on{color:var(--cyan);border-bottom-color:var(--cyan)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin:14px 0}
.phead{display:flex;align-items:center;gap:10px;margin:0 0 14px}
.phead h2{font-family:'Mont',system-ui;font-size:15px;font-weight:600;margin:0}
.step{font-family:'Mont',system-ui;font-size:12px;font-weight:700;color:var(--bg);background:var(--cyan);width:22px;height:22px;border-radius:7px;display:grid;place-items:center;flex:0 0 22px}
.hint{color:var(--mut);font-size:13px;line-height:1.55;margin:6px 0}
label{display:block;color:var(--mut);font-size:12.5px;margin:12px 0 5px;letter-spacing:.2px}
input,select,textarea{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:11px 12px;font-size:14.5px;font-family:inherit}
input:focus,select:focus,textarea:focus{outline:2px solid var(--cyan);outline-offset:0;border-color:transparent}
textarea{min-height:130px;line-height:1.6;resize:vertical}
button{font-family:inherit;font-size:14.5px;font-weight:600;border:0;border-radius:10px;padding:11px 18px;background:var(--pink);color:#fff;cursor:pointer;transition:filter .15s}
button:hover:not(:disabled){filter:brightness(1.12)} button:disabled{opacity:.45;cursor:default}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--ink)}
button.small{padding:6px 12px;font-size:13px}
.row{display:flex;gap:14px;flex-wrap:wrap}.row>div{flex:1;min-width:170px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{display:flex;align-items:center;gap:8px;background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:6px 13px 6px 8px;cursor:pointer;font-size:13.5px;user-select:none}
.chip.on{border-color:var(--cyan);background:rgba(34,211,238,.10)}
.chip.empty{opacity:.45}
.chip img{width:26px;height:26px;object-fit:cover;border-radius:50%;background:var(--line)}
.chip .n{color:var(--mut);font-size:12px}
.drop{border:1.5px dashed var(--line);border-radius:12px;padding:18px;text-align:center;color:var(--mut);font-size:13.5px;margin-top:12px;transition:.15s}
.drop.hot{border-color:var(--cyan);color:var(--ink);background:rgba(34,211,238,.06)}
.rev{display:flex;gap:10px;align-items:center;margin:7px 0;padding:9px 12px;background:var(--panel2);border:1px solid var(--line);border-radius:11px}
.rev input[type=text]{border:0;background:transparent;padding:6px 4px}
.rev input[type=checkbox]{width:17px;height:17px;flex:0 0 17px;accent-color:var(--cyan)}
.rev.rejected{opacity:.45}
button.rate{background:transparent;border:1px solid var(--line);padding:5px 9px;font-size:15px;line-height:1}
button.rate.picked.up{border-color:var(--ok);background:rgba(52,211,153,.14)}
button.rate.picked.down{border-color:var(--bad);background:rgba(251,113,133,.14)}
.libbar{display:flex;align-items:center;gap:12px;margin-top:12px}
.abadge{display:inline-block;background:rgba(34,211,238,.15);color:var(--cyan);border:1px solid rgba(34,211,238,.3);border-radius:6px;font-size:11px;padding:2px 8px;font-weight:600;letter-spacing:.3px;margin-bottom:2px}
.grid{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.grid img{width:104px;border-radius:9px;border:1px solid var(--line)}
.prev{display:flex;gap:14px;margin-top:14px;flex-wrap:wrap}
.prev img{width:190px;border-radius:12px;border:1px solid var(--line)}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:18px}
.batch{display:flex;gap:12px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line);font-size:13.5px}
.batch:last-child{border:0}
code{background:var(--panel2);padding:2px 6px;border-radius:5px;font-size:12px;color:var(--mut)}
/* Performance dashboard */
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:20px}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kpi .val{font-family:'Mont',system-ui;font-size:28px;font-weight:700;margin:4px 0 2px}
.kpi .lbl{color:var(--mut);font-size:12px}
.ptable{width:100%;border-collapse:collapse;font-size:13.5px}
.ptable th{padding:8px 10px;text-align:left;color:var(--mut);font-weight:600;border-bottom:1px solid var(--line)}
.ptable td{padding:8px 10px;border-bottom:1px solid rgba(38,44,61,.6)}
.ptable tr:last-child td{border:0}
.ptable tr:hover td{background:rgba(255,255,255,.02)}
.bar-fill{height:6px;border-radius:3px;background:var(--cyan);transition:.3s}
.angle-chip{display:inline-block;background:rgba(34,211,238,.1);color:var(--cyan);border-radius:5px;padding:1px 7px;font-size:11.5px;font-weight:600}
.page{display:none}.page.on{display:block}
</style></head><body><div class="wrap">

<header>
  <h1>Spark Maker</h1>
  <div class="tabs">
    <div class="tab on" onclick="switchTab('creator')">🎬 Creator</div>
    <div class="tab" onclick="switchTab('perf')">📊 Performance</div>
  </div>
</header>

<!-- ═══════════════ CREATOR TAB ═══════════════ -->
<div class="page on" id="page-creator">

<div class="panel">
  <div class="phead"><span class="step">1</span><h2>Assets</h2></div>
  <p class="hint">Sort your files into category folders — tick which ones this batch should use. Nothing ticked = all folders.</p>
  <label>Girl photos &amp; videos</label>
  <div class="chips" id="gchips"></div>
  <label>Proof screenshots</label>
  <div class="chips" id="pchips"></div>
  <div class="row" style="margin-top:14px">
    <div>
      <label>Add files to</label>
      <select id="upkind" onchange="loadAssets()">
        <option value="girls">Girl photos &amp; videos</option>
        <option value="proofs">Proof screenshots</option></select>
    </div>
    <div><label>Folder</label><select id="upcat"></select></div>
  </div>
  <div class="drop" id="drop">
    Drop images or videos here — or <label for="fileinput" style="display:inline;color:var(--cyan);cursor:pointer;margin:0">browse</label>
    <input id="fileinput" type="file" multiple style="display:none">
    <div id="upstat" class="hint" style="margin-top:6px"></div>
  </div>
</div>

<div class="panel">
  <div class="phead"><span class="step">2</span><h2>Hooks</h2></div>
  <p class="hint">One line = 1 creative set. Format: <b>slide 1 || slide 2 || angle tag</b>. Lines without || use empty slide 2.</p>
  <textarea id="hooks" placeholder="my ex text me asking who is funding my life || ths app sends funds direct to paypal || ex text"></textarea>
  <div style="margin-top:12px;padding:12px;background:var(--panel2);border:1px solid var(--line);border-radius:12px">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
      <span style="font-size:13px;color:var(--mut);font-weight:600">Add one manually</span>
      <div style="flex:1;height:1px;background:var(--line)"></div>
    </div>
    <div class="row">
      <div style="flex:3"><input id="m1" placeholder="Slide 1 — your hook"></div>
      <div style="flex:2"><input id="m2" placeholder="Slide 2 — payoff (optional)"></div>
      <div style="flex:2"><input id="m3" placeholder="Angle tag (optional)"></div>
      <div style="flex:0 0 auto;display:flex;align-items:flex-end"><button class="ghost" onclick="addManual()" style="margin-bottom:6px;white-space:nowrap">+ Add</button></div>
    </div>
  </div>
  <div class="row" style="margin-top:14px">
    <div><label>How many to generate</label><input id="hookn" type="number" value="20" min="1" max="60"></div>
    <div><label>Language</label><select id="hooklang">
      <option>English</option><option>German</option><option>French</option><option>Italian</option>
      <option>Spanish</option><option>Polish</option><option>Korean</option><option>Japanese</option>
      <option>Swedish</option><option>Dutch</option></select></div>
    <div><label>Angle</label><select id="angle">
      <option value="mix">Mixed</option><option value="friend">Friend notices something</option>
      <option value="secret">Keeping it quiet</option><option value="reaction">Quote from others</option>
      <option value="boredom">Boredom / phone in bed</option></select></div>
  </div>
  <label>What is it about</label>
  <input id="hookdesc" value="an app where you earn money by playing mini games like block puzzles">
  <label>Your best hooks as examples — the AI copies this tone</label>
  <textarea id="examples" style="min-height:74px"></textarea>
  <div id="keybox" style="display:none">
    <label>Free API key — aistudio.google.com → Get API key → Create API key. No card needed.</label>
    <input id="apikey" placeholder="paste key here — stored on this PC only">
  </div>
  <div class="libbar">
    <span id="libcount" class="hint" style="margin:0">no ratings yet</span>
    <button class="ghost small" onclick="toggleLib()">Show rated hooks</button>
  </div>
  <p class="hint">Every 👍 and 👎 is sent along next time you generate.</p>
  <div id="libbox"></div>
  <div class="bar">
    <button class="ghost" onclick="genHooks()">🤖 Generate hooks</button>
    <span id="hookout" class="hint" style="margin:0"></span>
  </div>
  <div id="review"></div>
</div>

<div class="panel">
  <div class="phead"><span class="step">3</span><h2>Look</h2></div>
  <div class="row">
    <div><label>Text style</label><select id="style" onchange="preview()">
      <option value="outline">White with black outline</option>
      <option value="box">White box, black text</option></select></div>
    <div><label>Position</label><select id="position" onchange="preview()">
      <option value="middle">Middle</option><option value="top">Top</option><option value="bottom">Bottom</option></select></div>
    <div><label>Size</label><select id="fontsize" onchange="preview()">
      <option>56</option><option selected>64</option><option>76</option><option>90</option></select></div>
  </div>
  <div class="bar"><button class="ghost" onclick="preview()">Show preview</button>
    <span id="prevmsg" class="hint" style="margin:0"></span></div>
  <div class="prev" id="prevbox"></div>
</div>

<div class="panel">
  <div class="phead"><span class="step">4</span><h2>Run</h2></div>
  <div class="row" style="max-width:420px">
    <div><label>Variants per hook</label>
      <select id="variants"><option value="1">1 set per hook</option>
      <option value="2">2 sets per hook</option><option value="3">3 sets per hook</option>
      <option value="5">5 sets per hook</option></select></div>
  </div>
  <div class="row" style="max-width:420px;margin-top:14px">
    <div><label>Angle tag for this entire batch (optional)</label>
      <input id="batchAngle" placeholder="e.g. ex text, friend notices, secret income"></div>
  </div>
  <div class="bar" style="margin-top:12px">
    <button id="go" onclick="runBatch()">Make creatives</button>
    <span id="runmsg" class="hint" style="margin:0"></span>
  </div>
  <div id="out"></div>
</div>

<div class="panel">
  <div class="phead"><span class="step">5</span><h2>Downloads</h2></div>
  <div id="batches" class="hint"></div>
</div>

</div><!-- end creator tab -->

<!-- ═══════════════ PERFORMANCE TAB ═══════════════ -->
<div class="page" id="page-perf">

<div class="kpi-grid" id="kpis" style="margin-top:20px">
  <div class="kpi"><div class="lbl">Total spend</div><div class="val" id="kpi-spend">—</div></div>
  <div class="kpi"><div class="lbl">Total revenue</div><div class="val" id="kpi-rev">—</div></div>
  <div class="kpi"><div class="lbl">Overall ROI</div><div class="val" id="kpi-roi">—</div></div>
  <div class="kpi"><div class="lbl">ROAS</div><div class="val" id="kpi-roas">—</div></div>
  <div class="kpi"><div class="lbl">Hooks tracked</div><div class="val" id="kpi-hooks">—</div></div>
  <div class="kpi"><div class="lbl">Best angle</div><div class="val" id="kpi-angle" style="font-size:16px;padding-top:6px">—</div></div>
</div>

<div class="panel">
  <div class="phead"><h2>Add performance data</h2></div>

  <div class="row" style="max-width:720px;align-items:flex-end;margin-bottom:4px">
    <div><label>Run name</label><input id="runName" placeholder="e.g. RUN 1 — Freecash DE ex text angle"></div>
    <div><label>From</label><input id="perfFrom" type="date"></div>
    <div><label>To</label><input id="perfTo" type="date"></div>
  </div>


  <div style="background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:10px">
    <p class="hint" style="margin-top:0"><b>MABAC data</b> — clicks &amp; revenue automatically via Everflow API</p>
    <div id="efKeyBox" style="display:none">
      <label>Everflow API key (one time — from MABAC dashboard → API)</label>
      <div style="display:flex;gap:8px"><input id="efKey" placeholder="x-eflow-api-key">
      <button class="ghost small" onclick="saveEfKey()" style="white-space:nowrap">Connect</button></div>
    </div>
    <div id="efConnected" style="display:none">
      <span class="ok" style="font-size:13px">✅ MABAC connected</span>
      <button class="ghost small" onclick="resetEfKey()" style="margin-left:12px">Change key</button>
    </div>
    <div class="bar" style="margin-top:12px">
      <button class="ghost" onclick="fetchEverflow()">⬇️ Pull data from MABAC</button>
      <span id="efMsg" class="hint" style="margin:0"></span>
    </div>
    <div id="efResult"></div>
  </div>

  <div style="background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:10px">
    <p class="hint" style="margin-top:0"><b>TikTok data</b> — upload your campaign CSV (Spend, CPC, CPM)</p>
    <div class="drop" id="perfdrop">
      Drop TikTok CSV here — or <label for="perffile" style="display:inline;color:var(--cyan);cursor:pointer;margin:0">browse</label>
      <input id="perffile" type="file" accept=".csv" style="display:none">
    </div>
    <div id="perfmap" style="display:none;margin-top:14px">
      <p class="hint" id="perfinfo"></p>
      <div style="overflow-x:auto"><div id="perfrows"></div></div>
    </div>
  </div>

  <div class="bar" style="margin-top:14px">
    <button onclick="savePerf()">&#128190; Save run &amp; teach the AI</button>
    <span id="perfmsg" class="hint" style="margin:0"></span>
  </div>
</div>

<div class="panel">
  <div class="phead"><h2>Hook performance</h2>
    <span class="hint" style="margin:0;margin-left:auto">sorted by ROI</span>
  </div>
  <div style="overflow-x:auto"><div id="perftable"><span class="hint">No data yet.</span></div></div>
</div>

<div class="panel">
  <div class="phead"><h2>Performance by angle</h2></div>
  <div id="angletable"><span class="hint">No data yet.</span></div>
</div>

</div><!-- end perf tab -->

</div><script>
const NL=String.fromCharCode(10);
async function j(u,o){const r=await fetch(u,o);return r.json()}
let SEL={girls:new Set(),proofs:new Set()};

function switchTab(t){
 document.querySelectorAll('.tab').forEach((el,i)=>el.classList.toggle('on',['creator','perf'][i]===t));
 document.querySelectorAll('.page').forEach(el=>el.classList.remove('on'));
 document.getElementById('page-'+t).classList.add('on');
 if(t==='perf'){loadPerfTable();loadAngleTable();checkEfKey();
  const today=new Date().toISOString().split('T')[0];
  if(!document.getElementById('perfFrom').value) document.getElementById('perfFrom').value=today;
  if(!document.getElementById('perfTo').value) document.getElementById('perfTo').value=today;
 }
}

// ---- Assets ----
async function loadAssets(){
 const a=await j('/api/assets');
 drawChips('gchips',a.girls,'girls'); drawChips('pchips',a.proofs,'proofs');
 const kind=document.getElementById('upkind').value;
 const sel=document.getElementById('upcat');
 const names=(kind==='girls'?a.girls:a.proofs).map(c=>c.name);
 sel.innerHTML=['unsorted',...names.filter(n=>n!=='unsorted')].map(n=>'<option>'+n+'</option>').join('');
}
function drawChips(id,cats,kind){
 const el=document.getElementById(id);
 if(!cats.length){el.innerHTML='<span class="hint" style="margin:0">No folders yet.</span>';return}
 el.innerHTML=cats.map(c=>{
  const on=SEL[kind].has(c.name)?' on':'';
  const empty=c.count===0?' empty':'';
  const img=c.sample&&!/\.(mp4|mov|m4v|webm)$/i.test(c.sample)?'<img src="/thumb/'+kind+'/'+encodeURI(c.sample)+'">':'<img>';
  return '<div class="chip'+on+empty+'" onclick="tog(\''+kind+'\',\''+c.name+'\',this)">'+img+
         '<span>'+c.name+'</span><span class="n">'+c.count+'</span></div>';
 }).join('');
}
function tog(kind,name,el){SEL[kind].has(name)?SEL[kind].delete(name):SEL[kind].add(name);el.classList.toggle('on')}
const drop=document.getElementById('drop');
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',ev=>upload(ev.dataTransfer.files));
document.getElementById('fileinput').addEventListener('change',e=>upload(e.target.files));
async function upload(files){
 if(!files||!files.length)return;
 const st=document.getElementById('upstat'); st.textContent='Uploading '+files.length+' file(s)…';
 const fd=new FormData();
 fd.append('kind',document.getElementById('upkind').value);
 fd.append('category',document.getElementById('upcat').value);
 for(const f of files) fd.append('files',f);
 const r=await(await fetch('/api/upload',{method:'POST',body:fd})).json();
 st.innerHTML='<span class="ok">Added '+r.saved+' file(s) to '+r.folder+'.</span>'+(r.skipped?' <span class="bad">'+r.skipped+' skipped.</span>':'');
 loadAssets();
}

// ---- Manual add ----
function addManual(){
 const s1=document.getElementById('m1').value.trim(); if(!s1){document.getElementById('m1').focus();return;}
 const s2=document.getElementById('m2').value.trim();
 const ang=document.getElementById('m3').value.trim();
 let line=s1; if(s2||ang)line+=' || '+(s2||''); if(ang)line+=' || '+ang;
 const t=document.getElementById('hooks');
 t.value=(t.value.trim()?t.value.trim()+NL:'')+line;
 document.getElementById('m1').value='';document.getElementById('m2').value='';document.getElementById('m3').value='';
 document.getElementById('m1').focus();
}
['m1','m2','m3'].forEach(id=>{const el=document.getElementById(id);if(el)el.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addManual();}});});

// ---- AI key ----
async function checkKey(){
 const k=await j('/api/keystatus');
 document.getElementById('keybox').style.display=k.keys.gemini?'none':'block';
 document.getElementById('apikey').value='';
}

// ---- Generate hooks ----
async function genHooks(){
 const o=document.getElementById('hookout'); o.textContent='AI is thinking…';
 const r=await j('/api/hooks',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({provider:'gemini',api_key:document.getElementById('apikey').value||'',
   n:document.getElementById('hookn').value,language:document.getElementById('hooklang').value,
   desc:document.getElementById('hookdesc').value,examples:document.getElementById('examples').value,
   angle:document.getElementById('angle').value})});
 if(r.error){o.innerHTML='<span class="bad">'+r.error+'</span>';checkKey();return}
 o.innerHTML='<span class="ok">'+r.hooks.length+' suggestions — pick what you like.</span>';
 renderReview(r.hooks); checkKey();
}
function renderReview(hooks){
 let h='<div class="bar"><button class="ghost small" onclick="markAll(true)">Select all</button>'+
  '<button class="ghost small" onclick="markAll(false)">Select none</button>'+
  '<button class="small" onclick="applySelected()">Add selected</button>'+
  '<button class="small" style="background:var(--ok);color:#000" onclick="applyAndRun()">⚡ Add selected &amp; run batch</button></div>';
 hooks.forEach((line,i)=>{
  let s1,s2,angle;
  if(typeof line==='object'){s1=line.slide1||'';s2=line.slide2||'';angle=line.angle||'';}
  else{const p=line.split('||');s1=(p[0]||'').trim();s2=(p[1]||'').trim();angle=(p[2]||'').trim();}
  const badge=angle?'<span class="abadge">'+angle+'</span>':'';
  h+='<div class="rev" data-row="'+i+'">'
    +'<input type="checkbox" class="revchk" data-i="'+i+'" checked>'
    +'<div style="flex:3;display:flex;flex-direction:column;gap:3px">'+badge
    +'<input type="text" class="rev1" data-i="'+i+'" value="'+s1.replace(/"/g,'&quot;')+'">'
    +'</div>'
    +'<input type="text" class="rev2" data-i="'+i+'" style="flex:2" value="'+s2.replace(/"/g,'&quot;')+'">'
    +'<input type="hidden" class="rangle" data-i="'+i+'" value="'+angle.replace(/"/g,'&quot;')+'">'
    +'<button class="rate up" onclick="rate('+i+',\'up\',this)">👍</button>'
    +'<button class="rate down" onclick="rate('+i+',\'down\',this)">👎</button>'
    +'</div>';
 });
 document.getElementById('review').innerHTML=h;
}
function markAll(v){document.querySelectorAll('.revchk').forEach(c=>c.checked=v)}
function applySelected(){
 const lines=[];
 document.querySelectorAll('.revchk').forEach(c=>{
  if(!c.checked)return; const i=c.dataset.i;
  const a=document.querySelector('.rev1[data-i="'+i+'"]').value.trim();
  const b=document.querySelector('.rev2[data-i="'+i+'"]').value.trim();
  const g=(document.querySelector('.rangle[data-i="'+i+'"]')||{}).value||'';
  if(a){let ln=a;if(b||g)ln+=' || '+(b||'');if(g)ln+=' || '+g;lines.push(ln);}
 });
 if(!lines.length)return;
 const t=document.getElementById('hooks');
 t.value=(t.value.trim()?t.value.trim()+NL:'')+lines.join(NL);
 document.getElementById('review').innerHTML='';
 document.getElementById('hookout').innerHTML='<span class="ok">'+lines.length+' added.</span>';
}
function applyAndRun(){applySelected();setTimeout(()=>{if(document.getElementById('hooks').value.trim())runBatch();},100);}
function rate(i,how,btn,overrideText,overrideAngle){
 const t=overrideText||(i>=0?document.querySelector('.rev1[data-i="'+i+'"]').value.trim():'');
 const ang=overrideAngle||(i>=0?((document.querySelector('.rangle[data-i="'+i+'"]')||{}).value||''):'');
 if(!t)return;
 const row=btn&&btn.closest?btn.closest('.rev'):null;
 if(row){row.querySelectorAll('.rate').forEach(b=>b.classList.remove('picked'));btn.classList.add('picked');
  row.classList.toggle('rejected',how==='down');
  if(how==='down')row.querySelector('.revchk').checked=false;}
 fetch('/api/rate',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({text:t,rating:how,angle:ang,lang:document.getElementById('hooklang').value})})
  .then(r=>r.json()).then(loadLib);
}

// ---- Library ----
async function loadLib(){
 const l=await j('/api/library');
 document.getElementById('libcount').innerHTML='<b>'+l.winners.length+'</b> rated good &nbsp;·&nbsp; <b>'+l.losers.length+'</b> rated bad';
 const box=document.getElementById('libbox'); if(!box.dataset.open)return;
 const rows=(arr,label)=>arr.slice(0,40).map(t=>'<div class="batch"><span style="flex:1">'+label+' '+t.replace(/</g,'&lt;')+'</span><button class="ghost small" onclick="libRemove('+JSON.stringify(t).replace(/"/g,'&quot;')+')">remove</button></div>').join('');
 box.innerHTML=rows(l.winners,'👍')+rows(l.losers,'👎')||'<span class="hint">Nothing rated yet.</span>';
}
function toggleLib(){const box=document.getElementById('libbox');if(box.dataset.open){box.dataset.open='';box.innerHTML='';}else{box.dataset.open='1';}loadLib();}
async function libRemove(t){await j('/api/library/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})});loadLib();}

// ---- Preview ----
function firstHook(){
 const l=document.getElementById('hooks').value.split(NL).filter(x=>x.trim());
 if(!l.length)return['your hook goes here',''];
 const p=l[0].split('||');return[(p[0]||'').trim(),(p[1]||'').trim()];
}
async function preview(){
 const m=document.getElementById('prevmsg'); m.textContent='Rendering…';
 const[hook,s2]=firstHook();
 const r=await j('/api/preview',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({hook:hook,slide2:s2,style:document.getElementById('style').value,
   position:document.getElementById('position').value,font_size:document.getElementById('fontsize').value,
   girl_cats:[...SEL.girls],proof_cats:[...SEL.proofs]})});
 if(r.error){m.innerHTML='<span class="bad">'+r.error+'</span>';return}
 m.textContent='First hook, random assets.';
 document.getElementById('prevbox').innerHTML=r.files.map(f=>'<img src="/files/'+f+'?t='+Date.now()+'">').join('');
}

// ---- Batch ----
let LAST=[];
async function runBatch(){
 const b=document.getElementById('go'),out=document.getElementById('out'),m=document.getElementById('runmsg');
 b.disabled=true;b.textContent='Working…';m.textContent='Videos take longer than photos.';out.innerHTML='';
 const r=await j('/api/batch',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({hooks:document.getElementById('hooks').value,
   style:document.getElementById('style').value,position:document.getElementById('position').value,
   font_size:document.getElementById('fontsize').value,
   variants:document.getElementById('variants').value,
   batch_angle:document.getElementById('batchAngle').value.trim(),
   girl_cats:[...SEL.girls],proof_cats:[...SEL.proofs]})});
 b.disabled=false;b.textContent='Make creatives';m.textContent='';
 if(r.error){out.innerHTML='<p class="bad">'+r.error+'</p>';return}
 LAST=r.files;
 let h='<p class="ok">'+r.files.length+' files ready. '+(r.note||'')+'</p>'+
  '<div class="bar"><a href="/files/'+r.zip+'"><button>Download ZIP</button></a>'+
  '<button class="ghost" onclick="downloadAll()">Download files separately</button></div><div class="grid">';
 for(const f of r.files){
  h+=f.endsWith('.jpg')?'<a href="/files/'+f+'" target="_blank"><img src="/files/'+f+'"></a>':
     '<a href="/files/'+f+'" target="_blank" style="width:100%;font-size:13px">🎥 '+f.split('/')[1]+'</a>';
 }
 h+='</div>';
 if(r.errors.length)h+='<p class="bad">'+r.errors.join('<br>')+'</p>';
 out.innerHTML=h; loadBatches();
}
function downloadAll(){
 if(!LAST.length)return;
 LAST.forEach((f,i)=>setTimeout(()=>{const a=document.createElement('a');a.href='/files/'+f;a.download=f.split('/')[1];document.body.appendChild(a);a.click();a.remove();},i*350));
}
async function loadBatches(){
 const bs=await j('/api/batches'); const el=document.getElementById('batches');
 if(!bs.length){el.textContent='Your finished batches will appear here.';return}
 el.innerHTML=bs.map(b=>'<div class="batch"><span style="flex:1">'+b.name+' — '+b.count+' files</span>'+
  (b.zip?'<a href="/files/'+b.zip+'"><button class="ghost small">ZIP</button></a>':'')+'</div>').join('');
}

// ════════════ PERFORMANCE TAB JS ════════════
let CSV_ROWS=[],CSV_COLS=[];
const pd=document.getElementById('perfdrop');
['dragenter','dragover'].forEach(e=>pd.addEventListener(e,ev=>{ev.preventDefault();pd.classList.add('hot')}));
['dragleave','drop'].forEach(e=>pd.addEventListener(e,ev=>{ev.preventDefault();pd.classList.remove('hot')}));
pd.addEventListener('drop',ev=>handleCSV(ev.dataTransfer.files[0]));
document.getElementById('perffile').addEventListener('change',e=>handleCSV(e.target.files[0]));

// ---- Everflow / MABAC ----
async function checkEfKey(){
 const r=await j('/api/everflow/keycheck');
 document.getElementById('efKeyBox').style.display=r.has_key?'none':'block';
 document.getElementById('efConnected').style.display=r.has_key?'block':'none';
}
async function saveEfKey(){
 const key=document.getElementById('efKey').value.trim();
 if(!key)return;
 const msg=document.getElementById('efMsg'); msg.textContent='Testing…';
 const r=await j('/api/everflow/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:key})});
 if(r.error){msg.innerHTML='<span class="bad">'+r.error+'</span>';return}
 msg.innerHTML='<span class="ok">✅ Connected — '+r.rows+' offers found.</span>';
 document.getElementById('efKey').value='';
 checkEfKey();
}
function resetEfKey(){document.getElementById('efConnected').style.display='none';document.getElementById('efKeyBox').style.display='block';}
let EF_DATA={};
async function fetchEverflow(){
 const msg=document.getElementById('efMsg');
 const from=document.getElementById('perfFrom').value;
 const to=document.getElementById('perfTo').value;
 if(!from||!to){msg.innerHTML='<span class="bad">Set the date range first.</span>';return}
 msg.textContent='Fetching from MABAC…';
 const r=await j('/api/everflow/fetch',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({from,to})});
 if(r.error){msg.innerHTML='<span class="bad">'+r.error+'</span>';return}
 EF_DATA=r;
 msg.innerHTML='<span class="ok">✅ Data pulled.</span>';
 const ios_pct=r.total_clicks>0?((r.ios_clicks/r.total_clicks)*100).toFixed(0):'—';
 const and_pct=r.total_clicks>0?((r.android_clicks/r.total_clicks)*100).toFixed(0):'—';
 document.getElementById('efResult').innerHTML=
  '<div class="kpi-grid" style="margin-top:10px">'+
  '<div class="kpi"><div class="lbl">Total clicks</div><div class="val" style="font-size:20px">'+r.total_clicks+'</div></div>'+
  '<div class="kpi"><div class="lbl">iOS clicks</div><div class="val" style="font-size:20px">'+r.ios_clicks+' <span style="font-size:13px;color:var(--mut)">('+ios_pct+'%)</span></div></div>'+
  '<div class="kpi"><div class="lbl">Android clicks</div><div class="val" style="font-size:20px">'+r.android_clicks+' <span style="font-size:13px;color:var(--mut)">('+and_pct+'%)</span></div></div>'+
  '<div class="kpi"><div class="lbl">Conversions</div><div class="val" style="font-size:20px;color:var(--ok)">'+r.conversions+'</div></div>'+
  '<div class="kpi"><div class="lbl">Revenue</div><div class="val" style="font-size:20px;color:var(--ok)">$'+r.revenue.toFixed(2)+'</div></div>'+
  '<div class="kpi"><div class="lbl">RPC</div><div class="val" style="font-size:20px">$'+r.rpc.toFixed(3)+'</div></div>'+
  '</div>';
}

async function handleCSV(f){
 if(!f)return;
 const fd=new FormData(); fd.append('csv',f);
 const r=await(await fetch('/api/perf/upload',{method:'POST',body:fd})).json();
 if(r.error){alert(r.error);return}
 CSV_ROWS=r.rows; CSV_COLS=r.cols;
 document.getElementById('perfmap').style.display='block';
 document.getElementById('perfinfo').innerHTML='<b>'+CSV_ROWS.length+'</b> ads found. Columns detected: <code>'+CSV_COLS.slice(0,8).join(', ')+'</code>. The tool auto-fills angle tags for hooks created in Spark Maker.';
 buildPerfMap();
}
function bestCol(candidates){
 for(const c of candidates){
  const hit=CSV_COLS.find(k=>k.toLowerCase()===c.toLowerCase());
  if(hit)return hit;
 }
 for(const c of candidates){
  const hit=CSV_COLS.find(k=>k.toLowerCase().includes(c.toLowerCase()));
  if(hit)return hit;
 }
 return '';
}
async function buildPerfMap(){
 const reg=await j('/api/registry');
 // TikTok CSV exact column names first, then fallbacks
 const nameCol =bestCol(['Campaign name','Ad name','ad_name','name','creative']);
 const spendCol=bestCol(['Spend','spend','amount spent']);
 const iosCol  =bestCol(['ios','iphone','apple']);
 const andCol  =bestCol(['android']);
 const clickCol=bestCol(['Clicks (destination)','click','result','conversion']);
 let h='<table class="ptable"><thead><tr>'+
  '<th>Ad name in TikTok</th><th>Hook text</th><th>Angle</th><th>Spend</th></tr></thead><tbody>';
 CSV_ROWS.forEach((row,i)=>{
  const name=(row[nameCol]||'').replace(/</g,'&lt;');
  const _spend=row[spendCol]||'';
  // Auto-match hook+angle from registry
  let autoHook='', autoAngle='';
  for(const[rid,rd] of Object.entries(reg)){
   if(name.toLowerCase().includes(rid.toLowerCase())||
      (rd.hook&&name.toLowerCase().includes(rd.hook.toLowerCase().slice(0,20)))){
    autoHook=rd.hook; autoAngle=rd.angle; break;
   }
  }
  h+=`<tr>
   <td style="max-width:200px;color:var(--mut);word-break:break-word;font-size:12.5px">${name}</td>
   <td><input class="phook" data-i="${i}" style="font-size:13px" value="${autoHook.replace(/"/g,'&quot;')}" placeholder="which hook?"></td>
   <td><input class="pangle" data-i="${i}" style="font-size:13px;color:var(--cyan)" value="${autoAngle.replace(/"/g,'&quot;')}" placeholder="angle tag"></td>
   <td style="color:var(--ok);font-weight:600">${_spend}</td>
   <input type="hidden" class="pspend" data-i="${i}" value="${_spend}">
  </tr>`;
 });
 h+='</tbody></table>';
 document.getElementById('perfrows').innerHTML=h;
}

async function savePerf(){
 const msg=document.getElementById('perfmsg'); msg.textContent='Saving…';
 const run_name=document.getElementById('runName').value.trim();
 if(!EF_DATA.revenue&&!EF_DATA.total_clicks){
  msg.innerHTML='<span class="bad">⚠️ Pull data from MABAC first — Revenue and Clicks are missing.</span>';return;}
 const hooks=[...document.querySelectorAll('.phook')].filter(h=>h.value.trim());
 const hookCount=hooks.length||1;
 const mappings=[];
 document.querySelectorAll('.phook').forEach(inp=>{
  const i=inp.dataset.i; const hook=inp.value.trim(); if(!hook)return;
  const spend=parseFloat((document.querySelector('.pspend[data-i="'+i+'"]')||{value:0}).value)||0;
  const share=1/hookCount;
  mappings.push({
   hook_text:hook,
   spend:spend,
   revenue:parseFloat(((EF_DATA.revenue||0)*share).toFixed(2)),
   conversions:Math.round((EF_DATA.conversions||0)*share),
   clicks:Math.round((EF_DATA.total_clicks||0)*share),
   angle:(document.querySelector('.pangle[data-i="'+i+'"]')||{value:''}).value||'',
   run_name:run_name});
 });
 if(!mappings.length){msg.innerHTML='<span class="bad">Enter at least one hook name.</span>';return}
 const r=await j('/api/perf/save',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({mappings})});
 msg.innerHTML='<span class="ok">Saved '+r.saved+'.'+(r.auto_good.length?' ✅ '+r.auto_good.length+' winners':'')+
  (r.auto_bad.length?' ❌ '+r.auto_bad.length+' losers':'')+'</span>';
 EF_DATA={};  // reset after save
 document.getElementById('efResult').innerHTML='';
 await loadPerfTable(); loadAngleTable();
}

async function loadPerfTable(){
 const rows=await j('/api/perf');
 if(!rows||!rows.length){document.getElementById('perftable').innerHTML='<span class="hint">No data yet — add a run above.</span>';updateKPIs([]);return;}
 const maxSpend=Math.max(...rows.map(r=>r.spend||0))||1;
 let h='<table class="ptable"><thead><tr><th>Hook</th><th>Angle</th><th>Run</th><th>Spend</th><th>Revenue</th><th>Conv.</th><th>Cost per conversion</th><th>ROAS</th><th>ROI</th><th></th></tr></thead><tbody>';
 rows.forEach(r=>{
  const col=r.roi!=null?(r.roi>0?'var(--ok)':'var(--bad)'):'var(--mut)';
  const roi=r.roi!=null?(r.roi>0?'+':'')+r.roi+'%':'—';
  const bar=`<div class="bar-fill" style="width:${Math.min(100,(r.spend/maxSpend)*100).toFixed(0)}%;background:${r.roi>0?'var(--ok)':'var(--line)'}"></div>`;
  const angl=r.angle?'<span class="angle-chip">'+r.angle+'</span>':'—';
  h+=`<tr>
   <td style="max-width:280px">${(r.hook||'').replace(/</g,'&lt;')}</td>
   <td>${angl}</td>
   <td style="padding:5px 8px">${r.spend?'$'+r.spend.toFixed(2):'-'}</td>
   <td style="padding:5px 8px;color:var(--ok)">${r.revenue?'$'+r.revenue.toFixed(2):'-'}</td>
   <td style="padding:5px 8px">${r.conversions||0}</td>
   <td style="padding:5px 8px">${r.cpa?'$'+r.cpa.toFixed(2):'-'}</td>
   <td style="padding:5px 8px;font-weight:600">${(r.spend>0)?(r.revenue/r.spend).toFixed(2)+'x':'—'}</td>

   <td style="font-weight:700;color:${col}">${roi}</td>
   <td><button class="rate up small" onclick="rate(-1,'up',null,'${r.hook.replace(/'/g,"\\'")}','${(r.angle||'').replace(/'/g,"\\'")}')">👍</button>
       <button class="rate down small" onclick="rate(-1,'down',null,'${r.hook.replace(/'/g,"\\'")}','${(r.angle||'').replace(/'/g,"\\'")}')">👎</button></td>
  </tr>`;
 });
 h+='</tbody></table>';
 document.getElementById('perftable').innerHTML=h;
 updateKPIs(rows);
}

async function loadAngleTable(){
 const rows=await j('/api/perf');
 if(!rows.length)return;
 const byAngle={};
 rows.forEach(r=>{
  if(!r.angle)return;
  if(!byAngle[r.angle])byAngle[r.angle]={angle:r.angle,spend:0,revenue:0,hooks:0};
  byAngle[r.angle].spend+=r.spend||0;
  byAngle[r.angle].revenue+=r.revenue||0;
  byAngle[r.angle].hooks++;
 });
 const angles=Object.values(byAngle).sort((a,b)=>(b.revenue-b.spend)-(a.revenue-a.spend));
 if(!angles.length){document.getElementById('angletable').innerHTML='<span class="hint">Add angle tags to see this breakdown.</span>';return}
 let h='<table class="ptable"><thead><tr><th>Angle</th><th>Hooks</th><th>Spend</th><th>Revenue</th><th>ROI</th></tr></thead><tbody>';
 angles.forEach(a=>{
  const roi=a.spend>0?((a.revenue-a.spend)/a.spend*100).toFixed(1):null;
  const col=roi!=null?(roi>0?'var(--ok)':'var(--bad)'):'var(--mut)';
  h+=`<tr>
   <td><span class="angle-chip">${a.angle}</span></td>
   <td style="color:var(--mut)">${a.hooks}</td>
   <td style="color:var(--mut)">€${a.spend.toFixed(2)}</td>
   <td style="color:var(--mut)">€${a.revenue.toFixed(2)}</td>
   <td style="font-weight:700;color:${col}">${roi!=null?(roi>0?'+':'')+roi+'%':'—'}</td>
  </tr>`;
 });
 h+='</tbody></table>';
 document.getElementById('angletable').innerHTML=h;
}

function updateKPIs(rows){
 if(!rows||!rows.length)return;
 const spend=rows.reduce((s,r)=>s+(r.spend||0),0);
 const rev=rows.reduce((s,r)=>s+(r.revenue||0),0);
 const roi=spend>0?((rev-spend)/spend*100).toFixed(1):null;
 document.getElementById('kpi-spend').textContent='$'+spend.toFixed(2);
 document.getElementById('kpi-rev').textContent='$'+rev.toFixed(2);
 document.getElementById('kpi-roi').textContent=roi!=null?(roi>0?'+':'')+roi+'%':'—';
 document.getElementById('kpi-roi').style.color=roi>0?'var(--ok)':roi<0?'var(--bad)':'var(--ink)';
 const roas=spend>0?(rev/spend):null;
 document.getElementById('kpi-roas').textContent=roas!=null?roas.toFixed(2)+'x':'—';
 document.getElementById('kpi-roas').style.color=roas>1?'var(--ok)':roas<1?'var(--bad)':'var(--ink)';
 document.getElementById('kpi-hooks').textContent=rows.length;
 // Best angle
 const byAngle={};
 rows.forEach(r=>{if(!r.angle)return;if(!byAngle[r.angle])byAngle[r.angle]={s:0,r:0};byAngle[r.angle].s+=r.spend||0;byAngle[r.angle].r+=r.revenue||0;});
 const best=Object.entries(byAngle).sort((a,b)=>(b[1].r-b[1].s)-(a[1].r-a[1].s))[0];
 document.getElementById('kpi-angle').textContent=best?best[0]:'—';
}

loadAssets(); checkKey(); loadBatches(); loadLib();
</script></body></html>"""
@app.route("/")
def home(): return Response(PAGE, mimetype="text/html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5016))
    is_local = not os.environ.get("RAILWAY_ENVIRONMENT")
    if is_local:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        print(f"\n  Spark Maker -> http://127.0.0.1:{port}  (keep this window open)\n")
    app.run(host="0.0.0.0", port=port)
