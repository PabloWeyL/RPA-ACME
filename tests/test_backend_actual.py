"""Contrato actual y adaptador del bot, sin backend, Azure ni SMTP reales."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from servidor_cola_actual import ServidorColaActual
from integraciones import backend_actual as adaptador
from integraciones.weyland.cola_actual import crear_cliente_cola
from integraciones.weyland import ErrorWeyland, ErrorRedWeyland, ResultadoBot
from integraciones.trazabilidad import actual

PROYECTO = Path(__file__).resolve().parents[1].name
SIN_INPUT = PROYECTO == 'RPA-Godzilla'


class BackendActualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.servidor = ServidorColaActual(entrada=None if SIN_INPUT else b'input ficticio')
        self.addCleanup(self.servidor.cerrar)
        self.valores = {'BACKEND_AUTH_MODE': 'weyland', 'BACKEND_CONTRATO': 'cola_actual',
            'BACKEND_MAQUINA_ID': '1', 'BACKEND_PROCESO_CODIGO': 'DEMO',
            'BACKEND_API_URL': self.servidor.url, 'BACKEND_PERMITIR_HTTP_LOCAL': 'true',
            'BACKEND_EMAIL': 'bot@example.test', 'BACKEND_PASSWORD': 'password_ficticia',
            'BACKEND_BOT_API_KEY': 'clave_ficticia', 'BACKEND_TRABAJOS_DIR': str(self.raiz / 'trabajos')}
        self.negocio = Mock(side_effect=self.procesar)
        self.eventos = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        if SIN_INPUT:
            self.stack.enter_context(patch.object(adaptador, 'avisar', side_effect=self.avisar))
        else:
            self.stack.enter_context(patch.object(adaptador, 'ajustes_correo', return_value={
                'habilitado': True, 'inicio': True, 'nombre': 'PRUEBA',
                'host': 'smtp.example.test', 'puerto': 587, 'usuario': 'bot@example.test',
                'password': 'smtp_ficticia', 'remitente': 'bot@example.test',
                'resultados': ('cliente@example.test',), 'errores': ('operaciones@example.test',),
                'limite': '20971520'}))
            self.stack.enter_context(patch('integraciones.weyland_notificaciones.enviar', side_effect=self.smtp))

    def smtp(self, config, remitente, destinatarios, mime):
        from email import policy
        from email.parser import BytesParser
        mensaje = BytesParser(policy=policy.default).parsebytes(mime)
        evento = mensaje['Subject'].split(' - ')[1]
        adjuntos = [p.get_payload(decode=True) for p in mensaje.iter_attachments()]
        self.eventos.append((evento, self.servidor.estado, tuple(destinatarios), adjuntos))
        return {}

    def avisar(self, valores, evento, identificador, adjuntos=None):
        self.assertEqual(identificador, 123)
        self.eventos.append((evento, self.servidor.estado, (), [Path(p).read_bytes() for p in adjuntos or ()]))
        return True

    def procesar(self, carpeta, entrada, parametros):
        self.assertEqual(actual()['solicitud_id'], 123)
        self.assertEqual(actual()['ejecucion_id_web'], 123)
        if SIN_INPUT:
            self.assertIsNone(entrada)
        else:
            self.assertEqual(entrada.read_bytes(), b'input ficticio')
        reporte = carpeta / 'resultado.txt'
        reporte.write_text('resultado del adaptador', encoding='utf-8')
        return ResultadoBot((reporte,))

    def ejecutar(self):
        return adaptador.ejecutar_actual(self.raiz, 'procesar' if SIN_INPUT or PROYECTO != 'RPA-ACME' else 'consultar',
            self.valores, fabrica=lambda: crear_cliente_cola(self.valores), runner=self.negocio)

    def test_login_input_negocio_reporte_y_correo_confirmado(self):
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.negocio.assert_called_once()
        self.assertEqual([e[0] for e in self.eventos], ['inicio', 'resultados'])
        self.assertEqual(self.eventos[0][1], 'EN_PROCESO')
        self.assertEqual(self.eventos[1][1], 'COMPLETADA')
        self.assertEqual(self.eventos[1][3], [self.servidor.reporte])
        if not SIN_INPUT:
            self.assertEqual(set(self.eventos[0][2]), {'cliente@example.test', 'operaciones@example.test'})
        with ZipFile(io.BytesIO(self.servidor.reporte)) as archivo:
            datos = json.loads(archivo.read('reportes/ejecucion.json'))
            self.assertEqual(datos['solicitud_id'], datos['ejecucion_id'])
            self.assertEqual(datos['ejecucion_id'], 123)

    def test_corte_del_ack_concilia_sin_repetir_negocio_o_reporte(self):
        self.servidor.cortar_entrega = True
        with self.assertRaises(ErrorRedWeyland): self.ejecutar()
        self.assertNotIn('resultados', [e[0] for e in self.eventos])
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.negocio.assert_called_once()
        self.assertEqual(self.servidor.entregas, 1)
        self.assertEqual(self.eventos[-1][0], 'resultados')

    def test_204_no_procesa_ni_envia_resultado(self):
        self.servidor.estado = 'COMPLETADA'
        self.assertEqual(self.ejecutar()['fase'], 'SIN_TRABAJO')
        self.negocio.assert_not_called()
        self.assertFalse(self.eventos)

    def test_modalidad_incompatible_informa_error_sin_negocio(self):
        self.servidor.entrada = b'input no admitido' if SIN_INPUT else None
        self.assertEqual(self.ejecutar()['fase'], 'ERROR')
        self.negocio.assert_not_called()
        self.assertEqual(self.servidor.fallos, 1)

    def test_error_de_negocio_se_notifica_solo_tras_ack(self):
        self.negocio.side_effect = RuntimeError('SECRETO_NO_PUBLICAR')
        self.assertEqual(self.ejecutar()['fase'], 'ERROR')
        self.assertEqual(self.eventos[-1][0:2], ('fallas', 'ERROR'))
        if not SIN_INPUT:
            self.assertEqual(self.eventos[-1][2], ('operaciones@example.test',))
        error = next(r for r in self.servidor.registro if r[1].endswith('/error'))
        self.assertNotIn(b'SECRETO_NO_PUBLICAR', error[3])

    def test_otro_codigo_queda_bloqueado_sin_negocio(self):
        self.servidor.codigo = 'OTRO'
        for _ in range(2):
            with self.assertRaises(ErrorWeyland): self.ejecutar()
        self.negocio.assert_not_called()
        self.assertEqual(self.servidor.inicios, 1)

    def test_vm_ausente_no_inicia_conexion(self):
        self.valores['BACKEND_MAQUINA_ID'] = ''
        with self.assertRaises(ValueError): self.ejecutar()
        self.assertFalse(self.servidor.registro)

    def test_fabrica_del_proyecto_usa_cliente_actual_y_login(self):
        import os
        from config.api import crear_cliente_api
        from integraciones.weyland.cola_actual import ClienteColaWeyland
        valores = dict(self.valores)
        argumentos = {}
        if PROYECTO == 'RPA-Control-Vencimientos':
            for clave in ('BACKEND_EMAIL', 'BACKEND_PASSWORD', 'BACKEND_BOT_API_KEY'):
                valores.pop(clave)
            valores.update(ENTORNO='DESARROLLO', TIMEOUT_SEGUNDOS='30', KEYVAULT_NAME='vault-ficticio',
                SECRET_BACKEND_EMAIL='ref-correo', SECRET_BACKEND_PASSWORD='ref-password',
                SECRET_BACKEND_API_KEY='ref-api-key', BACKEND_LOGIN_URL=self.servidor.url + '/auth/login')
            argumentos['proveedor_secretos'] = Mock(return_value={
                'SECRET_BACKEND_EMAIL': 'bot@example.test', 'SECRET_BACKEND_PASSWORD': 'password_ficticia',
                'SECRET_BACKEND_API_KEY': 'clave_ficticia'})
        archivo = self.raiz / 'acceso.env'
        archivo.write_text(''.join(f'{k}={v}\n' for k, v in valores.items()), encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True), crear_cliente_api(archivo, **argumentos) as cliente:
            self.assertIsInstance(cliente, ClienteColaWeyland)
            self.assertFalse(self.servidor.registro)
            cliente.verificar()
        self.assertEqual(self.servidor.inicios, 0)

    def test_cli_actual_rechaza_id_manual_sin_conexion(self):
        import main
        if SIN_INPUT:
            with patch.object(main, 'preparar_carpetas'), patch.object(main, 'leer_valores', return_value=self.valores), \
                    patch('integraciones.worker.ejecutar_desde_config') as ejecutar:
                self.assertEqual(main.main(['web-procesar', '--id-solicitud', '123']), 1)
        else:
            with patch('config.weyland.leer_valores', return_value=self.valores), \
                    patch('integraciones.weyland_worker.ejecutar_desde_config') as ejecutar, \
                    self.assertRaises(SystemExit):
                main.main(['web-procesar', '--id-solicitud', '123'])
        ejecutar.assert_not_called()

    def test_cli_actual_no_exige_id_manual(self):
        import main
        if SIN_INPUT:
            with patch.object(main, 'preparar_carpetas'), patch.object(main, 'leer_valores', return_value=self.valores), \
                    patch.object(main, 'reintentar_pendientes'), patch('integraciones.worker.ejecutar_desde_config',
                    return_value={'fase': 'COMPLETADA', 'ejecucion_id': 123}) as ejecutar:
                self.assertEqual(main.main(['web-procesar']), 0)
            self.assertIsNone(ejecutar.call_args.args[0])
        else:
            config = Mock(root=self.raiz, modulo_scraping=True, modulo_procesamiento=True)
            with patch('config.weyland.leer_valores', return_value=self.valores), \
                    patch.object(main, 'cargar_configuracion', return_value=config), \
                    patch('integraciones.weyland_worker.ejecutar_desde_config', return_value=0) as ejecutar:
                self.assertEqual(main.main(['web-procesar']), 0)
            self.assertIsNone(ejecutar.call_args.kwargs['solicitud_id'])

    def test_sin_parametros_valida_prueba_y_procesa_api_local(self):
        import os
        import main
        from integraciones import arranque_web as perfil
        original = adaptador.ejecutar_actual
        def ejecutar(raiz, comando, valores, **kwargs):
            return original(raiz, comando, valores, fabrica=lambda: crear_cliente_cola(self.valores), runner=self.negocio)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'BOT_PRUEBAS_EN_CURSO': '0'}))
            stack.enter_context(patch.object(perfil, 'RAIZ', self.raiz))
            stack.enter_context(patch.object(perfil, 'leer_valores', return_value=self.valores))
            stack.enter_context(patch.object(perfil, 'validar_tecnico', return_value={'logs': self.raiz / 'logs'}))
            pruebas = stack.enter_context(patch('integraciones.arranque.ejecutar_pruebas',
                return_value={'ejecutadas': 5, 'fallas': 0, 'errores': 0, 'omitidas': 0, 'aprobadas': True}))
            if not SIN_INPUT:
                stack.enter_context(patch('config.weyland.leer_valores', return_value=self.valores))
            stack.enter_context(patch.object(adaptador, 'ejecutar_actual', side_effect=ejecutar))
            self.assertEqual(main.main([]), 0)
        pruebas.assert_called_once()
        self.negocio.assert_called_once()
        self.assertEqual(self.servidor.entregas, 1)
        self.assertEqual(self.servidor.estado, 'COMPLETADA')

    def test_negocio_real_del_bot_con_export_aislado(self):
        if SIN_INPUT:
            from test_navegador import PruebasNavegador
            from test_bot import bytes_imagen
            from recursos.imagenes import extraer_godzilla
            navegador = PruebasNavegador('test_extrae_una_imagen_con_chromium_real')
            navegador.setUp()
            self.addCleanup(navegador.tearDown)
            self.negocio = Mock(wraps=adaptador.crear_runner(lambda carpeta: extraer_godzilla(carpeta, headless=True)))
            self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
            with ZipFile(io.BytesIO(self.servidor.reporte)) as archivo:
                self.assertEqual(archivo.read('godzilla.png'), bytes_imagen())
            return
        from openpyxl import Workbook
        libro = Workbook()
        if PROYECTO == 'RPA-ACME':
            from config.configuracion import Configuracion
            from integraciones.proceso import consultar
            from test_acme import NavegadorFalso
            libro.active.append(['tipo', 'estado'])
            libro.active.append(['WI5', 'Open'])
            original = Configuracion(root=self.raiz)
            self.stack.enter_context(patch.object(adaptador, 'cargar_configuracion', return_value=original))
            real = lambda config, entrada: consultar(config, entrada=entrada,
                fabrica_navegador=lambda c: NavegadorFalso(c), pausa=lambda _: None)
            self.stack.enter_context(patch.object(adaptador, 'consultar', side_effect=real))
            negocio = adaptador.crear_runner('consultar', self.raiz)
        else:
            import os
            from configuracion import cargar_configuracion
            from integraciones.monitor import Parametros
            raiz_proyecto = Path(__file__).resolve().parents[1]
            env = self.raiz / '.env'
            env.write_text((raiz_proyecto / '.env.example').read_text(encoding='utf-8'), encoding='utf-8')
            with patch.dict(os.environ, {}, clear=True):
                original = cargar_configuracion(True, env)
            # Los servicios se simulan explicitamente; no se usa el fallback CLI.
            original = replace(original, prueba_local=False)
            self.stack.enter_context(patch.object(adaptador, 'cargar_configuracion', return_value=original))
            vault = self.stack.enter_context(patch.object(adaptador, 'obtener_secretos', return_value={}))
            monitor = self.stack.enter_context(patch.object(adaptador, 'obtener_parametros',
                return_value=Parametros(date(2026, 11, 10), 30)))
            libro.active.append(['codigo', 'producto', 'lote', 'cantidad', 'fecha_vencimiento'])
            libro.active.append(['A', 'Leche', 'L1', 2, '2026-11-01'])
            negocio = adaptador.procesar_trabajo
        buffer = io.BytesIO()
        libro.save(buffer)
        libro.close()
        self.servidor.entrada = buffer.getvalue()
        self.negocio = Mock(wraps=negocio)
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.negocio.assert_called_once()
        with ZipFile(io.BytesIO(self.servidor.reporte)) as archivo:
            nombres = archivo.namelist()
            self.assertTrue(any(n.endswith('.xlsx') for n in nombres))
            self.assertFalse(any(n.endswith(('.db', '.sqlite')) or '/input/' in n for n in nombres))
        carpeta = self.negocio.call_args.args[0]
        self.assertTrue(list((carpeta / 'datos').rglob('*.db')))
        if PROYECTO != 'RPA-ACME':
            vault.assert_called_once()
            monitor.assert_called_once()

    def test_entrega_de_reportes_y_consulta_posterior(self):
        if PROYECTO != 'RPA-ACME':
            # En los otros bots verificar que un siguiente arranque no reenvie.
            self.ejecutar()
            entregas = self.servidor.entregas
            self.assertEqual(self.ejecutar()['fase'], 'SIN_TRABAJO')
            self.negocio.assert_called_once()
            self.assertEqual(self.servidor.entregas, entregas)
            if not SIN_INPUT:
                self.assertEqual(len(self.eventos), 2)
            return
        from openpyxl import Workbook
        from config.configuracion import Configuracion, ROOT
        from integraciones.proceso_mensual import descargar_reportes
        from test_reportes_mensuales import NavegadorMensualFalso
        libro = Workbook()
        libro.active.append(['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'])
        libro.active.append(['RO123', 2025, 1, 1])
        buffer = io.BytesIO(); libro.save(buffer); libro.close()
        self.servidor.entrada = buffer.getvalue()
        self.stack.enter_context(patch.object(adaptador, 'cargar_configuracion', return_value=Configuracion(root=ROOT)))
        real = lambda config, entrada: descargar_reportes(config, entrada=entrada,
            fabrica_navegador=NavegadorMensualFalso, pausa=lambda _: None)
        self.stack.enter_context(patch.object(adaptador, 'descargar_reportes', side_effect=real))
        resultado = adaptador.ejecutar_actual(self.raiz, 'descargar-reportes', self.valores,
            fabrica=lambda: crear_cliente_cola(self.valores))
        self.assertEqual(resultado['fase'], 'COMPLETADA')
        with ZipFile(io.BytesIO(self.servidor.reporte)) as archivo:
            self.assertTrue(any('/descargas/' in n for n in archivo.namelist()))
