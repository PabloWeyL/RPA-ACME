from email.message import EmailMessage
from pathlib import Path
from datetime import datetime
import tempfile
import unittest
from unittest.mock import Mock, patch

from config.configuracion import Configuracion, ROOT, cargar_configuracion, configurar_logger
from integraciones.notificaciones import Notificador, enviar_gmail
from main import main


class ConfiguracionEstandarTests(unittest.TestCase):
    def cargar(self, variables):
        with patch('config.configuracion.load_dotenv'), patch.dict('os.environ', variables, clear=True):
            return cargar_configuracion()

    def test_estandar_tiene_prioridad_sobre_aliases(self):
        config = self.cargar({
            'DB_PATH': 'datos/acme.db', 'ACME_DB': 'antigua.db',
            'EXCEL_ENTRADA': 'entrada/work.xlsx', 'ACME_FILTROS': 'antiguo.xlsx',
            'EXCEL_REPORTES_MENSUALES': 'entrada/meses.xlsx',
            'CARPETA_DESCARGAS': 'salida/descargas', 'EXCEL_SALIDA': 'salida/reportes.xlsx',
            'MAX_REINTENTOS': '5', 'ACME_MAX_INTENTOS': '2',
            'MODULO_CORREO': 'true', 'ACME_CORREO_ACTIVO': 'false',
            'EMAIL_REMITENTE': 'nuevo@example.test', 'ACME_CORREO_USUARIO': 'viejo@example.test',
            'EMAIL_PASSWORD': 'aplicacion', 'LISTA_DISTRIBUCION_CORREO': 'uno@example.test;dos@example.test',
            'LISTA_DISTRIBUCION_ERROR': 'errores@example.test', 'CORREO_CADA': '4',
            'ACME_EMAIL': 'acme@example.test', 'ACME_PASSWORD': 'acceso',
            'MODULO_CORREO_INICIAL': 'false', 'SMTP_PORT': '587',
        })
        self.assertEqual(config.db, Path('datos/acme.db'))
        self.assertEqual(config.filtros, Path('entrada/work.xlsx'))
        self.assertEqual(config.reportes_input, Path('entrada/meses.xlsx'))
        self.assertEqual(config.ruta(config.descargas), ROOT / 'salida/descargas')
        self.assertEqual(config.carpeta_reportes, ROOT / 'salida')
        self.assertEqual(config.max_intentos, 5)
        self.assertTrue(config.correo_activo)
        self.assertFalse(config.correo_inicial)
        self.assertEqual(config.correo_cada, 4)
        self.assertEqual(config.correo_destinatarios, ('uno@example.test', 'dos@example.test'))
        self.assertEqual(config.correo_destinatarios_error, ('errores@example.test',))
        self.assertEqual(config.correo_usuario, 'nuevo@example.test')
        self.assertEqual(config.email, 'acme@example.test')
        for secreto in ('aplicacion', 'acceso', 'nuevo@example.test', 'acme@example.test'):
            self.assertNotIn(secreto, repr(config))
            self.assertEqual(config.ocultar(secreto), '[OCULTO]')

    def test_ruta_base_no_cambia_selectores_del_proyecto(self):
        with tempfile.TemporaryDirectory() as temporal:
            config = self.cargar({'RUTA_BASE': temporal, 'RUTA_HISTORICO': 'historico', 'CARPETA_CAPTURAS': 'evidencia'})
            self.assertEqual(config.ruta(config.db), Path(temporal) / 'temporales/acme.db')
            self.assertEqual(config.ruta(config.capturas), Path(temporal) / 'evidencia')
            self.assertIn('login', config.selectores())
            logger = configurar_logger(config, 'prueba')
            try:
                logger.info('Configuracion probada')
            finally:
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
            self.assertTrue((Path(temporal) / 'historico/rpa_acme.log').is_file())

    def test_modulo_scraping_desactivado_no_inicia_automatizacion(self):
        config = self.cargar({'MODULO_SCRAPING': 'false'})
        with patch('main.cargar_configuracion', return_value=config), patch('main.BaseDatos') as base, patch('builtins.print'):
            self.assertEqual(main(['consultar']), 0)
            self.assertEqual(main(['descargar-reportes']), 0)
            base.assert_not_called()

    def test_log_unico_con_contexto_y_secretos_ocultos(self):
        with tempfile.TemporaryDirectory() as temporal:
            config = Configuracion(root=Path(temporal), password='clave-privada')
            logger = configurar_logger(config, 'ejecucion-1')
            logger.info('Consulta %s', config.password)
            logger = configurar_logger(config, 'ejecucion-2', modulo='REPORTES_MENSUALES')
            try:
                logger.info('Descarga terminada')
            finally:
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                    handler.close()
            archivos = list((Path(temporal) / 'logs').glob('*.log'))
            self.assertEqual([p.name for p in archivos], ['rpa_acme.log'])
            texto = archivos[0].read_text(encoding='utf-8')
            self.assertIn('[ID:ejecucion-1] [WORK_ITEMS]', texto)
            self.assertIn('[ID:ejecucion-2] [REPORTES_MENSUALES]', texto)
            self.assertIn('[RPA_ACME]', texto)
            self.assertNotIn('clave-privada', texto)
            self.assertIn('[OCULTO]', texto)
            self.assertEqual(texto.count('Descarga terminada'), 1)

    def test_log_se_reemplaza_al_cumplir_12_meses_incluso_desde_29_febrero(self):
        for ahora, reemplazar in ((datetime(2025, 2, 28, 9, 59, 59), False),
                                 (datetime(2025, 2, 28, 10), True)):
            with self.subTest(ahora=ahora), tempfile.TemporaryDirectory() as temporal:
                config = Configuracion(root=Path(temporal))
                carpeta = Path(temporal) / 'logs'
                carpeta.mkdir()
                ruta = carpeta / 'rpa_acme.log'
                ruta.write_text('2024-02-29 10:00:00 [INFO] REGISTRO_ANTERIOR\n', encoding='utf-8')
                antiguo = carpeta / '20261029_ejecucion.log'
                antiguo.write_text('historial previo', encoding='utf-8')
                with patch('config.configuracion.datetime', wraps=datetime) as reloj:
                    reloj.now.return_value = ahora
                    logger = configurar_logger(config, 'nueva')
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                    handler.close()
                self.assertEqual('REGISTRO_ANTERIOR' not in ruta.read_text(encoding='utf-8'), reemplazar)
                self.assertEqual(antiguo.read_text(encoding='utf-8'), 'historial previo')

    def test_retencion_logs_debe_ser_positiva(self):
        self.assertEqual(self.cargar({}).retencion_logs_meses, 12)
        self.assertEqual(self.cargar({'RETENCION_LOGS_MESES': '24'}).retencion_logs_meses, 24)
        with self.assertRaisesRegex(ValueError, 'RETENCION_LOGS_MESES'):
            self.cargar({'RETENCION_LOGS_MESES': '0'})

    def test_correo_inicial_desactivado_conserva_final_y_lista_de_errores(self):
        config = Configuracion(correo_activo=True, correo_inicial=False,
                               correo_usuario='robot@example.test', correo_password='aplicacion',
                               correo_destinatarios=('uno@example.test',),
                               correo_destinatarios_error=('uno@example.test', 'errores@example.test'))
        enviar = Mock()
        n = Notificador(config, Mock(), 'Work Items', 'ejecucion', Path('entrada.xlsx'), enviar=enviar)
        n.inicio()
        enviar.assert_not_called()
        n.final({'estado': 'error'})
        mensaje = enviar.call_args.args[1]
        self.assertEqual(mensaje['To'], 'uno@example.test, errores@example.test')
        self.assertIn(config.nombre_proceso, mensaje.get_content())

    def test_smtp_587_activa_starttls_antes_de_login(self):
        config = Configuracion(smtp_port=587, correo_usuario='robot@example.test',
                               correo_password='aplicacion', correo_destinatarios=('uno@example.test',))
        mensaje = EmailMessage()
        mensaje['To'] = 'uno@example.test, errores@example.test'
        with patch('integraciones.notificaciones.smtplib.SMTP') as smtp, patch('integraciones.notificaciones.smtplib.SMTP_SSL') as ssl:
            servidor = smtp.return_value.__enter__.return_value
            servidor.send_message.return_value = {}
            enviar_gmail(config, mensaje)
            ssl.assert_not_called()
            smtp.assert_called_once_with('smtp.gmail.com', 587, timeout=10)
            self.assertEqual([c[0] for c in servidor.method_calls], ['ehlo', 'starttls', 'ehlo', 'login', 'send_message'])
            self.assertTrue(servidor.starttls.call_args.kwargs['context'].check_hostname)
            self.assertEqual(servidor.send_message.call_args.kwargs['to_addrs'], ['uno@example.test', 'errores@example.test'])


if __name__ == '__main__':
    unittest.main()
