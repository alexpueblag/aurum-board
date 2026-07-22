#!/usr/bin/env python3
# Guardian de SALUD DEL REFRESCO — corre en GitHub Actions cada hora (desfasado del refresco).
# Pregunta: "el refresco automatico del board ha tenido una corrida EXITOSA en las ultimas horas?"
# Usa el historial de corridas del workflow (no el timestamp de metrics.json, que a proposito
# no cambia cuando no hay datos nuevos). Si lleva >UMBRAL sin exito, abre/actualiza un issue.
import json, subprocess, datetime, sys

WORKFLOW = "refresh-board.yml"
UMBRAL_HORAS = 4
TITLE = "Guardian: el refresco del board no ha corrido bien"
LABEL = "guardian-salud"

def gh(*args):
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True)

def main():
    out = gh("run", "list", "--workflow", WORKFLOW, "--limit", "20",
             "--json", "status,conclusion,createdAt").stdout
    try:
        runs = json.loads(out or "[]")
    except Exception:
        runs = []
    completadas = [r for r in runs if r.get("status") == "completed"]

    if not completadas:
        print("El workflow de refresco aun no ha tenido corridas completas; nada que evaluar.")
        return

    ahora = datetime.datetime.now(datetime.timezone.utc)
    def edad_horas(iso):
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (ahora - t).total_seconds() / 3600.0

    exito_reciente = any(r.get("conclusion") == "success" and edad_horas(r["createdAt"]) <= UMBRAL_HORAS
                         for r in completadas)
    ultima = completadas[0]
    print(f"Ultima corrida: {ultima.get('conclusion')} hace {edad_horas(ultima['createdAt']):.1f} h · "
          f"exito en las ultimas {UMBRAL_HORAS} h: {exito_reciente}")

    gh("label", "create", LABEL, "--color", "b07d3a",
       "--description", "El refresco automatico del board no esta corriendo bien", "--force")
    listado = gh("issue", "list", "--label", LABEL, "--state", "open",
                 "--json", "number,title", "--limit", "20").stdout
    try:
        abiertos = [i for i in json.loads(listado or "[]") if i["title"] == TITLE]
    except Exception:
        abiertos = []
    stamp = ahora.strftime("%Y-%m-%d %H:%M UTC")

    if not exito_reciente:
        cuerpo = (f"El refresco del board no ha tenido una corrida EXITOSA en las ultimas {UMBRAL_HORAS} h "
                  f"({stamp}). Ultima corrida: **{ultima.get('conclusion')}** hace "
                  f"{edad_horas(ultima['createdAt']):.1f} h.\n\nRevisa la pestana Actions del repo. "
                  "Si es el token, hay que renovar el Secret YOD_META_TOKEN.\n\nGuardian automatico.")
        if abiertos:
            gh("issue", "comment", str(abiertos[0]["number"]), "--body", "Sigue sin refrescar:\n\n" + cuerpo)
        else:
            gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body", cuerpo)
        print("Refresco en riesgo — issue gestionado.")
    else:
        for i in abiertos:
            gh("issue", "comment", str(i["number"]), "--body", "El refresco volvio a correr bien (" + stamp + "). Cierro.")
            gh("issue", "close", str(i["number"]))
        print("Refresco sano.")

if __name__ == "__main__":
    main()
