"""Prueba HTTP real contra un servidor de contrato aislado, no el backend productivo."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

try:
    from weyland import ClienteWeyland as ClienteBot, ConfiguracionWeyland
    from weyland import ErrorWeyland, ErrorHTTPWeyland, ejecutar_solicitud as ejecutar, ResultadoBot
except ModuleNotFoundError:
    from integraciones.weyland import ClienteWeyland as ClienteBot, ConfiguracionWeyland
    from integraciones.weyland import ErrorWeyland, ErrorHTTPWeyland, ejecutar_solicitud as ejecutar, ResultadoBot
from test_weyland import detalle


class PruebasHttp(unittest.TestCase):
    def setUp(self):
        self.registro = []
        self.antiguo = False
        self.expirar = False
        self.rechazar_clave = False
        self.expirar_reporte = False
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def responder(self, estado, cuerpo):
                self.send_response(estado)
                self.send_header('Content-Type', 'application/json')
                if self.path.endswith('/auth/csrf'):
                    self.send_header('Set-Cookie', 'wa_csrf=csrf_prueba; Path=/')
                if self.path.endswith('/auth/login') and estado == 200:
                    self.send_header('Set-Cookie', 'wa_sesion=sesion_prueba; Path=/')
                self.end_headers()
                self.wfile.write(json.dumps(cuerpo).encode())

            def do_GET(self):
                test.registro.append(('GET', self.path, dict(self.headers), b''))
                if self.path.endswith('/auth/csrf'):
                    return self.responder(200, {'csrf': 'csrf_prueba'})
                if self.path.endswith('/auth/perfil'):
                    return self.responder(200, {'usuario': {'correo': 'robot@example.test'}})
                if self.path == '/api/bot/ejecuciones/123':
                    if test.antiguo:
                        return self.responder(404, {'error': {'codigo': 'NO_ENCONTRADO'}})
                    return self.responder(200, detalle())
                self.responder(404, {})

            def do_POST(self):
                cuerpo = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                test.registro.append(('POST', self.path, dict(self.headers), cuerpo))
                if self.path.endswith('/auth/login'):
                    test.assertEqual(json.loads(cuerpo)['correo'], 'robot@example.test')
                    test.assertEqual(self.headers.get('X-CSRFToken'), 'csrf_prueba')
                    return self.responder(200, {'usuario': {'correo': 'robot@example.test'}})
                test.assertIn('wa_sesion=sesion_prueba', self.headers.get('Cookie', ''))
                test.assertEqual(self.headers.get('X-CSRFToken'), 'csrf_prueba')
                test.assertEqual(self.headers.get('X-Api-Key'), 'clave_ficticia')
                test.assertTrue(self.headers.get('Idempotency-Key'))
                if test.rechazar_clave:
                    return self.responder(401, {'error': {'codigo': 'API_KEY_INVALIDA'}})
                if test.expirar:
                    test.expirar = False
                    return self.responder(401, {'error': {'codigo': 'SESION_EXPIRADA'}})
                if self.path.endswith('/siguiente'):
                    test.assertEqual(json.loads(cuerpo), {'solicitud_id': 123, 'ejecucion_id': 123})
                    return self.responder(200, detalle(estado='EN_PROCESO'))
                if self.path.endswith('/reporte'):
                    if test.expirar_reporte:
                        test.expirar_reporte = False
                        return self.responder(401, {'error': {'codigo': 'SESION_EXPIRADA'}})
                    from email.parser import BytesParser
                    from email.policy import default
                    mensaje = BytesParser(policy=default).parsebytes(
                        b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + cuerpo)
                    partes = {p.get_param('name', header='content-disposition'): p for p in mensaje.iter_parts()}
                    metadatos = json.loads(partes['metadatos'].get_payload(decode=True))
                    test.assertTrue(partes['archivo'].get_payload(decode=True).startswith(b'PK'))
                    return self.responder(200, {'solicitud_id': 123, 'ejecucion_id': 123,
                        'estado': 'COMPLETADA', 'reporte': {'sha256': metadatos['export']['sha256']}})
                self.responder(404, {})

        self.servidor = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.hilo = threading.Thread(target=self.servidor.serve_forever, daemon=True)
        self.hilo.start()
        base = f'http://127.0.0.1:{self.servidor.server_port}/api'
        self.cliente = ClienteBot(ConfiguracionWeyland(base, base + '/auth/login',
            'robot@example.test', 'contrasena_ficticia', 'clave_ficticia', permitir_http_local=True))

    def tearDown(self):
        self.cliente.cerrar()
        self.servidor.shutdown()
        self.servidor.server_close()
        self.hilo.join()

    def runner(self, carpeta, entrada, parametros):
        salida = carpeta / 'reporte.txt'
        salida.write_bytes(b'reporte de prueba')
        return ResultadoBot((salida,))

    def test_login_inicio_y_multipart_por_http(self):
        with tempfile.TemporaryDirectory() as carpeta:
            resultado = ejecutar(self.cliente, 123, 'DEMO', Path(carpeta), self.runner)
        self.assertEqual(resultado['fase'], 'COMPLETADA')
        rutas = [p for m, p, _, _ in self.registro if m == 'POST']
        self.assertEqual(rutas, ['/api/auth/login', '/api/bot/ejecuciones/siguiente',
                                 '/api/bot/ejecuciones/123/reporte'])

    def test_backend_antiguo_no_llama_siguiente(self):
        self.antiguo = True
        with tempfile.TemporaryDirectory() as carpeta:
            with self.assertRaisesRegex(ErrorWeyland, 'contrato por ID'):
                ejecutar(self.cliente, 123, 'DEMO', Path(carpeta), self.runner)
        self.assertFalse(any(p.endswith('/siguiente') for _, p, _, _ in self.registro))

    def test_sesion_expirada_reautentica_con_misma_clave(self):
        self.expirar = True
        self.cliente.iniciar(123, 'DEMO', 'clave_inicio_prueba')
        inicios = [h for _, p, h, _ in self.registro if p.endswith('/siguiente')]
        self.assertEqual(len(inicios), 2)
        self.assertEqual(inicios[0]['Idempotency-Key'], inicios[1]['Idempotency-Key'])
        self.assertEqual(sum(p.endswith('/auth/login') for _, p, _, _ in self.registro), 2)

    def test_clave_invalida_no_reintenta_login(self):
        self.rechazar_clave = True
        with self.assertRaises(ErrorHTTPWeyland):
            self.cliente.iniciar(123, 'DEMO', 'clave_inicio_prueba')
        self.assertEqual(sum(p.endswith('/auth/login') for _, p, _, _ in self.registro), 1)

    def test_expiracion_durante_multipart_reenvia_archivo_completo(self):
        self.expirar_reporte = True
        with tempfile.TemporaryDirectory() as carpeta:
            resultado = ejecutar(self.cliente, 123, 'DEMO', Path(carpeta), self.runner)
        self.assertEqual(resultado['fase'], 'COMPLETADA')
        reportes = [h for _, p, h, _ in self.registro if p.endswith('/reporte')]
        self.assertEqual(len(reportes), 2)
        self.assertEqual(reportes[0]['Idempotency-Key'], reportes[1]['Idempotency-Key'])


if __name__ == '__main__':
    unittest.main()
