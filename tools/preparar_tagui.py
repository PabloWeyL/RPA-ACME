"""Copia TagUI a un runtime privado y desactiva trazas de protocolo sensibles."""
import os
from pathlib import Path
import shutil
import socket


def preparar(root):
    if os.name != "nt":
        raise RuntimeError("Este proyecto usa el runtime Windows de TagUI")
    runtime = root / ".runtime"
    destino = runtime / "tagui"
    marca = runtime / "preparado_v2"
    if marca.exists():
        return runtime
    origen = Path(os.getenv("TAGUI_ORIGEN") or (Path(os.getenv("APPDATA", "")) / "tagui"))
    if not (origen / "src/tagui.cmd").is_file():
        raise RuntimeError("No se encuentra TagUI. Configura TAGUI_ORIGEN con su carpeta de instalacion.")

    def ignorar(carpeta, nombres):
        excluidos = {"chrome", "sikulix", "slimerjs", "samples", "media", "test", ".git", "node_modules"}
        return [nombre for nombre in nombres if nombre in excluidos or nombre.endswith((".log", ".in", ".out", ".tmp"))]

    shutil.copytree(origen, destino, ignore=ignorar, dirs_exist_ok=True)
    (destino / "src/chrome").mkdir(parents=True, exist_ok=True)
    # init() ejecuta este script. Nunca finalizar procesos de otros proyectos.
    (destino / "src/end_processes.cmd").write_text("@echo off\nexit /b 0\n", encoding="ascii")
    php = destino / "src/tagui_chrome.php"
    texto = php.read_text(encoding="utf-8-sig")
    texto = "\n".join(linea for linea in texto.splitlines() if not linea.startswith(('echo "[tagui] INPUT', 'echo "[tagui] OUTPUT')))
    php.write_text(texto, encoding="utf-8")
    # Usar otro puerto y perfil que el bot de supermercados.
    with socket.socket() as servidor:
        servidor.bind(("127.0.0.1", 0))
        puerto = servidor.getsockname()[1]
    launcher = destino / "src/tagui.cmd"
    texto = launcher.read_text(encoding="utf-8-sig").replace("9222", str(puerto))
    texto = texto.replace('start "Chrome Engine" /min cmd /c', 'start "Chrome Engine" /b cmd /c')
    launcher.write_text(texto, encoding="utf-8")
    (runtime / "puerto").write_text(str(puerto), encoding="ascii")
    marca.write_text("Runtime privado; sin registro de entradas/salidas CDP", encoding="utf-8")
    return runtime


if __name__ == "__main__":
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    print("Runtime preparado:", preparar(root))
