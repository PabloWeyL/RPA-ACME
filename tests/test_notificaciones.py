from email.message import EmailMessage
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook

from config.base_datos import BaseDatos
from config.configuracion import Configuracion, ROOT, cargar_configuracion
from integraciones.notificaciones import Notificador, enviar_gmail
from integraciones.proceso import consultar
from integraciones.proceso_mensual import HistorialMensual, descargar_reportes
from integraciones.reportes_mensuales import ReportesMensuales, SinReporte
from test_acme import NavegadorFalso, item
from test_reportes_mensuales import NavegadorMensualFalso


class CorreoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='acme-correo-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Configuracion(root=self.root, correo_activo=True,
                                    correo_usuario='robot@example.test',
                                    correo_password='abcd efgh ijkl mnop',
                                    correo_destinatarios=('uno@example.test', 'dos@example.test'))
        self.logger = Mock(spec=logging.Logger)
        self.mensajes = []
        self.transporte = Mock(side_effect=lambda config, mensaje: self.mensajes.append(mensaje))
        self.notificador = Notificador(self.config, self.logger, 'Work Items', 'ejecucion-prueba',
                                      self.root / 'filtros.xlsx', enviar=self.transporte)
        self.reporte = self.root / 'seguimiento.xlsx'
        libro = Workbook()
        libro.active.append(['campo', 'valor'])
        libro.save(self.reporte)
        libro.close()

    def resumen(self, **cambios):
        return dict({'estado': 'completado', 'seleccionados': 0, 'leidos': 0, 'errores': 0}, **cambios)

    def test_solo_inicio_y_final_sin_avisos_intermedios_ni_final_duplicado(self):
        self.notificador.inicio()
        self.notificador.seleccionar(20)
        for _ in range(20):
            self.notificador.resolver('leido')
        self.notificador.final(self.resumen(seleccionados=20, leidos=20), self.reporte)
        self.notificador.final(self.resumen(), self.reporte)
        self.assertEqual(len(self.mensajes), 2)
        self.assertIn('[Inicio]', self.mensajes[0]['Subject'])
        self.assertIn('[Final completado]', self.mensajes[1]['Subject'])
        self.assertIn('20/20', self.mensajes[1].get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(self.mensajes[0]['To'], 'uno@example.test, dos@example.test')
        self.assertIn('filtros.xlsx', self.mensajes[0].get_content())
        self.assertEqual(len(list(self.mensajes[0].iter_attachments())), 0)
        adjuntos = list(self.mensajes[1].iter_attachments())
        self.assertEqual(len(adjuntos), 1)
        self.assertEqual(adjuntos[0].get_filename(), self.reporte.name)
        self.assertEqual(adjuntos[0].get_payload(decode=True), self.reporte.read_bytes())

    def test_inicio_y_resultados_a_ambas_listas_sin_duplicados(self):
        self.config.correo_destinatarios_error = ('operacion@example.test', 'UNO@example.test')
        self.notificador.inicio()
        self.notificador.final(self.resumen(), self.reporte)
        self.assertEqual(len(self.mensajes), 2)
        for mensaje in self.mensajes:
            self.assertEqual(mensaje['To'], 'uno@example.test, dos@example.test, operacion@example.test')

    def test_resultados_con_errores_a_ambas_listas(self):
        self.config.correo_destinatarios_error = ('operacion@example.test',)
        self.notificador.final(self.resumen(estado='con_errores', errores=1), self.reporte)
        self.assertEqual(self.mensajes[0]['To'], 'uno@example.test, dos@example.test, operacion@example.test')

    def test_falla_general_solo_a_lista_error(self):
        self.config.correo_destinatarios_error = ('operacion@example.test',)
        self.notificador.final(self.resumen(estado='error'), self.reporte)
        self.assertEqual(self.mensajes[0]['To'], 'operacion@example.test')

    def test_error_sin_lista_error_no_reenvia_a_resultados(self):
        self.notificador.final(self.resumen(estado='error'), self.reporte)
        self.transporte.assert_not_called()
        self.assertIn('lista de destinatarios vacia', str(self.logger.warning.call_args))

    def test_solo_lista_error_recibe_todos_los_avisos(self):
        self.config.correo_destinatarios = ()
        self.config.correo_destinatarios_error = ('operacion@example.test',)
        notificador = Notificador(self.config, self.logger, 'Work Items', 'solo-error', self.reporte, enviar=self.transporte)
        self.assertTrue(notificador.activo)
        notificador.inicio()
        notificador.final(self.resumen(estado='error'), self.reporte)
        self.assertEqual(len(self.mensajes), 2)
        self.assertTrue(all(m['To'] == 'operacion@example.test' for m in self.mensajes))

    def test_cero_tareas_envia_inicio_y_final_con_duracion(self):
        self.notificador.inicio()
        self.notificador.seleccionar(0)
        self.notificador.final(self.resumen(), self.reporte)
        self.assertEqual(len(self.mensajes), 2)
        cuerpo = self.mensajes[1].get_body(preferencelist=('plain',)).get_content()
        self.assertIn('0/0', cuerpo)
        self.assertIn('Duracion:', cuerpo)

    def test_adjunto_ausente_grande_o_ilegible_envia_resumen(self):
        for caso in ('ausente', 'grande', 'ilegible'):
            with self.subTest(caso=caso):
                n = Notificador(self.config, self.logger, 'Reportes mensuales', caso,
                                self.root / 'entrada.xlsx', enviar=self.transporte)
                if caso == 'grande':
                    with patch('integraciones.notificaciones.MAX_ADJUNTO', 8):
                        n.final(self.resumen(), self.reporte)
                elif caso == 'ilegible':
                    with patch.object(Path, 'open', side_effect=PermissionError('Acceso denegado')):
                        n.final(self.resumen(), self.reporte)
                else:
                    n.final(self.resumen(), self.root / 'ausente.xlsx')
                self.assertEqual(list(self.mensajes[-1].iter_attachments()), [])
                self.assertIn('Excel adjunto omitido', self.mensajes[-1].get_content())
        self.assertEqual(self.transporte.call_count, 3)
        self.assertEqual(self.logger.warning.call_count, 3)

    def test_fallo_smtp_no_reintenta_y_oculta_secretos(self):
        self.transporte.side_effect = RuntimeError('abcd efgh ijkl mnop / abcdefghijklmnop / robot@example.test')
        self.notificador.inicio()
        self.assertEqual(self.transporte.call_count, 1)
        advertencias = str(self.logger.warning.call_args_list)
        for secreto in ('abcd efgh ijkl mnop', 'abcdefghijklmnop', 'robot@example.test'):
            self.assertNotIn(secreto, advertencias)
            self.assertNotIn(secreto, repr(self.config))
        self.assertIn('[OCULTO]', advertencias)

    def test_desactivado_no_envia_ni_lee_adjuntos(self):
        self.config.correo_activo = False
        n = Notificador(self.config, self.logger, 'Work Items', 'off', self.reporte, enviar=self.transporte)
        with patch.object(Path, 'open', side_effect=AssertionError('No debe leer adjuntos')):
            n.inicio()
            n.seleccionar(20)
            for _ in range(20):
                n.resolver('leido')
            n.final(self.resumen(), self.reporte)
        self.transporte.assert_not_called()

    def test_configuracion_incompleta_o_invalida_desactiva_solo_correo(self):
        for cambios in ({'correo_password': ''}, {'correo_destinatarios': ()},
                        {'correo_destinatarios': ('sin-arroba',)},
                        {'correo_destinatarios': ('uno@example.test\nBcc:otro@example.test',)}):
            with self.subTest(cambios=cambios):
                valores = dict(correo_activo=True, correo_usuario='robot@example.test',
                               correo_password='contrasena', correo_destinatarios=('uno@example.test',))
                valores.update(cambios)
                n = Notificador(Configuracion(**valores), self.logger, 'Work Items', 'invalid', self.reporte, enviar=self.transporte)
                n.inicio()
                self.assertFalse(n.activo)
        self.transporte.assert_not_called()

    def test_carga_env_y_frecuencia_invalida_no_detiene_configuracion(self):
        variables = {'ACME_CORREO_ACTIVO': 'true', 'ACME_CORREO_USUARIO': 'robot@example.test',
                     'ACME_CORREO_PASSWORD': 'clave',
                     'ACME_CORREO_DESTINATARIOS': ' uno@example.test, dos@example.test ', 'ACME_CORREO_CADA': '5'}
        with patch('config.configuracion.load_dotenv'), patch.dict('os.environ', variables, clear=True):
            config = cargar_configuracion()
            self.assertTrue(config.correo_activo)
            self.assertEqual(config.correo_cada, 5)
            self.assertEqual(config.correo_destinatarios, ('uno@example.test', 'dos@example.test'))
        variables['ACME_CORREO_CADA'] = 'texto'
        with patch('config.configuracion.load_dotenv'), patch.dict('os.environ', variables, clear=True):
            config = cargar_configuracion()
            n = Notificador(config, self.logger, 'Work Items', 'invalid', self.reporte, enviar=self.transporte)
            self.assertTrue(n.activo)

    def test_gmail_usa_tls_timeout_y_destinatarios_explicitos(self):
        mensaje = EmailMessage()
        with patch('integraciones.notificaciones.smtplib.SMTP_SSL') as smtp:
            servidor = smtp.return_value.__enter__.return_value
            servidor.send_message.return_value = {}
            enviar_gmail(self.config, mensaje)
            self.assertEqual(smtp.call_args.args, ('smtp.gmail.com', 465))
            self.assertEqual(smtp.call_args.kwargs['timeout'], 10)
            self.assertTrue(smtp.call_args.kwargs['context'].check_hostname)
            servidor.login.assert_called_once_with('robot@example.test', 'abcdefghijklmnop')
            servidor.send_message.assert_called_once_with(mensaje, from_addr='robot@example.test', to_addrs=['uno@example.test', 'dos@example.test'])

    def test_rechazo_parcial_smtp_se_registra_sin_reenvio(self):
        with patch('integraciones.notificaciones.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {'dos@example.test': (550, b'rejected')}
            n = Notificador(self.config, self.logger, 'Work Items', 'parcial', self.reporte)
            n.inicio()
            self.assertEqual(smtp.return_value.__enter__.return_value.send_message.call_count, 1)
            self.assertIn('rechazo algunos destinatarios', str(self.logger.warning.call_args))


class NotificacionesFlujoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='acme-correo-flujo-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Configuracion(root=self.root, correo_activo=True, correo_usuario='robot@example.test',
                                    correo_password='clave', correo_destinatarios=('uno@example.test',), correo_cada=2,
                                    correo_destinatarios_error=('operacion@example.test',))
        (self.root / 'input').mkdir()
        (self.root / 'config').mkdir()
        (self.root / 'config/selectores.json').write_text((ROOT / 'config/selectores.json').read_text())
        for nombre, filas in (
            ('filtros.xlsx', [['tipo', 'estado']]),
            ('reportes_mensuales.xlsx', [['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'], ['A', 2025, 1, 5]]),
        ):
            libro = Workbook()
            for fila in filas:
                libro.active.append(fila)
            libro.save(self.root / 'input' / nombre)
            libro.close()
        self.mensajes = []
        self.observar = None

    def fabrica(self, config, logger, proceso, ejecucion, archivo):
        def enviar(config, mensaje):
            if self.observar:
                self.observar(ejecucion, mensaje)
            self.mensajes.append(mensaje)
        return Notificador(config, logger, proceso, ejecucion, archivo, enviar=enviar)

    def consultar(self, **kwargs):
        navegador = NavegadorFalso(self.config, **kwargs)
        return consultar(self.config, fabrica_navegador=lambda c: navegador, pausa=lambda _: None, fabrica_notificador=self.fabrica)

    def mensuales(self):
        return descargar_reportes(self.config, fabrica_navegador=NavegadorMensualFalso, pausa=lambda _: None, fabrica_notificador=self.fabrica)

    def test_work_items_solo_inicio_y_final_despues_del_commit(self):
        commits = []
        def observar(ejecucion, mensaje):
            if '[Final' in mensaje['Subject']:
                with BaseDatos(self.config.ruta(self.config.db)).conexion() as con:
                    commits.append(con.execute('SELECT COUNT(*) FROM resultados').fetchone()[0])
        self.observar = observar
        resumen, _ = self.consultar(items=[item(i) for i in range(1, 6)], errores={'1': 1})
        self.assertEqual(resumen['leidos'], 5)
        self.assertEqual(commits, [5])
        self.assertEqual(len(self.mensajes), 2)
        self.assertIn('[Final completado]', self.mensajes[-1]['Subject'])
        self.assertTrue(all(m['To'] == 'uno@example.test, operacion@example.test' for m in self.mensajes))

    def test_work_items_error_definitivo_cuenta_y_final_con_errores(self):
        resumen, _ = self.consultar(items=[item(i) for i in range(1, 4)], errores={'1': 99})
        self.assertEqual(resumen['estado'], 'con_errores')
        self.assertEqual(len(self.mensajes), 2)
        self.assertIn('Errores definitivos resueltos: 1', self.mensajes[1].get_body(preferencelist=('plain',)).get_content())
        self.assertIn('[Final con_errores]', self.mensajes[-1]['Subject'])

    def test_mensuales_cuenta_sin_reporte_y_errores_despues_del_commit(self):
        observados = []
        def observar(ejecucion, mensaje):
            if '[Final' in mensaje['Subject']:
                tareas = HistorialMensual(self.config.ruta(self.config.db)).tareas(ejecucion)
                observados.append(sum(t['estado'] != 'pendiente' for t in tareas))
        self.observar = observar
        real = ReportesMensuales.descargar
        def descargar(cliente, solicitud, carpeta):
            if solicitud.mes == 1:
                raise SinReporte('No report found')
            if solicitud.mes == 2:
                raise RuntimeError('Fallo temporal')
            return real(cliente, solicitud, carpeta)
        with patch.object(ReportesMensuales, 'descargar', descargar):
            resumen, _ = self.mensuales()
        self.assertEqual((resumen['descargado'], resumen['sin_reporte'], resumen['errores']), (3, 1, 1))
        self.assertEqual(observados, [5])
        self.assertEqual(len(self.mensajes), 2)
        self.assertIn('Meses sin reporte: 1', self.mensajes[1].get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(len(list(self.mensajes[-1].iter_attachments())), 1)

    def test_autenticacion_rechazada_envia_final_error(self):
        resumen, _ = self.consultar(login_error=True)
        self.assertEqual(resumen['estado'], 'error')
        self.assertEqual(len(self.mensajes), 2)
        cuerpo = self.mensajes[-1].get_body(preferencelist=('plain',)).get_content()
        self.assertIn('Credenciales rechazadas', cuerpo)
        self.assertEqual(self.mensajes[0]['To'], 'uno@example.test, operacion@example.test')
        self.assertEqual(self.mensajes[-1]['To'], 'operacion@example.test')

    def test_entrada_mensual_invalida_envia_final_error(self):
        (self.root / 'input/reportes_mensuales.xlsx').unlink()
        resumen, _ = self.mensuales()
        self.assertEqual(resumen['estado'], 'error')
        self.assertEqual(len(self.mensajes), 2)
        self.assertIn('[Final error]', self.mensajes[-1]['Subject'])

    def test_fallo_de_correo_no_cambia_resultado_de_ambos_procesos(self):
        def fallar(ejecucion, mensaje):
            raise ConnectionError('Gmail no disponible')
        self.observar = fallar
        resumen_items, _ = self.consultar(items=[item(1)])
        resumen_mes, _ = self.mensuales()
        self.assertEqual(resumen_items['estado'], 'completado')
        self.assertEqual(resumen_mes['estado'], 'completado')
        registros = '\n'.join(ruta.read_text(encoding='utf-8') for ruta in (self.root / 'logs').glob('*.log'))
        self.assertIn('Gmail no disponible', registros)

    def test_fallo_generacion_excel_envia_final_sin_adjunto_en_ambos_procesos(self):
        for modulo, funcion, ejecutar in (
            ('integraciones.proceso', 'generar_reporte', lambda: self.consultar(items=[item(1)])),
            ('integraciones.proceso_mensual', 'generar_reporte_mensual', self.mensuales),
        ):
            with self.subTest(modulo=modulo), patch(modulo + '.' + funcion, side_effect=OSError('Disco no disponible')):
                with self.assertRaises(OSError):
                    ejecutar()
                self.assertIn('[Final error]', self.mensajes[-1]['Subject'])
                self.assertEqual(self.mensajes[-1]['To'], 'operacion@example.test')
                self.assertIn('No se pudo generar el Excel', self.mensajes[-1].get_content())
                self.assertEqual(list(self.mensajes[-1].iter_attachments()), [])
                with BaseDatos(self.config.ruta(self.config.db)).conexion() as con:
                    tabla = 'ejecuciones_mensuales' if modulo.endswith('proceso_mensual') else 'ejecuciones'
                    estado, error = con.execute('SELECT estado,error FROM ' + tabla + ' ORDER BY rowid DESC LIMIT 1').fetchone()
                self.assertEqual(estado, 'error')
                self.assertIn('Disco no disponible', error)


if __name__ == '__main__':
    unittest.main()
