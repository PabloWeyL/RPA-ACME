import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from config.configuracion import Configuracion, ROOT
from integraciones.navegador import ErrorAutenticacion, SesionCaducada
from integraciones.proceso_mensual import HistorialMensual, descargar_reportes
from integraciones.reportes_mensuales import (
    ENVIAR_JS, PREPARAR_JS, ReportesMensuales, SinReporte, SolicitudMensual,
    SolicitudRechazada, leer_solicitudes, validar_archivo,
)
from integraciones.work_items import script_selectores
from automatizacion.procesos import CanalInterrumpido


class NavegadorMensualFalso:
    def __init__(self, config):
        self.config = config
        self.selectores = config.selectores()
        self.cerrado = False
        self.logins = 0
        self.carpeta = None
        self.alerta = ''

    def abrir(self):
        pass

    def login(self):
        self.logins += 1

    def ir(self, url):
        self.url = url

    def carpeta_descargas(self, carpeta):
        self.carpeta = carpeta

    def comprobar_sesion(self):
        pass

    def esperar(self, condicion, mensaje):
        for _ in range(3):
            respuesta = condicion()
            if respuesta:
                return respuesta
        raise TimeoutError(mensaje)

    def js(self, script):
        if 'enviar.click()' in script:
            (self.carpeta / 'reporte.csv').write_text('Invoice,Amount\nA1,100\n')
            return True
        if 'var errores=' in script:
            return {'alerta': self.alerta, 'error': ''}
        return True

    def captura(self, ejecucion, identificador):
        return None

    def cerrar(self):
        self.cerrado = True


class ReportesMensualesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='acme-mensuales-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Configuracion(root=self.root, timeout=1)
        # Los selectores del sitio pertenecen a la instalacion, no a los datos temporales.
        (self.root / 'config').mkdir()
        (self.root / 'config/selectores.json').write_text((ROOT / 'config/selectores.json').read_text())
        self.entrada = self.root / 'inputs/reportes_mensuales.xlsx'
        self.entrada.parent.mkdir()
        self.escribir([['00123', 2025, 1, 3]])
        self.navegador = NavegadorMensualFalso(self.config)

    def escribir(self, filas, cabecera=None):
        libro = Workbook()
        libro.active.append(cabecera or ['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'])
        for fila in filas:
            libro.active.append(fila)
        libro.save(self.entrada)
        libro.close()

    def ejecutar(self, limite=None):
        return descargar_reportes(self.config, limite=limite, fabrica_navegador=lambda c: self.navegador, pausa=lambda _: None)

    def test_periodos_inclusivos_y_solapados_sin_duplicados(self):
        self.escribir([['00123', 2025, 1, 3], [None, None, None, None], ['00123', 2025, 2, 4], ['B', 2024, 12, 12]])
        solicitudes = leer_solicitudes(self.entrada)
        self.assertEqual([(s.proveedor_taxid, s.mes) for s in solicitudes], [('00123', 1), ('00123', 2), ('00123', 3), ('00123', 4), ('B', 12)])
        self.assertEqual(solicitudes[3].fila, 4)

    def test_entrada_invalida_no_abre_navegador(self):
        for fila in ([None, 2025, 1, 2], ['A', 2025, 0, 2], ['A', 2025, 3, 2], ['A', 2025, 1, 13], ['A', 2025, 1.5, 2], ['A', None, 1, 2]):
            with self.subTest(fila=fila):
                self.escribir([fila])
                with self.assertRaises(ValueError):
                    leer_solicitudes(self.entrada)
        with patch.object(self.navegador, 'abrir') as abrir:
            resumen, reporte = self.ejecutar()
            abrir.assert_not_called()
        self.assertEqual(resumen['estado'], 'error')
        self.assertTrue(reporte.is_file())

    def test_cabeceras_requeridas_y_entrada_sin_solicitudes(self):
        for cabecera in (['proveedor_taxid', 'anio', 'mes_desde'], ['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta', 'anio']):
            self.escribir([], cabecera)
            with self.assertRaisesRegex(ValueError, 'encabezados'):
                leer_solicitudes(self.entrada)
        self.escribir([])
        with self.assertRaisesRegex(ValueError, 'no contiene'):
            leer_solicitudes(self.entrada)

    def test_descarga_archivos_historial_y_excel(self):
        resumen, reporte = self.ejecutar(limite=2)
        self.assertEqual((resumen['estado'], resumen['seleccionados'], resumen['descargado']), ('completado', 2, 2))
        self.assertTrue(self.navegador.cerrado)
        historial = HistorialMensual(self.config.ruta(self.config.db))
        tareas = historial.tareas(resumen['id'])
        for tarea in tareas:
            archivo = Path(tarea['archivo'])
            self.assertTrue(archivo.is_file())
            self.assertIn('00123', archivo.name)
            self.assertEqual(tarea['intentos'], 1)
        with historial.base.conexion() as con:
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])
        libro = load_workbook(reporte, read_only=True)
        self.assertEqual(libro.sheetnames, ['resultados', 'errores', 'resumen'])
        self.assertEqual(libro['resultados'].max_row, 3)
        libro.close()
        segundo, _ = self.ejecutar(limite=1)
        self.assertNotEqual(segundo['id'], resumen['id'])
        self.assertEqual(len(historial.tareas(resumen['id'])), 2)

    def test_sin_reporte_se_registra_sin_reintentos(self):
        with patch.object(ReportesMensuales, 'descargar', side_effect=SinReporte('No report found')) as descargar:
            resumen, _ = self.ejecutar(limite=1)
        self.assertEqual((resumen['estado'], resumen['sin_reporte'], resumen['descargado']), ('completado', 1, 0))
        self.assertEqual(descargar.call_count, 1)

    def test_fallo_tecnico_reintenta_y_continua(self):
        real = ReportesMensuales.descargar
        fallos = []
        def descargar(cliente, solicitud, carpeta):
            if solicitud.mes == 1:
                fallos.append(1)
                raise RuntimeError('Fallo temporal')
            return real(cliente, solicitud, carpeta)
        with patch.object(ReportesMensuales, 'descargar', descargar):
            resumen, _ = self.ejecutar(limite=2)
        self.assertEqual((resumen['estado'], resumen['descargado'], resumen['errores']), ('con_errores', 1, 1))
        self.assertEqual(len(fallos), 3)

    def test_caducidad_reautentica_con_carpeta_nueva(self):
        with patch.object(self.navegador, 'comprobar_sesion', side_effect=[SesionCaducada('caducada'), None, None]):
            resumen, _ = self.ejecutar(limite=1)
        self.assertEqual(resumen['descargado'], 1)
        self.assertEqual(self.navegador.logins, 2)

    def test_rechazo_de_autenticacion_detiene_pendientes(self):
        with patch.object(ReportesMensuales, 'descargar', side_effect=ErrorAutenticacion('rechazadas')) as descargar:
            resumen, _ = self.ejecutar(limite=2)
        self.assertEqual((resumen['estado'], resumen['errores']), ('error', 2))
        self.assertEqual(descargar.call_count, 1)
        self.assertTrue(self.navegador.cerrado)

    def test_motor_interrumpido_no_reintenta_y_resuelve_pendientes(self):
        with patch.object(ReportesMensuales, 'descargar', side_effect=CanalInterrumpido('Motor interrumpido')) as descargar:
            resumen, _ = self.ejecutar(limite=2)
        self.assertEqual(descargar.call_count, 1)
        self.assertEqual((resumen['estado'], resumen['errores']), ('error', 2))
        self.assertTrue(self.navegador.cerrado)

    def test_rechazo_de_periodo_no_se_reintenta(self):
        with patch.object(ReportesMensuales, 'descargar', side_effect=SolicitudRechazada('Anio no disponible')) as descargar:
            resumen, _ = self.ejecutar(limite=1)
        self.assertEqual(resumen['errores'], 1)
        self.assertEqual(descargar.call_count, 1)

    def test_timeout_identifica_el_control_faltante(self):
        with patch.object(self.navegador, 'js', return_value={'faltantes': ['enviar']}):
            with self.assertRaisesRegex(TimeoutError, 'formulario mensual: enviar'):
                ReportesMensuales(self.navegador).descargar(
                    SolicitudMensual('A', 2025, 1, 2), self.root / 'faltante')

    def test_no_acepta_html_archivo_vacio_o_descarga_parcial(self):
        ruta = self.root / 'reporte.csv'
        for contenido in ('', '<!doctype html><html>Error</html>'):
            ruta.write_text(contenido)
            with self.assertRaises(RuntimeError):
                validar_archivo(ruta)
        ruta.write_text('<html><form>login password</form></html>')
        with self.assertRaises(SesionCaducada):
            validar_archivo(ruta)
        original = self.navegador.js
        def js(script):
            if 'enviar.click()' in script:
                (self.navegador.carpeta / 'reporte.csv.crdownload').write_text('parcial')
                return True
            return original(script)
        with patch.object(self.navegador, 'js', side_effect=js):
            with self.assertRaises(TimeoutError):
                ReportesMensuales(self.navegador).descargar(SolicitudMensual('A', 2025, 1, 2), self.root / 'parcial')

    def test_alerta_sin_reporte(self):
        original = self.navegador.js
        def js(script):
            if 'enviar.click()' in script:
                self.navegador.alerta = 'No report found for this month'
                return True
            return original(script)
        with patch.object(self.navegador, 'js', side_effect=js):
            resumen, _ = self.ejecutar(limite=1)
        self.assertEqual(resumen['sin_reporte'], 1)

    def test_recupera_interrumpidas_conservando_confirmadas(self):
        historial = HistorialMensual(self.config.ruta(self.config.db))
        ejecucion = historial.iniciar(self.entrada, None)
        historial.encolar(ejecucion, leer_solicitudes(self.entrada))
        tarea = historial.tareas(ejecucion)[0]
        historial.resolver(tarea['id'], 'descargado', archivo='confirmado.csv')
        historial.recuperar_interrumpidas(self.root / 'reportes')
        resumen = historial.resumen(ejecucion)
        self.assertEqual((resumen['estado'], resumen['descargado'], resumen['errores']), ('error', 1, 2))
        self.assertEqual(len(list((self.root / 'reportes').glob('reportes_mensuales_*.xlsx'))), 1)


class FormularioMensualDOMTests(unittest.TestCase):
    def test_controles_acme_bootstrap_y_boton_por_id(self):
        selectores = Configuracion().selectores()['reportes_mensuales']
        html = '''<form id="searchForm" novalidate>
        <div class="controls"><label>Vendor TaxID:</label><input id="vendorTaxID" name="vendorTaxID"></div>
        <div class="controls"><label>Month:</label><br>
        <div class="btn-group bootstrap-select"><button type="button" data-id="reportMonth"><span>---</span></button>
        <select name="reportMonth" id="reportMonth" class="selectpicker" tabindex="-98" style="display:none">
        <option value="">---</option><option value="1">January</option><option value="6">June</option></select></div></div>
        <div class="controls"><label>Year:</label><br>
        <div class="btn-group bootstrap-select"><button type="button" data-id="reportYear"><span>---</span></button>
        <select name="reportYear" id="reportYear" class="selectpicker" tabindex="-98" style="display:none">
        <option value="">---</option><option value="2026">2026</option><option value="2025">2025</option></select></div></div>
        <button type="button" id="buttonDownload">Download Report</button></form>'''
        # Simula solo la API del plugin: comprueba que se actualiza el control
        # visual y que el handler del boton recibe los valores de los selects.
        setup = '''window.cambios=[];window.renders=[];
        window.jQuery=function(e){return {data:function(){return true;},selectpicker:function(accion){
            window.renders.push([e.id,accion]);document.querySelector('[data-id="'+e.id+'"] span').textContent=e.selectedOptions[0].text;}};};
        window.jQuery.fn={selectpicker:function(){}};
        document.querySelectorAll('select').forEach(function(e){e.addEventListener('change',function(){window.cambios.push(e.id);});});
        document.querySelector('#buttonDownload').onclick=function(){window.enviado=[document.querySelector('#vendorTaxID').value,
            document.querySelector('#reportMonth').value,document.querySelector('#reportYear').value];};'''
        preparar = 'var solicitud={proveedor_taxid:"00123",mes:6,anio:2025};' + script_selectores(PREPARAR_JS, selectores)
        enviar = script_selectores(ENVIAR_JS, selectores)
        script = setup + 'var preparado=(function(){' + preparar + '})();(function(){' + enviar + '})();return JSON.stringify({preparado:preparado,enviado:window.enviado,cambios:window.cambios,renders:window.renders,mes:document.querySelector(\'[data-id="reportMonth"] span\').textContent});'
        # La identificacion por ID debe funcionar aunque cambie el texto del boton.
        otro_texto = html.replace('Download Report', 'Descargar')
        proceso = subprocess.run(['node', str(ROOT / 'tests/browser_dom.cjs')],
            input=json.dumps([{'html': h, 'script': script} for h in (html, otro_texto)]),
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(proceso.returncode, 0, proceso.stderr)
        for caso in json.loads(proceso.stdout):
            resultado = json.loads(caso['result'])
            self.assertEqual(resultado, {'preparado': 'true', 'enviado': ['00123', '6', '2025'],
                'cambios': ['reportMonth', 'reportYear'],
                'renders': [['reportMonth', 'render'], ['reportYear', 'render']], 'mes': 'June'})

    def test_formulario_por_etiquetas_opciones_y_boton(self):
        selectores = Configuracion().selectores()['reportes_mensuales']
        html = '''<form><label for="tax">Vendor TaxID:</label><input id="tax">
        <label for="mes">Month:</label><select id="mes"><option value="0">Select</option><option value="Jan">January</option></select>
        <label for="anio">Year:</label><select id="anio"><option value="2025">2025</option></select>
        <button type="button" onclick="window.enviado=true">Download Report</button></form>'''
        preparar = 'var solicitud={proveedor_taxid:"00123",mes:1,anio:2025};' + script_selectores(PREPARAR_JS, selectores)
        envio = script_selectores(ENVIAR_JS, selectores)
        script = 'var preparado=(function(){' + preparar + '})();var enviado=(function(){' + envio + '})();return JSON.stringify({preparado:preparado,tax:document.querySelector("#tax").value,mes:document.querySelector("#mes").value,enviado:window.enviado});'
        no_anio = 'var solicitud={proveedor_taxid:"00123",mes:1,anio:2024};' + script_selectores(PREPARAR_JS, selectores)
        proceso = subprocess.run(['node', str(ROOT / 'tests/browser_dom.cjs')], input=json.dumps([{'html': html, 'script': script}, {'html': html, 'script': no_anio}]), capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(proceso.returncode, 0, proceso.stderr)
        respuestas = [json.loads(x['result']) for x in json.loads(proceso.stdout)]
        self.assertEqual(respuestas[0], {'preparado': 'true', 'tax': '00123', 'mes': 'Jan', 'enviado': True})
        self.assertIn('rechazo', respuestas[1])


if __name__ == '__main__':
    unittest.main()
