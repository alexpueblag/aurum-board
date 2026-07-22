#!/usr/bin/env python3
# Guardian del TOKEN DE META — corre en GitHub Actions 1 vez al dia.
# Verifica que el token de SOLO-LECTURA (Secret YOD_META_TOKEN) siga vivo. Un token
# "permanente" igual muere si cambian password, quitan un permiso o la app cae en review.
# Convierte una falla sorpresiva (board congelado) en un aviso CON tiempo de reaccion.
# Si no hay Secret todavia, no hace nada (no-op limpio). NUNCA imprime el token.
import json, os, re, subprocess, sys, datetime, urllib.request, urllib.error, urllib.parse

TITLE = "Guardian: el token de Meta esta en riesgo"
LABEL = "guardian-token"

def _scrub(s):
    # blindaje: nunca dejar caer el token en un log/issue publico
    return re.sub(r"access_token=[^&\s\"']+", "access_token=***", str(s))

def gh(*args):
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True)

def api(path, token):
    url = "https://graph.facebook.com/v21.0/" + path + "?" + urllib.parse.urlencode({"access_token": token})
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())

def main():
    token = os.environ.get("YOD_META_TOKEN")
    if not token:
        print("Sin Secret YOD_META_TOKEN todavia; nada que vigilar.")
        return

    problema = None
    try:
        me = api("me", token)
        if not me.get("id"):
            problema = "el endpoint /me no devolvio una cuenta"
        else:
            dbg = api("debug_token", token) if False else None  # /debug_token requiere app token; /me basta como latido
            print("Token valido; cuenta:", me.get("name") or me.get("id"))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode() or "{}").get("error", {})
            problema = _scrub(f"code {err.get('code')} · {err.get('message')}")
        except Exception:
            problema = f"HTTP {e.code}"
    except Exception as e:
        problema = type(e).__name__

    gh("label", "create", LABEL, "--color", "a8503f",
       "--description", "El token de Meta del board esta en riesgo", "--force")
    listado = gh("issue", "list", "--label", LABEL, "--state", "open",
                 "--json", "number,title", "--limit", "20").stdout
    try:
        abiertos = [i for i in json.loads(listado or "[]") if i["title"] == TITLE]
    except Exception:
        abiertos = []
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if problema:
        cuerpo = (f"El token de Meta del board fallo la verificacion ({stamp}):\n\n> {problema}\n\n"
                  "El board dejara de refrescarse hasta renovarlo. Genera un token de SOLO-LECTURA "
                  "nuevo del System User y actualiza el Secret **YOD_META_TOKEN** en Settings -> "
                  "Secrets -> Actions.\n\nGuardian automatico.")
        if abiertos:
            gh("issue", "comment", str(abiertos[0]["number"]), "--body", "Sigue en riesgo:\n\n" + cuerpo)
        else:
            gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body", cuerpo)
        print("Token en riesgo — issue gestionado.")
    else:
        for i in abiertos:
            gh("issue", "comment", str(i["number"]), "--body", "El token volvio a validar (" + stamp + "). Cierro.")
            gh("issue", "close", str(i["number"]))
        print("Token sano.")

if __name__ == "__main__":
    main()
