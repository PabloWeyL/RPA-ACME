from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook, load_workbook

from automatizacion.archivos import validar_tabla
from automatizacion.ejecucion import finalizar_con_reporte, reintentar
from automatizacion.excel import escribir_reporte, ruta_reporte
from automatizacion.procesos import CanalInterrumpido, ProcesoAislado
from config.base_datos import BaseDatos
from config.configuracion import Configuracion
from integraciones.intercambio import HistorialIntercambio
from integraciones.reportes import generar_reporte
from integraciones.proceso_mensual import HistorialMensual
from integraciones.reportes_mensuales import SolicitudMensual
from test_acme import item


class ComponentesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_nombre_reporte_fecha_hora_y_sin_colision_entre_ejecuciones(self):
        inicio = '2026-11-05T14:30:00-03:00'
        primero = ruta_reporte(self.root, 'work_items', 'ejecucion_1', inicio)
        segundo = ruta_reporte(self.root, 'work_items', 'ejecucion_2', inicio)
        self.assertRegex(primero.name, r'^work_items_05112026-143000_[a-f0-9]{12}\.xlsx$')
        self.assertNotEqual(primero, segundo)
        self.assertEqual(primero, ruta_reporte(self.root, 'work_items', 'ejecucion_1', inicio))
        escribir_reporte(primero, [('datos', ['valor'], [['primero']])])
        escribir_reporte(segundo, [('datos', ['valor'], [['segundo']])])
        self.assertEqual(len(list(self.root.glob('*.xlsx'))), 2)

    def test_historiales_crean_solo_sus_tablas(self):
        mensual = HistorialMensual(self.root / 'mensual.db')
        intercambio = HistorialIntercambio(Configuracion(root=self.root, db=Path('api.db')))
        for base, esperadas in (
            (mensual.base, {'ejecuciones_mensuales', 'descargas_mensuales'}),
            (intercambio.base, {'intercambios_api'}),
        ):
            with base.conexion() as con:
                tablas = {fila[0] for fila in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tablas, esperadas)

    def test_interrupcion_antes_del_excel_se_recupera_sin_repetir_lecturas(self):
        base = BaseDatos(self.root / 'work.db')
        ejecucion, archivo = base.iniciar(self.root / 'entrada.xlsx', None)
        base.encolar(ejecucion, archivo, [item(1)], 1)
        base.guardar(base.tareas(ejecucion)[0], 'Detalle confirmado')
        generar = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            finalizar_con_reporte(lambda error: base.finalizar(ejecucion, error), generar, Mock(), str,
                                 confirmar_reporte=lambda: base.confirmar_reporte(ejecucion))
        self.assertEqual(base.recuperar_interrumpidas(), [ejecucion])
        with base.conexion() as con:
            estado, pendiente, leidos = con.execute('SELECT estado,reporte_pendiente,leidos FROM ejecuciones').fetchone()
        self.assertEqual((estado, pendiente, leidos), ('completado', 1, 1))
        reporte = generar_reporte(base, ejecucion, self.root / 'salida')
        base.confirmar_reporte(ejecucion)
        self.assertTrue(reporte.is_file())
        self.assertEqual(base.recuperar_interrumpidas(), [])

    def test_interrupcion_mensual_regenera_excel_sin_repetir_descargas(self):
        historial = HistorialMensual(self.root / 'mensual.db')
        ejecucion = historial.iniciar(self.root / 'entrada.xlsx', None)
        historial.encolar(ejecucion, [SolicitudMensual('A', 2025, 1, 2)])
        historial.resolver(historial.tareas(ejecucion)[0]['id'], 'descargado', archivo='confirmado.csv')
        with self.assertRaises(KeyboardInterrupt):
            finalizar_con_reporte(lambda error: historial.finalizar(ejecucion, error), Mock(side_effect=KeyboardInterrupt),
                                 Mock(), str, confirmar_reporte=lambda: historial.confirmar_reporte(ejecucion))
        carpeta = self.root / 'salida'
        with patch('integraciones.seguimiento_mensual.generar_reporte_mensual', side_effect=OSError('Disco lleno')):
            with self.assertRaises(OSError):
                historial.recuperar_interrumpidas(carpeta)
        with historial.base.conexion() as con:
            self.assertEqual(con.execute('SELECT reporte_pendiente FROM ejecuciones_mensuales').fetchone()[0], 1)
        historial.recuperar_interrumpidas(carpeta)
        self.assertEqual(historial.resumen(ejecucion)['estado'], 'completado')
        self.assertEqual(historial.resumen(ejecucion)['descargado'], 1)
        self.assertEqual(len(list(carpeta.glob('reportes_mensuales_*.xlsx'))), 1)
        with patch('integraciones.seguimiento_mensual.generar_reporte_mensual') as generar:
            historial.recuperar_interrumpidas(carpeta)
        generar.assert_not_called()

    def test_timeout_descarta_proceso_y_rechaza_mas_comandos(self):
        motor = ProcesoAislado('motor_prueba', timeout=5)
        self.addCleanup(motor.cerrar)
        # Arranca el proceso antes de reducir el plazo, sin contar el spawn.
        motor.llamar('escribir_despues', str(self.root / 'inicio'), 0)
        proceso = motor.proceso
        motor.timeout = .05
        with self.assertRaises(CanalInterrumpido):
            motor.escribir_despues(str(self.root / 'tardio'), 10)
        self.assertIsNone(motor.proceso)
        with self.assertRaises(ValueError):
            proceso.is_alive()  # Handle cerrado despues de confirmar terminacion.
        with self.assertRaises(CanalInterrumpido):
            motor.escribir_despues(str(self.root / 'otro'), 0)
        self.assertFalse((self.root / 'tardio').exists())
        self.assertFalse((self.root / 'otro').exists())

    def test_plazo_compartido_por_varios_comandos(self):
        motor = ProcesoAislado('time', timeout=5)
        self.addCleanup(motor.cerrar)
        motor.monotonic()
        with self.assertRaises(CanalInterrumpido), motor.plazo(.1):
            motor.sleep(.06)
            motor.sleep(.2)

    def test_motor_tagui_real_sin_iniciar_navegador(self):
        motor = ProcesoAislado('rpa', timeout=10, directorio=str(self.root))
        self.addCleanup(motor.cerrar)
        motor.tagui_location(str(self.root))
        self.assertEqual(motor.tagui_location(), str(self.root))
        self.assertFalse(motor.debug(False))
        self.assertFalse(motor.error(False))

    def test_cierre_interrumpido_cuenta_pendientes_y_conserva_exitos(self):
        base = BaseDatos(self.root / 'test.db')
        ejecucion, archivo = base.iniciar(self.root / 'entrada.xlsx', None)
        base.encolar(ejecucion, archivo, [item(1), item(2)], 2)
        base.guardar(base.tareas(ejecucion)[0], 'Detalle 1')
        resumen = base.finalizar(ejecucion, 'Sesion rechazada')
        self.assertEqual((resumen['leidos'], resumen['errores'], resumen['estado']), (1, 1, 'error'))
        self.assertEqual(base.tareas(ejecucion), [])
        with base.conexion() as con:
            error, fecha = con.execute("SELECT error,fecha_fin FROM cola WHERE estado='error'").fetchone()
        self.assertEqual(error, 'Sesion rechazada')
        self.assertTrue(fecha)

    def test_no_finaliza_con_exito_si_quedan_pendientes(self):
        historial = HistorialMensual(self.root / 'test.db')
        ejecucion = historial.iniciar(self.root / 'entrada.xlsx', None)
        historial.encolar(ejecucion, [SolicitudMensual('A', 2025, 1, 2)])
        resumen = historial.finalizar(ejecucion)
        self.assertEqual((resumen['estado'], resumen['errores'], resumen['pendiente']), ('error', 1, 0))

    def test_fallo_excel_persiste_error_en_ambos_historiales(self):
        base = BaseDatos(self.root / 'test.db')
        mensual = HistorialMensual(self.root / 'test.db')
        ejecucion, _ = base.iniciar(self.root / 'entrada.xlsx', None)
        mes = mensual.iniciar(self.root / 'entrada.xlsx', None)
        for finalizar in (lambda error: base.finalizar(ejecucion, error), lambda error: mensual.finalizar(mes, error)):
            avisos = Mock()
            generar = Mock(side_effect=OSError('Disco lleno'))
            with self.assertRaises(OSError):
                finalizar_con_reporte(finalizar, generar, avisos, str, 'Fallo original')
            resumen = avisos.final.call_args.args[0]
            self.assertEqual(resumen['estado'], 'error')
            self.assertIn('Fallo original', resumen['error'])
            self.assertIn('Disco lleno', resumen['error'])
        with base.conexion() as con:
            for tabla in ('ejecuciones', 'ejecuciones_mensuales'):
                estado, error = con.execute('SELECT estado,error FROM ' + tabla).fetchone()
                self.assertEqual(estado, 'error')
                self.assertIn('Disco lleno', error)

    def test_csv_rechaza_respuestas_de_error_y_filas_invalidas(self):
        ruta = self.root / 'reporte.csv'
        for texto in ('{"error":"Internal Server Error"}', 'Internal Server Error', 'A,B\n1,2,3\n', 'A,B\n', '<html>Error</html>'):
            with self.subTest(texto=texto):
                ruta.write_text(texto)
                with self.assertRaises(ValueError):
                    validar_tabla(ruta)

    def test_csv_delimitadores_y_columnas_requeridas(self):
        ruta = self.root / 'reporte.csv'
        for separador in (',', ';', '\t', '|'):
            ruta.write_text(f'Invoice{separador}Amount\nA1{separador}100\n', encoding='utf-8-sig')
            validar_tabla(ruta, ('invoice', 'amount'))
            with self.assertRaises(ValueError):
                validar_tabla(ruta, ('vendor',))

    def test_xls_y_xlsx_corruptos_no_se_aceptan_por_extension(self):
        for extension in ('.xls', '.xlsx'):
            ruta = self.root / ('reporte' + extension)
            ruta.write_bytes(b'contenido que no es un libro')
            with self.assertRaises(Exception):
                validar_tabla(ruta)

    def test_xlsx_valido_y_sin_registros(self):
        ruta = self.root / 'reporte.xlsx'
        libro = Workbook()
        libro.active.append(['Invoice', 'Amount'])
        libro.save(ruta)
        with self.assertRaises(ValueError):
            validar_tabla(ruta)
        libro.active.append(['A1', 100])
        libro.save(ruta)
        libro.close()
        validar_tabla(ruta, ('Invoice',))

    def test_excel_atomico_conserva_archivo_anterior_si_falla(self):
        ruta = self.root / 'reporte.xlsx'
        hojas = [('datos', ['valor'], [['=1+1']])]
        escribir_reporte(ruta, hojas)
        previo = ruta.read_bytes()
        with patch('automatizacion.excel.Workbook.save', side_effect=OSError('Disco lleno')):
            with self.assertRaises(OSError):
                escribir_reporte(ruta, hojas)
        self.assertEqual(ruta.read_bytes(), previo)
        self.assertEqual(list(self.root.glob('.*.xlsx')), [])
        libro = load_workbook(ruta)
        self.assertEqual(libro['datos']['A2'].data_type, 's')
        libro.close()

    def test_reintento_no_repite_un_error_fatal(self):
        accion = Mock(side_effect=CanalInterrumpido('Descartado'))
        pausa = Mock()
        with self.assertRaises(CanalInterrumpido):
            reintentar(accion, 3, reintentable=lambda error: not isinstance(error, CanalInterrumpido), pausa=pausa)
        self.assertEqual(accion.call_count, 1)
        pausa.assert_not_called()
