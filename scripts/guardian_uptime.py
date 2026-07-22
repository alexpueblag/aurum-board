#!/usr/bin/env python3
# Guardian de UPTIME — corre en GitHub Actions cada ~30 min.
# Revisa que los formularios (paginas) y sus backends (Apps Script) respondan de verdad,
# no solo que el servidor conteste. Si algo se cae, abre/actualiza UN issue de GitHub
# (etiqueta guardian-uptime); al recuperarse, comenta y lo cierra. Sin secretos.
import json, os, subprocess, sys, datetime, urllib.request, urllib.error

# Endpoints que importan para que ENTREN leads. Backend del cuestionario = webhook VIVO
# al que el formulario manda los leads (recurso=textos); no la URL de lectura del board.
TARGETS = [
    {"n": "Pagina · Plan de Potencial", "url": "https://alexpueblag.github.io/plan-potencial/", "need": "<title"},
    {"n": "Pagina · Cuestionario",      "url": "https://alexpueblag.github.io/aurum-experiencia/", "need": "<title"},
    {"n": "Pagina · Board",             "url": "https://alexpueblag.github.io/aurum-board/", "need": "<title"},
    {"n": "Backend · Plan de Potencial", "url": "https://script.google.com/macros/s/AKfycbw3EB-6Q9Mq-ouDU-JvKMrRUaw4auYVeGkKja783yJ7_dEpCOW8xoMhs8IQMDojmlDB3A/exec?recurso=board", "json_ok": True},
    {"n": "Backend · Cuestionario",      "url": "https://script.google.com/macros/s/AKfycbztAKA7K5QwO6k45PqjixYLNppLypzCpoz2KvNIkML8kciBLZVKKoais8__0DnYuEQQOg/exec?recurso=textos", "json_any": True},
]
TITLE = "Guardian: un servicio o formulario esta caido"
LABEL = "guardian-uptime"

def check(t):
    url = t["url"] + ("&" if "?" in t["url"] else "?") + "cb=" + str(os.getpid())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "yod-guardian"})
        with urllib.request.urlopen(req, timeout=35) as r:
            code = r.status
            body = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__
    if code != 200:
        return False, f"HTTP {code}"
    if t.get("need") and t["need"] not in body:
        return False, "responde 200 pero no trae el contenido esperado"
    if t.get("json_ok") or t.get("json_any"):
        try:
            j = json.loads(body)
        except Exception:
            return False, "responde 200 pero no es JSON valido"
        if t.get("json_ok") and not j.get("ok"):
            return False, f"200 pero ok={j.get('ok')} ({j.get('error')})"
    return True, "ok"

def gh(*args):
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True)

def main():
    fails = []
    for t in TARGETS:
        ok, msg = check(t)
        print(("OK    " if ok else "FALLA ") + t["n"] + " -> " + msg)
        if not ok:
            fails.append((t["n"], t["url"].split("?")[0], msg))

    gh("label", "create", LABEL, "--color", "d73a4a",
       "--description", "Aviso automatico de servicio caido", "--force")
    listado = gh("issue", "list", "--label", LABEL, "--state", "open",
                 "--json", "number,title", "--limit", "20").stdout
    try:
        abiertos = [i for i in json.loads(listado or "[]") if i["title"] == TITLE]
    except Exception:
        abiertos = []
    ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if fails:
        cuerpo = ("Estos servicios no respondieron bien (" + ahora + "):\n\n" +
                  "\n".join(f"- **{n}** — {m}\n  {u}" for n, u, m in fails) +
                  "\n\nGuardian automatico (uptime). Se cierra solo al recuperarse todo.")
        if abiertos:
            gh("issue", "comment", str(abiertos[0]["number"]), "--body", "Sigue caido:\n\n" + cuerpo)
        else:
            gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body", cuerpo)
        print(f"\n{len(fails)} servicio(s) caido(s) — issue gestionado.")
    else:
        for i in abiertos:
            gh("issue", "comment", str(i["number"]), "--body", "Todo recuperado (" + ahora + "). Cierro este aviso.")
            gh("issue", "close", str(i["number"]))
        print("\nTodo en pie.")

if __name__ == "__main__":
    main()
