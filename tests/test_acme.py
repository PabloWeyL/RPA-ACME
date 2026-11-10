import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from config.base_datos import BaseDatos
from config.configuracion import Configuracion, ROOT
from integraciones.filtros import Filtro, leer_filtros, seleccionar_items
from integraciones.bloqueo import bloqueo_proyecto
from integraciones.navegador import ErrorAutenticacion, Navegador, SesionCaducada
from integraciones.proceso import con_sesion, consultar
from integraciones.work_items import DETALLE_JS, LISTADO_JS, WorkItems, script_selectores
from automatizacion.procesos import CanalInterrumpido


def item(wiid, tipo="WI5", estado="Open"):
    return {"wiid": str(wiid), "descripcion": "Trabajo " + str(wiid), "tipo": tipo, "estado_web": estado, "fecha_web": "2026-10-28", "url": "https://acme-test.uipath.com/work-items/" + str(wiid)}


class NavegadorFalso:
    def __init__(self, config, items=None, errores=None, login_error=False):
        self.config = config
        self.selectores = json.loads((ROOT / "config/selectores.json").read_text())
        self.items = items if items is not None else [item(i) for i in range(1, 13)]
        self.errores = errores or {}
        self.login_error = login_error
        self.cerrado = False
        self.logins = 0
        self.url = ""

    def abrir(self):
        pass

    def cerrar(self):
        self.cerrado = True

    def login(self):
        self.logins += 1
        if self.login_error:
            raise ErrorAutenticacion("Credenciales rechazadas")

    def ir(self, url):
        self.url = url

    def comprobar_sesion(self):
        pass

    def estado(self):
        return {"url": self.url, "login": False}

    def esperar(self, condicion, mensaje):
        respuesta = condicion()
        if not respuesta:
            raise TimeoutError(mensaje)
        return respuesta

    def js(self, script):
        if "var tabla=" in script:
            pagina2 = "page=2" in self.url
            return {"items": self.items[6:] if pagina2 else self.items[:6], "siguiente": None if pagina2 or len(self.items) <= 6 else self.config.url + "/work-items?page=2"}
        wiid = self.url.rsplit("/", 1)[-1]
        if self.errores.get(wiid, 0):
            self.errores[wiid] -= 1
            raise RuntimeError("Fallo temporal de detalle")
        return "Work Item Details\nWIID: " + wiid + "\nDatos visibles de prueba"

    def captura(self, ejecucion, wiid):
        return None


class FlujoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="acme-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Configuracion(root=self.root, email="prueba@example.test", password="secreto-prueba", timeout=1)
        (self.root / "input").mkdir()
        self.escribir_filtros([["tipo", "estado"], [None, None]])

    def escribir_filtros(self, filas):
        libro = Workbook()
        for fila in filas:
            libro.active.append(fila)
        libro.save(self.root / "input/filtros.xlsx")
        libro.close()

    def ejecutar(self, items=None, errores=None, login_error=False, limite=10):
        navegador = NavegadorFalso(self.config, items, errores, login_error)
        resumen, reporte = consultar(self.config, limite, lambda c: navegador, pausa=lambda _: None)
        self.assertTrue(navegador.cerrado)
        return resumen, reporte, navegador

    def test_filtros_and_or_vacios_normalizados_y_sin_duplicados(self):
        items = [item(10), item(2, "WI4"), item(1, "WI5", "Completed"), item(10)]
        seleccion = seleccionar_items(items, [Filtro(" wi5 ", "open"), Filtro("WI4", "")])
        self.assertEqual([x["wiid"] for x in seleccion], ["2", "10"])
        self.assertEqual(len(seleccionar_items(items, [Filtro()])), 3)

    def test_excel_cabecera_y_blancos(self):
        self.assertEqual(leer_filtros(self.root / "input/filtros.xlsx"), [Filtro()])
        self.escribir_filtros([["otro", "estado"], ["WI5", "Open"]])
        with self.assertRaisesRegex(ValueError, "encabezados"):
            leer_filtros(self.root / "input/filtros.xlsx")

    def test_diez_detalles_paginacion_reporte_y_estados(self):
        resumen, reporte, _ = self.ejecutar()
        self.assertEqual((resumen["estado"], resumen["coincidencias"], resumen["leidos"]), ("completado", 12, 10))
        with BaseDatos(self.config.ruta(self.config.db)).conexion() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM resultados").fetchone()[0], 10)
            self.assertEqual(con.execute("SELECT estado FROM details").fetchone()[0], "leido")
            self.assertEqual(con.execute("SELECT DISTINCT estado FROM cola").fetchall()[0][0], "leido")
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(con.execute('SELECT reporte_pendiente FROM ejecuciones').fetchone()[0], 0)
        libro = load_workbook(reporte, read_only=True)
        self.assertEqual(libro.sheetnames, ["resultados", "errores", "resumen"])
        self.assertEqual(libro["resultados"].max_row, 11)
        libro.close()

    def test_ejecuciones_independientes_conservan_historial(self):
        primero, _, _ = self.ejecutar(limite=2)
        segundo, _, _ = self.ejecutar(limite=2)
        self.assertNotEqual(primero["id"], segundo["id"])
        with BaseDatos(self.config.ruta(self.config.db)).conexion() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ejecuciones").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM resultados").fetchone()[0], 4)

    def test_cero_coincidencias_es_valido(self):
        resumen, reporte, _ = self.ejecutar(items=[])
        self.assertEqual((resumen["estado"], resumen["leidos"]), ("completado", 0))
        libro = load_workbook(reporte, read_only=True)
        self.assertEqual(libro["resultados"].max_row, 1)
        libro.close()

    def test_error_tecnico_reintenta_y_continua(self):
        resumen, _, _ = self.ejecutar(items=[item(1), item(2)], errores={"1": 99})
        self.assertEqual((resumen["estado"], resumen["leidos"], resumen["errores"]), ("con_errores", 1, 1))
        with BaseDatos(self.config.ruta(self.config.db)).conexion() as con:
            self.assertEqual(con.execute("SELECT intentos FROM cola WHERE wiid='1'").fetchone()[0], 3)

    def test_error_transitorio_se_recupera(self):
        resumen, _, _ = self.ejecutar(items=[item(1)], errores={"1": 1})
        self.assertEqual(resumen["leidos"], 1)
        with BaseDatos(self.config.ruta(self.config.db)).conexion() as con:
            self.assertEqual(con.execute("SELECT intentos FROM cola").fetchone()[0], 2)

    def test_motor_interrumpido_no_reintenta_y_resuelve_pendientes(self):
        navegador = NavegadorFalso(self.config, items=[item(1), item(2)])
        with patch.object(WorkItems, 'detalle', side_effect=CanalInterrumpido('Motor interrumpido')) as detalle:
            resumen, _ = consultar(self.config, fabrica_navegador=lambda _: navegador, pausa=lambda _: None)
        self.assertEqual(detalle.call_count, 1)
        self.assertEqual((resumen['estado'], resumen['errores']), ('error', 2))
        self.assertTrue(navegador.cerrado)

    def test_excel_invalido_se_registra_y_no_abre_navegador(self):
        self.escribir_filtros([["incorrecto"]])
        with patch("integraciones.proceso.Navegador") as fabrica:
            resumen, _ = consultar(self.config, 10, fabrica)
            fabrica.assert_not_called()
        self.assertEqual(resumen["estado"], "error")
        with BaseDatos(self.config.ruta(self.config.db)).conexion() as con:
            self.assertEqual(con.execute("SELECT estado FROM details").fetchone()[0], "error")

    def test_credenciales_invalidas_detienen_sin_reintentos(self):
        resumen, _, navegador = self.ejecutar(login_error=True)
        self.assertEqual(resumen["estado"], "error")
        self.assertEqual(navegador.logins, 1)

    def test_esperar_respeta_timeout_sin_elemento(self):
        (self.root / "config").mkdir()
        (self.root / "config/selectores.json").write_text((ROOT / "config/selectores.json").read_text())
        navegador = Navegador(self.config)
        inicio = time.monotonic()
        with self.assertRaises(TimeoutError):
            navegador.esperar(lambda: False, "La condicion no termino")
        self.assertLess(time.monotonic() - inicio, 4)

    def test_ir_aplica_timeout_para_la_navegacion(self):
        (self.root / "config").mkdir()
        (self.root / "config/selectores.json").write_text((ROOT / "config/selectores.json").read_text())
        navegador = Navegador(self.config)
        with patch.object(navegador, "robot") as robot:
            robot.url.side_effect = TimeoutError("Motor sin respuesta")
            with self.assertRaises(TimeoutError):
                navegador.ir(self.config.url + "/work-items")

    def test_esperar_devuelve_el_valor_de_la_condicion_valida(self):
        (self.root / "config").mkdir()
        (self.root / "config/selectores.json").write_text((ROOT / "config/selectores.json").read_text())
        navegador = Navegador(self.config)
        self.assertEqual(navegador.esperar(lambda: "listo", "mensaje"), "listo")

    def test_caducidad_de_sesion_reautentica(self):
        navegador = NavegadorFalso(self.config)
        respuestas = iter([SesionCaducada("Sesion caducada"), "detalle"])
        def operacion():
            valor = next(respuestas)
            if isinstance(valor, Exception):
                raise valor
            return valor
        self.assertEqual(con_sesion(navegador, operacion), "detalle")
        self.assertEqual(navegador.logins, 1)

    def test_guardado_es_atomico(self):
        base = BaseDatos(self.config.ruta(self.config.db))
        ejecucion, archivo_id = base.iniciar(self.root / "input/filtros.xlsx", 1)
        base.encolar(ejecucion, archivo_id, [item(1)], 1)
        tarea = base.tareas(ejecucion)[0]
        with base.conexion() as con:
            con.execute("CREATE TRIGGER impedir BEFORE UPDATE OF estado ON cola WHEN NEW.estado='leido' BEGIN SELECT RAISE(ABORT,'fallo'); END")
        with self.assertRaises(sqlite3.Error):
            base.guardar(tarea, "Detalle valido")
        with base.conexion() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM resultados").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT estado FROM cola").fetchone()[0], "pendiente")

    def test_ciclo_de_paginacion_no_se_ignora(self):
        navegador = NavegadorFalso(self.config)
        with patch.object(navegador, "js", return_value={"items": [], "siguiente": self.config.url + "/work-items"}):
            with self.assertRaisesRegex(RuntimeError, "ciclo"):
                WorkItems(navegador).listar()

    def test_no_se_navega_fuera_de_acme(self):
        self.config.root = ROOT
        navegador = Navegador(self.config)
        with self.assertRaisesRegex(ValueError, "fuera del origen"):
            navegador.ir("https://example.test/work-items/1")

    def test_credenciales_no_se_incluyen_en_comandos_tagui(self):
        (self.root / "config").mkdir()
        (self.root / "config/selectores.json").write_text((ROOT / "config/selectores.json").read_text())
        (self.root / ".runtime").mkdir()
        navegador = Navegador(self.config)
        with patch.object(navegador, "robot") as robot, patch.object(navegador, "estado", side_effect=[{"login": True, "url": self.config.url + "/login"}, {"login": False, "url": self.config.url + "/home"}]):
            robot.url.return_value = robot.present.return_value = robot.click.return_value = robot.send.return_value = True
            navegador.login()
        comandos = repr(robot.mock_calls)
        self.assertNotIn(self.config.password, comandos)
        self.assertNotIn(self.config.email, comandos)
        self.assertEqual(robot.send.call_count, 2)
        self.assertFalse((self.root / ".runtime/credenciales.json").exists())

    def test_login_reutiliza_sesion_existente_sin_escribir_credenciales(self):
        self.config.root = ROOT
        navegador = Navegador(self.config)
        with patch.object(navegador, "robot") as robot, patch.object(navegador, "estado", return_value={"login": False, "url": self.config.url + "/home"}):
            robot.url.return_value = robot.present.return_value = True
            navegador.login()
        robot.send.assert_not_called()

    def test_redaccion_de_secretos(self):
        self.assertEqual(self.config.ocultar(self.config.email + " " + self.config.password), "[OCULTO] [OCULTO]")

    def test_bloqueo_impide_dos_consultas_simultaneas(self):
        ruta = self.root / ".runtime/consulta.lock"
        with bloqueo_proyecto(ruta):
            with self.assertRaisesRegex(RuntimeError, "Ya hay una consulta"):
                with bloqueo_proyecto(ruta):
                    self.fail("El segundo proceso no debe adquirir el bloqueo")
        with bloqueo_proyecto(ruta):
            pass

    def test_ejecucion_interrumpida_se_recupera_sin_borrar_datos(self):
        base = BaseDatos(self.config.ruta(self.config.db))
        ejecucion, archivo_id = base.iniciar(self.root / "input/filtros.xlsx", 2)
        base.encolar(ejecucion, archivo_id, [item(1), item(2)], 2)
        base.guardar(base.tareas(ejecucion)[0], "Detalle WIID 1")
        self.assertEqual(base.recuperar_interrumpidas(), [ejecucion])
        with base.conexion() as con:
            registro = con.execute("SELECT estado,leidos FROM ejecuciones WHERE id=?", (ejecucion,)).fetchone()
            self.assertEqual(tuple(registro), ("error", 1))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM resultados").fetchone()[0], 1)
        from integraciones.reportes import generar_reporte
        generar_reporte(base, ejecucion, self.config.carpeta_reportes)
        base.confirmar_reporte(ejecucion)
        self.assertEqual(base.recuperar_interrumpidas(), [])

    def test_detalle_vacio_no_se_marca_leido(self):
        navegador = NavegadorFalso(self.config, items=[item(1)])
        original = navegador.js
        navegador.js = lambda script: original(script) if "var tabla=" in script else ""
        resumen, _ = consultar(self.config, 10, lambda _: navegador, pausa=lambda _: None)
        self.assertEqual((resumen["leidos"], resumen["errores"]), (0, 1))


class DOMTests(unittest.TestCase):
    def test_tabla_realista_detalle_y_estado_vacio_en_chrome(self):
        selectores = json.loads((ROOT / "config/selectores.json").read_text())["work_items"]
        cabecera = '<tr><th>Actions</th><th>WIID</th><th>Description</th><th>Type</th><th>Status</th><th>Date</th></tr>'
        fila = '<tr><td><a href="https://acme-test.uipath.com/work-items/12">Ver</a></td><td>12</td><td>Descripcion</td><td>WI5</td><td>Open</td><td>2026-10-28</td></tr>'
        casos = [
            {"html": '<table><tbody>' + cabecera + fila + '</tbody></table><ul class="pagination"><li><a rel="next" href="https://acme-test.uipath.com/work-items?page=2">Next</a></li></ul>', "script": script_selectores(LISTADO_JS, selectores)},
            {"html": '<table><tbody>' + cabecera + '</tbody></table>', "script": script_selectores(LISTADO_JS, selectores)},
            {"html": '<div class="main-container"><h1>Work Item Details</h1><p>WIID: 12</p></div>', "script": script_selectores(DETALLE_JS, selectores)},
        ]
        run = subprocess.run(["node", str(ROOT / "tests/browser_dom.cjs")], input=json.dumps(casos), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        resultados = [json.loads(fila["result"]) for fila in json.loads(run.stdout)]
        self.assertEqual(resultados[0]["items"][0]["wiid"], "12")
        self.assertEqual(resultados[0]["siguiente"], "https://acme-test.uipath.com/work-items?page=2")
        self.assertEqual(resultados[1]["items"], [])
        self.assertIsNone(resultados[1]["siguiente"])
        self.assertIn("WIID: 12", resultados[2])


if __name__ == "__main__":
    unittest.main()
