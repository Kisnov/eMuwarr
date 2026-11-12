#!/usr/bin/env python3
"""Fake Uploadrr-EMU CLI for Phase 10F controlled-execution testing.

TEST-ONLY. Accepts the real Uploadrr-EMU flag contract (-tk/-mt/-tmdb/-ua/
-c/-res/-season/-episode/--resume) so it can be invoked with the exact argv built by
emuwarez_uploadrr/services/uploadrr_contract.py::build_uploadrr_emu_command.

Safety:
- Never connects to eMuwarez or any network host (no urllib/requests/socket).
- Never reads real API keys or Uploadrr-EMU's config.yaml.
- Never creates a real torrent or uploads anything.
- Only prints deterministic log lines to stdout/stderr and returns an exit code.

Failure/slowness simulation (for exercising cancel/timeout/failed paths):
  --fake-fail                 exit 1 after printing an error line
  --fake-sleep SECONDS        sleep between each log line
  FAKE_UPLOADRR_FAIL=1        same as --fake-fail (env fallback)
  FAKE_UPLOADRR_SLEEP=N       same as --fake-sleep (env fallback)
  FAKE_UPLOADRR_FAIL_EARLY=1  exit 1 before any `Step done:` line
  FAKE_UPLOADRR_LINES=N       print N more stdout lines (enough to fill the pipe)
  FAKE_UPLOADRR_GRANDCHILD=F  start a child that keeps stdout/stderr open for 60 s
                              (like mkbrr/ffmpeg) and write its pid to file F

Modo con guion (casos que el flujo fijo no puede simular):
  FAKE_UPLOADRR_SCRIPT=F      F es un JSON con una lista de pasos que sustituyen al flujo fijo, en orden:
                              {"out": "línea"}   línea a stdout con salto de línea
                              {"err": "línea"}   línea a stderr
                              {"raw": "texto"}   a stdout SIN salto de línea (salida cortada a medias)
                              {"sleep": 0.1}     espera esos segundos
                              {"wait_for": "P"}  espera a que exista el fichero P (la señal la da el test)
                              {"touch": "P"}     crea el fichero P (avisa al test de que se ha llegado ahí)
                              {"exit": N}        sale ya con ese código
                              {"kill": 9}        se mata a sí mismo con esa señal (sale con -9, como un OOM-kill)
                              {"hang": true}     no sale nunca (hasta que lo maten)
                              Sin {"exit"} ni {"hang"} al final, sale con 0.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time


def _correr_guion(ruta: str) -> int:
    with open(ruta, encoding="utf-8") as f:
        pasos = json.load(f)
    for paso in pasos:
        if "out" in paso:
            print(paso["out"], flush=True)
        elif "err" in paso:
            print(paso["err"], file=sys.stderr, flush=True)
        elif "raw" in paso:
            sys.stdout.write(paso["raw"])
            sys.stdout.flush()
        elif "sleep" in paso:
            time.sleep(float(paso["sleep"]))
        elif "wait_for" in paso:
            limite = time.monotonic() + 30
            while not os.path.exists(paso["wait_for"]) and time.monotonic() < limite:
                time.sleep(0.02)
        elif "touch" in paso:
            open(paso["touch"], "w").close()
        elif "exit" in paso:
            return int(paso["exit"])
        elif "kill" in paso:
            os.kill(os.getpid(), int(paso["kill"]))
            time.sleep(5)
        elif paso.get("hang"):
            while True:
                time.sleep(1)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("path")
    parser.add_argument("-tk", "--trackers", dest="trackers", default=None)
    parser.add_argument("-mt", dest="title", default=None)
    parser.add_argument("-tmdb", dest="tmdb_id", default=None)
    parser.add_argument("-ua", dest="unattended", action="store_true")
    parser.add_argument("-c", dest="category", default=None)
    parser.add_argument("-res", dest="resolution", default=None)
    parser.add_argument("-season", dest="season", default=None)
    parser.add_argument("-episode", dest="episode", default=None)
    parser.add_argument("-t", dest="release_type", default=None)
    parser.add_argument("-fd", dest="full_dir", action="store_true")
    parser.add_argument("--resume", "-rs", dest="resume", action="store_true")
    parser.add_argument("--fake-fail", dest="fake_fail", action="store_true")
    parser.add_argument("--fake-sleep", dest="fake_sleep", type=float, default=None)
    args, _unknown = parser.parse_known_args(argv)

    fail = args.fake_fail or os.environ.get("FAKE_UPLOADRR_FAIL") == "1"
    sleep_s = args.fake_sleep
    if sleep_s is None:
        try:
            sleep_s = float(os.environ.get("FAKE_UPLOADRR_SLEEP", "0") or 0)
        except ValueError:
            sleep_s = 0.0

    pidfile = os.environ.get("FAKE_UPLOADRR_GRANDCHILD")
    if pidfile:
        hijo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with open(pidfile, "w", encoding="utf-8") as f:
            f.write(str(hijo.pid))

    def emit(line: str) -> None:
        print(line, flush=True)
        if sleep_s:
            time.sleep(sleep_s)

    if os.environ.get("FAKE_UPLOADRR_SCRIPT"):
        return _correr_guion(os.environ["FAKE_UPLOADRR_SCRIPT"])

    if os.environ.get("FAKE_UPLOADRR_FAIL_EARLY") == "1":
        print("[EMU] ERROR: fallo simulado antes de terminar ningún paso", file=sys.stderr, flush=True)
        return 1

    # Las líneas son las del Uploadrr-EMU real, copiadas literales: src/trackers/UNIT3D.py, src/utils/checks.py,
    # src/utils/meta.py (Step done), src/utils/upload.py, src/clients.py.
    emit("Searching for existing torrents on EMU...")
    emit("No dupes found")
    # Las líneas fijas con que el Uploadrr-EMU real (upload.py) dice que ha terminado un paso.
    print("Step done: screenshots", flush=True)
    print("Step done: torrent", flush=True)
    emit("Uploading to EMU")
    for i in range(int(os.environ.get("FAKE_UPLOADRR_LINES", "0") or 0)):
        print(f"[EMU] hashing {i:06d} " + "." * 80)

    if fail:
        print("[EMU] ERROR: fallo simulado durante la subida (--fake-fail)", file=sys.stderr, flush=True)
        return 1

    # La línea fija que imprime el Uploadrr-EMU real (src/trackers/UNIT3D.py), antes del aviso de éxito.
    print("Torrent URL: https://fake-emu.invalid.test/torrents/999999", flush=True)
    emit("Torrent uploaded successfully!")
    emit("Adding and rechecking torrent")
    emit("Added to: /downloads/fake")
    return 0


if __name__ == "__main__":
    sys.exit(main())
