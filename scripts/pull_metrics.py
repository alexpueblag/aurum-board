#!/usr/bin/env python3
# Jalador de metricas (Graph API) para el board de Yo Desarrollo — VERSION NUBE.
# Copia adaptada de ~/yod_audit/pull_metrics.py para correr en GitHub Actions:
#  - El token entra por la variable de entorno YOD_META_TOKEN (un GitHub Secret),
#    con respaldo al archivo local ~/.yod_meta_token si se corre en la Mac.
#  - Escribe metrics.json + covers/ en la RAIZ del repo (no en scripts/).
#  - NUNCA imprime el token ni URLs con access_token (higiene de logs, repo publico).
#  - Los abortos controlados (pull vacio/degradado/lento) salen con codigo 0
#    (no-op limpio) para no marcar la corrida como fallida ni mandar correos falsos;
#    un token invalido si sale con error (eso si merece aviso).
import sys, os, json, time, datetime, io, re, urllib.request, urllib.parse, urllib.error
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    Image = None

API = "https://graph.facebook.com/v21.0"
# En la nube el script vive en <repo>/scripts/ y las salidas van a la RAIZ del repo.
BASE = str(Path(__file__).resolve().parents[1])

# Apps Script de los 2 imanes de leads (publicos; se leen para cruzar leads_utm con posts de FB).
BOARD_URLS = {
    "cuestionario": "https://script.google.com/macros/s/AKfycbw1Wm5wOC6XE2PcS0xBbIy-OdBbmU5vjvwnVNaHN6Fa7HHugyuk-EvkoURtr56j6dDVag/exec",
    "potencial": "https://script.google.com/macros/s/AKfycbw3EB-6Q9Mq-ouDU-JvKMrRUaw4auYVeGkKja783yJ7_dEpCOW8xoMhs8IQMDojmlDB3A/exec",
}

def _scrub(s):
    # defensa en profundidad: jamas dejar caer el access_token en un log publico
    return re.sub(r"access_token=[^&\s\"']+", "access_token=***", str(s))

def fetch_leads_utm():
    out = []
    for nombre, url in BOARD_URLS.items():
        try:
            u = url + ("&" if "?" in url else "?") + "recurso=board"
            req = urllib.request.Request(u, method="GET")
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            for l in (j.get("leads_utm") or []):
                if l.get("utm_campaign"):
                    out.append(l)
        except Exception:
            pass
    return out

def leads_por_campana(leads_utm):
    agg = {}
    for l in leads_utm:
        c = str(l.get("utm_campaign") or "").strip()
        if not c:
            continue
        agg.setdefault(c, {"leads": 0, "citas": 0})
        agg[c]["leads"] += 1
        if l.get("cita"):
            agg[c]["citas"] += 1
    return agg

def _get(path, params, intentos=4):
    """GET a la Graph API con REINTENTOS + timeout corto (20s). Cortes de red y 5xx/429
    se reintentan con backoff; los errores reales de la API se devuelven (sin el token)."""
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < intentos - 1:
                time.sleep(2 ** i)
                continue
            try:
                err = json.loads(e.read().decode() or "{}").get("error", {})
            except Exception:
                err = {"message": f"HTTP {e.code}"}
            return {"_error": err}
        except Exception as e:
            ultimo = e
            if i < intentos - 1:
                time.sleep(2 ** i)
                continue
    return {"_error": {"message": _scrub(f"red tras {intentos} intentos: {type(ultimo).__name__}: {ultimo}")}}

def resolve(token, want_name=None):
    me = _get("me/accounts", {"access_token": token,
                              "fields": "id,name,access_token,instagram_business_account"})
    if "_error" in me:
        raise SystemExit("ERROR token/cuenta: " + _scrub(me["_error"].get("message") or "desconocido"))
    pages = me.get("data", [])
    pg = None
    if want_name:
        pg = next((p for p in pages if p.get("name", "").strip().lower() == want_name.strip().lower()), None)
    if not pg:
        pg = next((p for p in pages if p.get("instagram_business_account")), None)
    if not pg and pages:
        pg = pages[0]
    if not pg:
        raise SystemExit("El token no administra ninguna pagina.")
    iga = pg.get("instagram_business_account") or {}
    ig_id = iga.get("id")
    if not ig_id:
        info = _get(pg["id"], {"access_token": pg.get("access_token", token), "fields": "instagram_business_account"})
        ig_id = (info.get("instagram_business_account") or {}).get("id")
    return pg["id"], pg.get("access_token", token), pg.get("name"), ig_id

def _insights(node_id, token, metrics):
    out = {}
    r = _get(f"{node_id}/insights", {"access_token": token, "metric": ",".join(metrics)})
    if "data" in r:
        for m in r["data"]:
            if m["name"] in out and m.get("period") != "lifetime":
                continue
            vals = m.get("values") or [{}]
            out[m["name"]] = vals[0].get("value")
        return out
    for m in metrics:
        rr = _get(f"{node_id}/insights", {"access_token": token, "metric": m})
        if "data" in rr and rr["data"]:
            entry = next((d for d in rr["data"] if d.get("period") == "lifetime"), rr["data"][0])
            vals = entry.get("values") or [{}]
            out[m] = vals[0].get("value")
    return out

def _num(x):
    return x if isinstance(x, (int, float)) else None

def download_cover(url, post_id):
    if not url:
        return None
    os.makedirs(os.path.join(BASE, "covers"), exist_ok=True)
    path = os.path.join(BASE, "covers", f"{post_id}.jpg")
    data = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            break
        except Exception:
            if i < 2:
                time.sleep(1 + i)
                continue
            return None
    if data is None:
        return None
    try:
        if Image is not None:
            try:
                img = Image.open(io.BytesIO(data))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.thumbnail((600, 600), Image.LANCZOS)
                img.save(path, "JPEG", quality=78, optimize=True)
                return f"covers/{post_id}.jpg"
            except Exception:
                pass
        with open(path, "wb") as f:
            f.write(data)
        return f"covers/{post_id}.jpg"
    except Exception:
        return None

def ig_reach_breakdown(media_id, token):
    r = _get(f"{media_id}/insights", {"access_token": token, "metric": "reach", "breakdown": "follow_type"})
    if "data" not in r or not r["data"]:
        return None, None
    tv = r["data"][0].get("total_value") or {}
    res = ((tv.get("breakdowns") or [{}])[0]).get("results") or []
    f = nf = None
    for it in res:
        dv = (it.get("dimension_values") or [""])[0]
        if dv == "FOLLOWER": f = it.get("value")
        elif dv == "NON_FOLLOWER": nf = it.get("value")
    return f, nf

def pull_ig(ig_id, token, limit=25):
    rows = []
    media = _get(f"{ig_id}/media", {
        "access_token": token, "limit": limit,
        "fields": "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count,thumbnail_url,media_url"})
    if "_error" in media:
        return rows, media["_error"].get("message")
    for m in media.get("data", []):
        is_reel = m.get("media_product_type") == "REELS"
        metrics = ["reach", "saved", "shares", "total_interactions", "profile_visits", "follows"]
        if is_reel:
            metrics += ["views", "plays", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]
        ins = _insights(m["id"], token, metrics)
        rf, rnf = ig_reach_breakdown(m["id"], token)
        likes = m.get("like_count"); comments = m.get("comments_count")
        saves = _num(ins.get("saved")); shares = _num(ins.get("shares"))
        inter = _num(ins.get("total_interactions"))
        if inter is None:
            inter = (likes or 0) + (comments or 0) + (saves or 0) + (shares or 0)
        reach = _num(ins.get("reach"))
        tipo = "Reel" if is_reel else ("Carrusel" if m.get("media_type") == "CAROUSEL_ALBUM" else (m.get("media_type") or "").title())
        thumb = m.get("thumbnail_url") or m.get("media_url")
        rows.append({
            "red": "IG", "tipo": tipo, "id": m["id"], "permalink": m.get("permalink"),
            "fecha": (m.get("timestamp") or "")[:10],
            "titulo": ((m.get("caption") or "").strip().split("\n")[0])[:90],
            "thumb": thumb, "cover": download_cover(thumb, m["id"]),
            "reach": reach, "likes": likes, "comments": comments, "shares": shares, "saves": saves,
            "views": _num(ins.get("views")) or _num(ins.get("plays")),
            "watch_avg": _num(ins.get("ig_reels_avg_watch_time")),
            "watch_total": _num(ins.get("ig_reels_video_view_total_time")),
            "profile_visits": _num(ins.get("profile_visits")),
            "follows": _num(ins.get("follows")),
            "reach_follower": _num(rf), "reach_nonfollower": _num(rnf),
            "interacciones": inter,
            "er": round(inter / reach * 100, 2) if reach else None,
            "save_rate": round(saves / reach * 100, 2) if (reach and saves is not None) else None,
        })
    return rows, None

def pull_fb(page_id, page_token, limit=25):
    rows = []
    posts = _get(f"{page_id}/published_posts", {
        "access_token": page_token, "limit": limit,
        "fields": "id,message,created_time,permalink_url,status_type,full_picture,shares"})
    if "_error" in posts:
        return rows, posts["_error"].get("message")
    for p in posts.get("data", []):
        ins = _insights(p["id"], page_token, ["post_clicks", "post_video_views"])
        det = _get(p["id"], {"access_token": page_token,
                   "fields": "reactions.summary(true).limit(0),comments.summary(true).limit(0)"})
        reactions = ((det.get("reactions") or {}).get("summary") or {}).get("total_count")
        likes = None
        comments = ((det.get("comments") or {}).get("summary") or {}).get("total_count")
        shares = (p.get("shares") or {}).get("count")
        react = reactions if reactions is not None else likes
        inter = (react or 0) + (comments or 0) + (shares or 0)
        fp = p.get("full_picture")
        rows.append({
            "red": "FB", "tipo": (p.get("status_type") or "post").replace("_", " ").title(),
            "id": p["id"], "permalink": p.get("permalink_url"),
            "fecha": (p.get("created_time") or "")[:10],
            "titulo": ((p.get("message") or "").strip().split("\n")[0])[:90],
            "thumb": fp, "cover": download_cover(fp, p["id"]),
            "reach": None, "likes": react, "comments": comments, "shares": shares, "saves": None,
            "clicks": _num(ins.get("post_clicks")), "views": _num(ins.get("post_video_views")),
            "profile_visits": None, "follows": None,
            "reach_follower": None, "reach_nonfollower": None,
            "interacciones": inter,
            "er": None, "save_rate": None,
        })
    return rows, None

def _skip(msg):
    # aborto CONTROLADO en la nube: no-op limpio (codigo 0), no una corrida "fallida"
    print("::notice::" + msg)
    sys.exit(0)

def main():
    def _limite(*_):
        _skip("La corrida excedio 6 min (red lenta). No se escribe; la proxima hora reintenta.")
    try:
        import signal
        signal.signal(signal.SIGALRM, _limite)
        signal.alarm(360)
    except Exception:
        pass

    token = os.environ.get("YOD_META_TOKEN")
    if not token:
        tf = os.path.expanduser("~/.yod_meta_token")
        if os.path.exists(tf):
            token = open(tf).read().strip()
    if not token:
        raise SystemExit("Falta el token (env YOD_META_TOKEN o ~/.yod_meta_token).")

    page_id, page_token, page_name, ig_id = resolve(token, "Yo Desarrollo")
    print(f"OK Pagina: {page_name} (id {page_id}) | IG: {ig_id or 'NO VINCULADO'}")

    fb_rows, fb_err = pull_fb(page_id, page_token)
    ig_rows, ig_err = ([], "sin IG") if not ig_id else pull_ig(ig_id, token)
    if fb_err: print("Aviso FB:", _scrub(fb_err))
    if ig_err: print("Aviso IG:", _scrub(ig_err))

    posts = ig_rows + fb_rows
    posts.sort(key=lambda r: r.get("fecha") or "", reverse=True)
    stamp = time.strftime("%Y-%m-%d %H:%M")

    prev_path = os.path.join(BASE, "metrics.json")
    prev_full, prev_n = None, 0
    if os.path.exists(prev_path):
        try:
            prev_full = json.load(open(prev_path))
            prev_n = len(prev_full.get("posts", []))
        except Exception:
            prev_full, prev_n = None, 0
    if not posts:
        _skip("0 posts (red o permisos). No se escribe metrics.json (protege los datos buenos).")
    if prev_n and len(posts) < prev_n * 0.7:
        _skip(f"Solo {len(posts)} posts vs {prev_n} previos: pull degradado. No se escribe.")

    def shortid(post_id):
        return str(post_id).split("_")[-1][-10:]
    campanas = leads_por_campana(fetch_leads_utm())
    leads_utm_activo = bool(campanas)
    for r in posts:
        if r.get("red") == "FB" and r.get("fecha"):
            if leads_utm_activo:
                por_fecha = campanas.get("fb-" + r["fecha"])
                por_id = campanas.get("fb-" + shortid(r["id"]))
                r["leads_atribuidos"] = (por_fecha["leads"] if por_fecha else 0) + (por_id["leads"] if por_id else 0)
                r["citas_atribuidas"] = (por_fecha["citas"] if por_fecha else 0) + (por_id["citas"] if por_id else 0)
            else:
                r["leads_atribuidos"] = None
                r["citas_atribuidas"] = None

    def _norm_posts(ps):
        return json.dumps([{k: v for k, v in p.items() if k != "serie"} for p in ps],
                          sort_keys=True, ensure_ascii=False, default=str)
    datos_cambiaron = prev_full is None or _norm_posts(prev_full.get("posts", [])) != _norm_posts(posts)

    hist_path = os.path.join(BASE, "metrics_history.jsonl")
    with open(hist_path, "a") as f:
        posts_hist = posts if datos_cambiaron else []
        for r in posts_hist:
            snap = {k: r.get(k) for k in ("red", "id", "reach", "likes", "comments", "shares", "saves", "interacciones", "views", "er")}
            snap["t"] = stamp
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")

    series = {}
    try:
        for line in open(hist_path):
            h = json.loads(line)
            series.setdefault(h.get("id"), {})[h.get("t")] = {"t": h.get("t"), "reach": h.get("reach"), "inter": h.get("interacciones")}
    except Exception:
        pass
    for r in posts:
        r["serie"] = list(series.get(r["id"], {}).values())[-30:]

    def semana_stats(ini, fin):
        sel = [r for r in posts if r.get("fecha") and ini <= r["fecha"] <= fin]
        reach = sum((r.get("reach") or 0) for r in sel if r.get("red") == "IG")
        inter = sum((r.get("interacciones") or 0) for r in sel)
        saves = sum((r.get("saves") or 0) for r in sel)
        return {"posts": len(sel), "reach": reach, "interacciones": inter, "guardados": saves}
    hoy = datetime.date.today()
    f_hoy, f_m7, f_m8, f_m14 = (hoy.isoformat(), (hoy - datetime.timedelta(days=6)).isoformat(),
                                 (hoy - datetime.timedelta(days=7)).isoformat(), (hoy - datetime.timedelta(days=13)).isoformat())
    semana = {"actual": semana_stats(f_m7, f_hoy), "anterior": semana_stats(f_m14, f_m8)}
    dias_sin_publicar = (hoy - datetime.date.fromisoformat(posts[0]["fecha"])).days if posts and posts[0].get("fecha") else None

    out = {"generado": stamp, "pagina": page_name, "ig_id": ig_id, "posts": posts,
           "semana": semana, "dias_sin_publicar": dias_sin_publicar, "leads_utm_activo": leads_utm_activo}
    with open(os.path.join(BASE, "metrics.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Escrito: metrics.json ({len(posts)} posts) @ {stamp} · cambios reales: {'SI' if datos_cambiaron else 'no'}")

if __name__ == "__main__":
    main()
