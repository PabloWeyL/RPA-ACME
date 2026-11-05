import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

from automatizacion.procesos import ProcesoAislado

from tools.preparar_tagui import preparar


class ErrorAutenticacion(RuntimeError):
    pass


class SesionCaducada(RuntimeError):
    pass


class Navegador:
    def __init__(self, config, robot=None):
        self.robot = robot if robot is not None else ProcesoAislado('rpa', config.timeout)
        self.config = config
        self.selectores = config.selectores()
        self.iniciado = False

    def abrir(self):
        runtime = preparar(self.config.root)
        self.robot.directorio = str(runtime)
        self.robot.tagui_location(str(runtime))
        self.robot.debug(False)
        if not self.robot.init(chrome_browser=True, headless_mode=self.config.headless, turbo_mode=True):
            raise RuntimeError("No se pudo iniciar Chrome mediante RPA/TagUI")
        self.iniciado = True
        self.robot.error(False)
        self.robot.timeout(self.config.timeout)

    def js(self, cuerpo):
        protegido = "try { return (function(){" + cuerpo + "})(); } catch(e) {return JSON.stringify({__error:e.message});}"
        resultado = self.robot.dom(" ".join(protegido.splitlines()))
        if resultado is None or str(resultado).strip() == "":
            raise RuntimeError("El navegador no devolvio una respuesta")
        dato = json.loads(str(resultado))
        if isinstance(dato, dict) and dato.get("__error"):
            raise RuntimeError("Error al leer el DOM: " + dato["__error"])
        return dato

    def estado(self):
        return self.js("return JSON.stringify({url:location.href,login:!!document.querySelector(" + json.dumps(self.selectores["login"]["password"]) + ")});")

    def con_timeout(self, accion, mensaje, timeout):
        # Las llamadas al motor tienen plazo en el canal entre procesos.
        # Las condiciones se ejecutan en serie y no dejan hilos atrasados.
        with self.robot.plazo(timeout):
            return accion()

    def esperar(self, condicion, mensaje):
        fin = time.monotonic() + self.config.timeout
        while time.monotonic() < fin:
            restante = fin - time.monotonic()
            valor = self.con_timeout(condicion, mensaje, max(restante, 0))
            if valor:
                return valor
            time.sleep(0.3)
        raise TimeoutError(mensaje)

    def ir(self, url):
        origen = urlparse(self.config.url)
        destino = urlparse(url)
        if (destino.scheme, destino.netloc) != (origen.scheme, origen.netloc):
            raise ValueError("Se rechazo un enlace fuera del origen de ACME")
        if not self.con_timeout(lambda: self.robot.url(url), "No se pudo abrir la pagina solicitada de ACME", self.config.timeout):
            raise RuntimeError("No se pudo abrir la pagina solicitada de ACME")

    def comprobar_sesion(self):
        estado = self.estado()
        if estado["login"] or "/login" in urlparse(estado["url"]).path:
            raise SesionCaducada("La sesion de ACME caduco")

    def login(self):
        if not self.config.email or not self.config.password:
            raise ErrorAutenticacion("Faltan ACME_EMAIL o ACME_PASSWORD en .env")
        datos = self.selectores["login"]
        self.ir(self.config.url + datos["ruta"])
        def formulario_o_sesion():
            estado = self.estado()
            if not estado["login"] and "/login" not in urlparse(estado["url"]).path and self.robot.present(datos["logout"]):
                return "sesion"
            if self.robot.present(datos["email"]) and self.robot.present(datos["password"]):
                return "formulario"
            return None
        if self.esperar(formulario_o_sesion, "No aparecio el formulario de acceso") == "sesion":
            return
        credenciales = self.config.root / ".runtime/credenciales.json"
        try:
            credenciales.write_text(json.dumps({"email": self.config.email, "password": self.config.password}), encoding="utf-8")
            os.chmod(credenciales, 0o600)
            for campo in ("email", "password"):
                if not self.robot.click(datos[campo]):
                    raise ErrorAutenticacion("No se pudo enfocar el formulario de acceso")
                # Los comandos y sus ecos contienen la ruta, nunca el secreto.
                codigo = "js var acmeCred=JSON.parse(require('fs').read(" + json.dumps(str(credenciales).replace("\\", "/")) + ")); chrome_step('Input.insertText',{text:acmeCred." + campo + "}); acmeCred=null;"
                if not self.robot.send(codigo):
                    raise ErrorAutenticacion("No se pudo completar el formulario de acceso")
            if not self.robot.click(datos["enviar"]):
                raise ErrorAutenticacion("No se pudo enviar el formulario de acceso")

            def acceso():
                estado = self.estado()
                if not estado["login"] and "/login" not in urlparse(estado["url"]).path:
                    return True
                errores = self.js("return JSON.stringify(Array.prototype.map.call(document.querySelectorAll(" + json.dumps(datos["error"]) + "),function(x){return x.innerText.trim();}).filter(Boolean));")
                if errores:
                    raise ErrorAutenticacion("ACME rechazo el acceso; revisa las credenciales")
                return False
            self.esperar(acceso, "No se confirmo el inicio de sesion")
        finally:
            credenciales.unlink(missing_ok=True)

    def captura(self, ejecucion, wiid):
        try:
            ruta = self.config.ruta(self.config.capturas) / ejecucion / (str(wiid) + ".png")
            ruta.parent.mkdir(parents=True, exist_ok=True)
            return str(ruta) if self.robot.snap("page", str(ruta)) else None
        except Exception:
            return None

    def carpeta_descargas(self, carpeta):
        carpeta = Path(carpeta).resolve()
        carpeta.mkdir(parents=True, exist_ok=True)
        parametros = json.dumps({"behavior": "allow", "downloadPath": str(carpeta)})
        codigo = "js var acmeDownload=JSON.parse(chrome_step('Page.setDownloadBehavior'," + parametros + ")); if(acmeDownload.error) throw new Error('No se pudo configurar la descarga');"
        if not self.con_timeout(lambda: self.robot.send(codigo), "No se pudo configurar la descarga de Chrome", self.config.timeout):
            raise RuntimeError("No se pudo configurar la carpeta de descarga de Chrome")

    def cerrar(self):
        try:
            if self.iniciado:
                try:
                    if not self.estado()["login"]:
                        if not self.robot.click(self.selectores["login"]["logout"]):
                            raise RuntimeError("No se pudo enviar el cierre de sesion")
                        self.esperar(lambda: self.estado()["login"], "No se confirmo el cierre de sesion")
                finally:
                    self.robot.send("js chrome.getCurrentUrl=function(){return '';}; chrome.getTitle=function(){return '';};")
                    self.robot.send("js chrome_step('Browser.close',{});")
                    self.robot.close()
        finally:
            self.iniciado = False
            self.robot.cerrar()
