from io import BytesIO
import json
import logging
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook, load_workbook

from config.base_datos import BaseDatos
from config.configuracion import Configuracion, ROOT
from integraciones.backend_api import (
    ClienteBackend, EntradaInvalida, ErrorAPI, ErrorAPIPermanente, validar_configuracion_backend,
)
from integraciones.bloqueo import bloqueo_proyecto
from integraciones.proceso import consultar
from integraciones.proceso_mensual import HistorialMensual, descargar_reportes
from test_acme import NavegadorFalso, item
from test_reportes_mensuales import NavegadorMensualFalso
from worker import Ejecutor, ejecutar

# pip incluye una copia de requests. Solo para pruebas sin acceso a PyPI;
# el ejecutor real exige la dependencia publica de requirements.txt.
try:
    import requests as requests_pruebas
except ModuleNotFoundError:
    try:
        from pip._vendor import requests as requests_pruebas
    except ImportError:
        requests_pruebas = None


def excel(filas):
    libro = Workbook()
    for fila in filas:
        libro.active.append(fila)
    memoria = BytesIO()
    libro.save(memoria)
    libro.close()
    return memoria.getvalue()


def configuracion(**cambios):
    valores = dict(backend_api_url='https://backend.example.test/api/rpa',
                   backend_api_token='token-privado-de-prueba', robot_id='acme-windows-01')
    valores.update(cambios)
    return Configuracion(**valores)


class Respuesta:
    def __init__(self, codigo=200, json_datos=None, bloques=()):
        self.status_code = codigo
        self.datos = json_datos
        self.bloques = bloques
        self.cerrada = False

    def json(self):
        if isinstance(self.datos, Exception):
            raise self.datos
        return self.datos

    def iter_content(self, chunk_size):
        for bloque in self.bloques:
            if isinstance(bloque, Exception):
                raise bloque
            yield bloque

    def close(self):
        self.cerrada = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ClienteBackendTests(unittest.TestCase):
    def setUp(self):
        self.config = configuracion()
        self.session = Mock()
        self.cliente = ClienteBackend(self.config, session=self.session)

    def test_asignacion_vacia_y_contrato_autenticado_sin_redirecciones(self):
        respuesta = Respuesta(204)
        self.session.request.return_value = respuesta
        self.assertIsNone(self.cliente.tomar())
        self.session.request.assert_called_once_with(
            'POST', 'https://backend.example.test/api/rpa/solicitudes/tomar',
            headers={'Authorization': 'Bearer token-privado-de-prueba'},
            timeout=(10, 60), allow_redirects=False, json={'robot_id': 'acme-windows-01'})
        self.assertTrue(respuesta.cerrada)

    def test_asignacion_json_valida_y_ids_no_utilizables_como_rutas(self):
        for identificador in ('id-1', '../otro', '', 'id/con/slash'):
            with self.subTest(identificador=identificador):
                self.session.request.return_value = Respuesta(json_datos={
                    'id': identificador, 'proceso': 'consultar', 'nombre_archivo': 'filtros.xlsx'})
                if identificador == 'id-1':
                    self.assertEqual(self.cliente.tomar()['id'], identificador)
                else:
                    with self.assertRaises(ErrorAPIPermanente):
                        self.cliente.tomar()
        self.session.request.return_value = Respuesta(json_datos=ValueError('json'))
        with self.assertRaises(ErrorAPIPermanente):
            self.cliente.tomar()

    def test_codigos_http_reintentables_y_permanentes(self):
        for codigo in (429, 500, 503, 401, 403, 400, 404, 302):
            respuesta = Respuesta(codigo)
            self.session.request.return_value = respuesta
            tipo = ErrorAPI if codigo == 429 or codigo >= 500 else ErrorAPIPermanente
            with self.subTest(codigo=codigo), self.assertRaises(tipo):
                self.cliente.tomar()
            self.assertTrue(respuesta.cerrada)

    def test_descarga_atomica_y_elimina_parciales(self):
        with tempfile.TemporaryDirectory() as temporal:
            destino = Path(temporal) / 'entrada.xlsx'
            self.session.request.return_value = Respuesta(bloques=[b'primer', b'bloque'])
            self.cliente.descargar('id-1', destino)
            self.assertEqual(destino.read_bytes(), b'primerbloque')
            self.session.request.return_value = Respuesta(bloques=[b'demasiado grande'])
            with patch('integraciones.backend_api.MAX_ENTRADA', 3), self.assertRaises(EntradaInvalida):
                self.cliente.descargar('id-1', destino)
            self.assertEqual(destino.read_bytes(), b'primerbloque')
            self.assertFalse(destino.with_suffix('.xlsx.part').exists())
            self.session.request.return_value = Respuesta(bloques=[])
            with self.assertRaises(EntradaInvalida):
                self.cliente.descargar('id-1', destino)

    def test_envio_multipart_con_archivo_o_error_sin_archivo(self):
        self.session.request.return_value = Respuesta(204)
        recibidos = []

        def recibir(*args, **kwargs):
            campos = kwargs['files']
            recibidos.append({
                'estado': campos['estado'][1], 'resumen': json.loads(campos['resumen'][1]),
                'archivo': campos['archivo'][1].read() if 'archivo' in campos else None})
            return Respuesta(204)

        self.session.request.side_effect = recibir
        with tempfile.TemporaryDirectory() as temporal:
            reporte = Path(temporal) / 'resultado.xlsx'
            reporte.write_bytes(b'excel-de-prueba')
            self.cliente.enviar_resultado('id-1', {'estado': 'completado', 'leidos': 2}, reporte)
        self.cliente.enviar_resultado('id-2', {'estado': 'error', 'error': 'Entrada invalida'}, None)
        self.assertEqual(recibidos[0]['archivo'], b'excel-de-prueba')
        self.assertEqual(recibidos[0]['resumen']['leidos'], 2)
        self.assertIsNone(recibidos[1]['archivo'])

    def test_configuracion_y_ocultacion_del_token(self):
        validar_configuracion_backend(self.config)
        for cambios in ({'backend_api_url': 'http://backend.test'},
                        {'backend_api_url': 'https://usuario:clave@backend.test'},
                        {'backend_api_url': 'https://backend.test?token=algo'},
                        {'backend_api_token': ''}, {'robot_id': '../robot'},
                        {'intervalo_consulta_segundos': 0}, {'intervalo_consulta_segundos': float('nan')}):
            with self.subTest(cambios=cambios), self.assertRaises(ValueError):
                validar_configuracion_backend(configuracion(**cambios))
        self.assertNotIn(self.config.backend_api_token, repr(self.config))
        self.assertEqual(self.config.ocultar(self.config.backend_api_token), '[OCULTO]')


class BackendFalso:
    def __init__(self, entrada):
        self.entrada = entrada
        self.solicitudes = [{'id': 'solicitud-1', 'proceso': 'consultar', 'nombre_archivo': 'libre.xlsx'}]
        self.descargas = 0
        self.intentos_envio = 0
        self.fallos_envio = 0
        self.enviados = []
        self.cerrado = False

    def tomar(self):
        return self.solicitudes.pop(0) if self.solicitudes else None

    def descargar(self, remoto_id, destino):
        self.descargas += 1
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(self.entrada)

    def enviar_resultado(self, remoto_id, resumen, reporte):
        self.intentos_envio += 1
        if self.fallos_envio:
            self.fallos_envio -= 1
            raise ErrorAPI('Conexion interrumpida durante el envio')
        self.enviados.append((remoto_id, resumen, reporte.read_bytes() if reporte else None))

    def cerrar(self):
        self.cerrado = True


@unittest.skipIf(requests_pruebas is None, 'Se necesita requests para verificar el transporte HTTP')
class TransporteHTTPTests(unittest.TestCase):
    def setUp(self):
        self.config = configuracion()
        self.peticiones = []
        self.respuestas = []
        peticiones, respuestas = self.peticiones, self.respuestas

        class Adaptador(requests_pruebas.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                peticiones.append((request, kwargs))
                estado, contenido = respuestas.pop(0)
                if isinstance(contenido, Exception):
                    raise contenido
                respuesta = requests_pruebas.Response()
                respuesta.status_code = estado
                respuesta._content = contenido
                respuesta._content_consumed = True
                respuesta.request = request
                return respuesta

            def close(self):
                pass

        self.session = requests_pruebas.Session()
        self.session.mount('https://', Adaptador())
        self.addCleanup(self.session.close)
        for contexto in (patch('integraciones.backend_api.requests', requests_pruebas),
                         patch('integraciones.backend_api.ERRORES_RED', (requests_pruebas.RequestException,))):
            contexto.start()
            self.addCleanup(contexto.stop)
        self.cliente = ClienteBackend(self.config, self.session)

    def test_requests_prepara_multipart_real_y_json_autenticados(self):
        self.respuestas.extend([(204, b''), (204, b'')])
        self.assertIsNone(self.cliente.tomar())
        peticion, opciones = self.peticiones[0]
        self.assertEqual(json.loads(peticion.body), {'robot_id': 'acme-windows-01'})
        self.assertEqual(peticion.headers['Authorization'], 'Bearer token-privado-de-prueba')
        self.assertEqual(opciones['timeout'], (10, 60))
        with tempfile.TemporaryDirectory() as temporal:
            reporte = Path(temporal) / 'seguimiento.xlsx'
            reporte.write_bytes(b'EXCEL_BINARIO')
            self.cliente.enviar_resultado('id-1', {'estado': 'completado', 'leidos': 3}, reporte)
        peticion, _ = self.peticiones[1]
        self.assertTrue(peticion.headers['Content-Type'].startswith('multipart/form-data; boundary='))
        self.assertEqual(peticion.method, 'PUT')
        self.assertIn(b'name="resumen"', peticion.body)
        self.assertIn(b'filename="seguimiento.xlsx"', peticion.body)
        self.assertIn(b'EXCEL_BINARIO', peticion.body)

    def test_timeout_de_red_es_reintentable_sin_exponer_secreto(self):
        self.respuestas.append((0, requests_pruebas.Timeout('Error con token-privado-de-prueba')))
        with self.assertRaises(ErrorAPI) as error:
            self.cliente.tomar()
        self.assertNotIn(self.config.backend_api_token, str(error.exception))

    def test_respuesta_perdida_al_tomar_se_puede_repetir(self):
        solicitud = {'id': 'id-1', 'proceso': 'consultar', 'nombre_archivo': 'datos.xlsx'}
        self.respuestas.extend([(0, requests_pruebas.ConnectionError('Respuesta perdida')),
                                (200, json.dumps(solicitud).encode())])
        with self.assertRaises(ErrorAPI):
            self.cliente.tomar()
        self.assertEqual(self.cliente.tomar(), solicitud)
        self.assertEqual(self.peticiones[0][0].body, self.peticiones[1][0].body)


class EjecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='acme-worker-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = configuracion(root=self.root)
        (self.root / 'config').mkdir()
        (self.root / 'config/selectores.json').write_text(
            (ROOT / 'config/selectores.json').read_text(encoding='utf-8'), encoding='utf-8')
        self.backend = BackendFalso(excel([['tipo', 'estado'], ['WI5', 'Open']]))
        self.procesos = {
            'consultar': Mock(side_effect=lambda config, **kwargs: consultar(
                config, fabrica_navegador=lambda c: NavegadorFalso(c, items=[item(1), item(2, tipo='WI4')]),
                **kwargs)),
            'descargar-reportes': Mock(side_effect=lambda config, **kwargs: descargar_reportes(
                config, fabrica_navegador=NavegadorMensualFalso, **kwargs)),
        }
        self.ejecutor = Ejecutor(self.config, self.backend, self.procesos)
        self.addCleanup(self.cerrar_logs)

    def cerrar_logs(self):
        logger = logging.getLogger(self.config.nombre_proceso)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def completar(self):
        for _ in range(5):
            if not self.ejecutor.ciclo():
                break

    def test_work_items_aisla_archivo_y_devuelve_resumen_y_excel(self):
        (self.root / 'inputs').mkdir()
        # La entrada manual de la carpeta raiz nunca se procesa por el worker.
        (self.root / 'inputs/otro.xlsx').write_bytes(b'no debe abrirse')
        self.completar()
        self.assertEqual(len(self.backend.enviados), 1)
        remoto_id, resumen, contenido = self.backend.enviados[0]
        self.assertEqual((remoto_id, resumen['estado'], resumen['leidos']), ('solicitud-1', 'completado', 1))
        self.assertEqual(self.procesos['consultar'].call_count, 1)
        entrada = self.procesos['consultar'].call_args.kwargs['entrada']
        self.assertEqual(entrada.name, 'entrada.xlsx')
        self.assertNotEqual(entrada.parent, self.root / 'inputs')
        libro = load_workbook(BytesIO(contenido), read_only=True)
        self.assertEqual(libro.sheetnames, ['resultados', 'errores', 'resumen'])
        libro.close()
        self.assertIsNone(self.ejecutor.historial.pendiente())

    def test_mensuales_envia_solo_excel_de_seguimiento(self):
        self.backend.solicitudes[0]['proceso'] = 'descargar-reportes'
        self.backend.entrada = excel([['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'], ['00123', 2025, 1, 2]])
        self.completar()
        _, resumen, contenido = self.backend.enviados[0]
        self.assertEqual((resumen['estado'], resumen['descargado']), ('completado', 2))
        self.assertTrue(contenido.startswith(b'PK'))
        self.assertEqual(len(list((self.root / 'descargas').rglob('*.csv'))), 2)
        self.assertEqual(self.procesos['descargar-reportes'].call_count, 1)

    def test_sin_coincidencias_tambien_envia_excel(self):
        self.backend.entrada = excel([['tipo', 'estado'], ['INEXISTENTE', 'Open']])
        self.completar()
        self.assertEqual(self.backend.enviados[0][1]['leidos'], 0)
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'completado')
        self.assertIsNotNone(self.backend.enviados[0][2])

    def test_entrada_invalida_no_ejecuta_rpa(self):
        self.backend.entrada = excel([['columna_incorrecta'], ['valor']])
        self.completar()
        self.procesos['consultar'].assert_not_called()
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'error')
        self.assertIsNone(self.backend.enviados[0][2])

    def test_nombre_remoto_no_controla_la_ruta_local(self):
        self.backend.solicitudes[0]['nombre_archivo'] = '../../fuera.xlsx'
        self.completar()
        self.assertFalse((self.root / 'fuera.xlsx').exists())
        self.assertEqual(self.procesos['consultar'].call_args.kwargs['entrada'].name, 'entrada.xlsx')

    def test_proceso_o_extension_no_admitidos(self):
        for cambios in ({'proceso': 'borrar'}, {'nombre_archivo': 'entrada.xls'}):
            self.backend.solicitudes = [dict(id='invalido-' + str(len(self.backend.enviados)),
                proceso='consultar', nombre_archivo='entrada.xlsx') | cambios]
            self.completar()
        self.procesos['consultar'].assert_not_called()
        self.assertEqual(self.backend.descargas, 0)
        self.assertTrue(all(envio[1]['estado'] == 'error' for envio in self.backend.enviados))

    def test_reinicio_reintenta_envio_sin_repetir_rpa(self):
        self.ejecutor.ciclo()
        self.ejecutor.ciclo()
        self.backend.fallos_envio = 1
        with self.assertRaises(ErrorAPI):
            self.ejecutor.ciclo()
        self.assertEqual(self.ejecutor.historial.pendiente()['estado'], 'pendiente_envio')
        self.ejecutor = Ejecutor(self.config, self.backend, self.procesos)
        self.completar()
        self.assertEqual(self.procesos['consultar'].call_count, 1)
        self.assertEqual(self.backend.intentos_envio, 2)

    def test_backend_guarda_resultado_pero_se_pierde_la_respuesta(self):
        self.ejecutor.ciclo()
        self.ejecutor.ciclo()
        enviar = self.backend.enviar_resultado

        def aceptar_y_perder_respuesta(*args):
            enviar(*args)
            raise ErrorAPI('Respuesta perdida despues de aceptar el resultado')

        with patch.object(self.backend, 'enviar_resultado', side_effect=aceptar_y_perder_respuesta):
            with self.assertRaises(ErrorAPI):
                self.ejecutor.ciclo()
        self.ejecutor = Ejecutor(self.config, self.backend, self.procesos)
        self.completar()
        self.assertEqual(self.backend.enviados[0], self.backend.enviados[1])
        self.assertEqual(self.procesos['consultar'].call_count, 1)

    def test_solicitud_repetida_reenvia_mismo_resultado(self):
        original = dict(self.backend.solicitudes[0])
        self.completar()
        self.backend.solicitudes.append(original)
        self.completar()
        self.assertEqual(len(self.backend.enviados), 2)
        self.assertEqual(self.backend.enviados[0], self.backend.enviados[1])
        self.assertEqual(self.procesos['consultar'].call_count, 1)
        self.assertEqual(self.backend.descargas, 1)

    def test_backend_no_puede_cambiar_una_solicitud_existente(self):
        self.completar()
        self.backend.solicitudes.append({'id': 'solicitud-1', 'proceso': 'descargar-reportes', 'nombre_archivo': 'otro.xlsx'})
        with self.assertRaises(ErrorAPIPermanente):
            self.ejecutor.ciclo()

    def test_descarga_interrumpida_conserva_estado_para_reintento(self):
        with patch.object(self.backend, 'descargar', side_effect=ErrorAPI('Sin conexion')):
            with self.assertRaises(ErrorAPI):
                self.ejecutor.ciclo()
        self.assertEqual(self.ejecutor.historial.pendiente()['estado'], 'descargando')
        self.completar()
        self.assertEqual(self.procesos['consultar'].call_count, 1)

    def test_bloqueo_ocupado_no_pierde_solicitud(self):
        self.ejecutor.ciclo()
        with bloqueo_proyecto(self.root / '.runtime/consulta.lock'):
            with self.assertRaisesRegex(RuntimeError, 'Ya hay una consulta'):
                self.ejecutor.ciclo()
        self.assertEqual(self.ejecutor.historial.pendiente()['estado'], 'preparado')
        self.completar()
        self.assertEqual(self.procesos['consultar'].call_count, 1)

    def test_reinicio_tras_finalizar_rpa_recupera_reporte_sin_repetirlo(self):
        self.ejecutor.ciclo()
        with patch.object(self.ejecutor.historial, 'resultado', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.ejecutor.ciclo()
        self.assertEqual(self.ejecutor.historial.pendiente()['estado'], 'ejecutando')
        self.ejecutor = Ejecutor(self.config, self.backend, self.procesos)
        self.completar()
        self.assertEqual(self.procesos['consultar'].call_count, 1)
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'completado')

    def test_reinicio_de_ejecucion_parcial_envia_error_y_excel(self):
        self.ejecutor.ciclo()
        trabajo = self.ejecutor.historial.pendiente()
        base = BaseDatos(self.config.ruta(self.config.db))
        ejecucion, archivo_id = base.iniciar(Path(trabajo['entrada']), None)
        base.encolar(ejecucion, archivo_id, [item(1), item(2)], 2)
        base.guardar(base.tareas(ejecucion)[0], 'Detalle confirmado de 1')
        self.ejecutor.historial.actualizar(trabajo['id'], estado='ejecutando', ejecucion_id=ejecucion)
        self.completar()
        self.procesos['consultar'].assert_not_called()
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'error')
        self.assertEqual(self.backend.enviados[0][1]['leidos'], 1)
        self.assertIsNotNone(self.backend.enviados[0][2])

    def test_interrupcion_sin_id_no_repite_rpa(self):
        self.ejecutor.ciclo()
        trabajo = self.ejecutor.historial.pendiente()
        self.ejecutor.historial.actualizar(trabajo['id'], estado='ejecutando')
        self.completar()
        self.procesos['consultar'].assert_not_called()
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'error')

    def test_recupera_mensuales_interrumpido_sin_repetir_descargas(self):
        self.backend.solicitudes[0]['proceso'] = 'descargar-reportes'
        self.backend.entrada = excel([['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'], ['00123', 2025, 1, 2]])
        self.ejecutor.ciclo()
        trabajo = self.ejecutor.historial.pendiente()
        historial = HistorialMensual(self.config.ruta(self.config.db))
        ejecucion = historial.iniciar(Path(trabajo['entrada']), None)
        from integraciones.reportes_mensuales import leer_solicitudes
        historial.encolar(ejecucion, leer_solicitudes(Path(trabajo['entrada'])))
        historial.resolver(historial.tareas(ejecucion)[0]['id'], 'descargado', archivo='confirmado.csv')
        self.ejecutor.historial.actualizar(trabajo['id'], estado='ejecutando', ejecucion_id=ejecucion)
        self.completar()
        self.procesos['descargar-reportes'].assert_not_called()
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'error')
        self.assertEqual(self.backend.enviados[0][1]['descargado'], 1)
        self.assertEqual(self.backend.enviados[0][1]['errores'], 1)
        self.assertIsNotNone(self.backend.enviados[0][2])

    def test_scraping_desactivado_conserva_preparado(self):
        self.ejecutor.ciclo()
        self.config.modulo_scraping = False
        with self.assertRaisesRegex(RuntimeError, 'MODULO_SCRAPING=false'):
            self.ejecutor.ciclo()
        self.assertEqual(self.ejecutor.historial.pendiente()['estado'], 'preparado')
        self.procesos['consultar'].assert_not_called()

    def test_fallo_generacion_reporte_se_entrega_como_error_sin_archivo(self):
        self.ejecutor.ciclo()
        with patch('integraciones.proceso.generar_reporte', side_effect=OSError('Disco no disponible')):
            with patch('worker.generar_reporte', side_effect=OSError('Disco no disponible')):
                self.ejecutor.ciclo()
        self.ejecutor.ciclo()
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'error')
        self.assertIsNone(self.backend.enviados[0][2])
        self.assertEqual(self.procesos['consultar'].call_count, 1)

    def test_excel_resultado_perdido_envia_error_sin_repetir_rpa(self):
        self.ejecutor.ciclo()
        self.ejecutor.ciclo()
        trabajo = self.ejecutor.historial.pendiente()
        Path(trabajo['reporte']).unlink()
        self.completar()
        self.assertEqual(self.backend.enviados[0][1]['estado'], 'error')
        self.assertIsNone(self.backend.enviados[0][2])
        self.assertEqual(self.procesos['consultar'].call_count, 1)

    def test_detencion_conserva_trabajo_y_cierra_cliente(self):
        detener = threading.Event()
        with patch('worker.Ejecutor', return_value=self.ejecutor):
            with patch.object(self.ejecutor, 'ciclo', side_effect=lambda: detener.set() or True):
                ejecutar(self.config, detener, self.backend)
        self.assertTrue(self.backend.cerrado)

    def test_auth_error_detiene_en_lugar_de_reintentar(self):
        self.backend.tomar = Mock(side_effect=ErrorAPIPermanente('HTTP 401'))
        with self.assertRaises(ErrorAPIPermanente):
            ejecutar(self.config, threading.Event(), self.backend)
        self.backend.tomar.assert_called_once()
        self.assertTrue(self.backend.cerrado)

    def test_backoff_y_corte_interrumpible(self):
        detener = Mock()
        detener.is_set.side_effect = [False] * 6 + [True]
        with patch('worker.Ejecutor', return_value=self.ejecutor):
            with patch.object(self.ejecutor, 'ciclo', side_effect=ErrorAPI('Sin conexion')):
                ejecutar(self.config, detener, self.backend)
        self.assertEqual([llamada.args[0] for llamada in detener.wait.call_args_list], [5, 10, 20, 40, 60, 60])


if __name__ == '__main__':
    unittest.main()
