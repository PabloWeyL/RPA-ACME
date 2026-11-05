import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import requests
from requests.adapters import BaseAdapter

from config.api import crear_cliente_api
from integraciones.api_http import (
    ClienteAPI, ConfiguracionAPI, ErrorAPI, ErrorAutenticacionAPI, ErrorTemporalAPI,
)


def tokens(access='access-1', refresh='refresh-1', ttl=300):
    return dict(access_token=access, refresh_token=refresh, expires_in=ttl, token_type='Bearer')


class Respuesta:
    def __init__(self, codigo=200, datos=None):
        self.status_code = codigo
        self.datos = datos
        self.cerrada = False

    def json(self):
        if isinstance(self.datos, Exception):
            raise self.datos
        return self.datos

    def close(self):
        self.cerrada = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AutenticacionTests(unittest.TestCase):
    def setUp(self):
        self.config = ConfiguracionAPI('https://api.example.test/v1', 'robot@example.test',
                                       'secreto-prueba', 'https://login.example.test/acceso')
        self.session = Mock()
        self.reloj = Mock(return_value=100)
        self.cliente = ClienteAPI(self.config, self.session, self.reloj)
        self.addCleanup(self.cliente.cerrar)

    def preparar(self, *respuestas):
        self.session.request.side_effect = respuestas

    def test_login_sin_bearer_y_reutiliza_token_hasta_caducar(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta(), Respuesta())
        for _ in range(2):
            with self.cliente.solicitar('GET', '/trabajos'):
                pass
        llamadas = self.session.request.call_args_list
        self.assertEqual(len(llamadas), 3)
        self.assertNotIn('headers', llamadas[0].kwargs)
        self.assertEqual(llamadas[0].kwargs['json'], {
            'correo': 'robot@example.test', 'password': 'secreto-prueba'})
        self.assertEqual(llamadas[0].args[1], 'https://login.example.test/acceso')
        self.assertTrue(llamadas[1].args[1].startswith('https://api.example.test/v1/'))
        for llamada in llamadas[1:]:
            self.assertEqual(llamada.kwargs['headers']['Authorization'], 'Bearer access-1')
            self.assertEqual(llamada.kwargs['timeout'], (10, 60))
            self.assertFalse(llamada.kwargs['allow_redirects'])

    def test_renovacion_anticipada_usa_y_conserva_refresh_rotado(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta(),
                      Respuesta(datos=tokens('access-2', 'refresh-2')), Respuesta(),
                      Respuesta(datos=tokens('access-3', 'refresh-3')), Respuesta())
        with self.cliente.solicitar('GET', '/trabajos'):
            pass
        self.reloj.return_value = 371
        with self.cliente.solicitar('GET', '/trabajos'):
            pass
        self.reloj.return_value = 642
        with self.cliente.solicitar('GET', '/trabajos'):
            pass
        llamadas = self.session.request.call_args_list
        self.assertEqual(llamadas[2].kwargs['json'], {'refresh_token': 'refresh-1'})
        self.assertEqual(llamadas[4].kwargs['json'], {'refresh_token': 'refresh-2'})
        self.assertNotIn('headers', llamadas[2].kwargs)
        self.assertEqual(llamadas[5].kwargs['headers']['Authorization'], 'Bearer access-3')

    def test_401_renueva_y_repite_mismo_json_e_idempotencia_una_vez(self):
        rechazada = Respuesta(401)
        self.preparar(Respuesta(datos=tokens()), rechazada,
                      Respuesta(datos=tokens('access-2', 'refresh-2')), Respuesta())
        datos = {'estado': 'completado', 'export': {'contenido': 'ZXhjZWw='}}
        with self.cliente.solicitar('PUT', '/trabajos/123/resultado', datos=datos,
                                    idempotencia='resultado-123'):
            pass
        primera, segunda = (self.session.request.call_args_list[i] for i in (1, 3))
        self.assertTrue(rechazada.cerrada)
        self.assertEqual(primera.kwargs['json'], segunda.kwargs['json'])
        self.assertEqual(primera.kwargs['headers']['Idempotency-Key'],
                         segunda.kwargs['headers']['Idempotency-Key'])
        self.assertEqual(segunda.kwargs['headers']['Authorization'], 'Bearer access-2')

    def test_dos_llamadas_concurrentes_no_consumen_dos_veces_el_refresh(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta(),
                      Respuesta(datos=tokens('access-2', 'refresh-2')), Respuesta(), Respuesta())
        with self.cliente.solicitar('GET', '/trabajos'):
            pass
        self.reloj.return_value = 371
        barrera = threading.Barrier(2)

        def consultar():
            barrera.wait(timeout=5)
            with self.cliente.solicitar('GET', '/trabajos'):
                pass

        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(consultar) for _ in range(2)]
            for futuro in futuros:
                futuro.result(timeout=5)
        refresh = [c for c in self.session.request.call_args_list
                   if c.args[1].endswith('/auth/refresh')]
        self.assertEqual(len(refresh), 1)
        self.assertEqual(refresh[0].kwargs['json'], {'refresh_token': 'refresh-1'})

    def test_segundo_401_detiene_y_no_inicia_bucle_de_login(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta(401),
                      Respuesta(datos=tokens('access-2', 'refresh-2')), Respuesta(401))
        with self.assertRaises(ErrorAutenticacionAPI):
            self.cliente.solicitar('GET', '/trabajos')
        with self.assertRaises(ErrorAutenticacionAPI):
            self.cliente.solicitar('GET', '/trabajos')
        self.assertEqual(self.session.request.call_count, 4)
        self.assertIsNone(self.cliente._access_token)

    def test_403_no_intenta_renovacion(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta(403))
        with self.assertRaisesRegex(ErrorAPI, '403'):
            self.cliente.solicitar('GET', '/trabajos')
        self.assertEqual(self.session.request.call_count, 2)

    def test_refresh_revocado_bloquea_sin_filtrar_respuesta(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta(401),
                      Respuesta(400, {'error': 'invalid_grant', 'detalle': 'refresh-1'}))
        with self.assertRaises(ErrorAutenticacionAPI) as error:
            self.cliente.solicitar('GET', '/trabajos')
        self.assertNotIn('refresh-1', str(error.exception))
        with self.assertRaises(ErrorAutenticacionAPI):
            self.cliente.solicitar('GET', '/trabajos')
        self.assertEqual(self.session.request.call_count, 3)

    def test_timeout_refresh_no_reenvia_token_posiblemente_consumido(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta(401),
                      requests.Timeout('contenido sensible refresh-1'))
        with self.assertRaises(ErrorTemporalAPI) as error:
            self.cliente.solicitar('GET', '/trabajos')
        self.assertNotIn('refresh-1', str(error.exception))
        with self.assertRaises(ErrorAutenticacionAPI):
            self.cliente.solicitar('GET', '/trabajos')
        self.assertEqual(self.session.request.call_count, 3)

    def test_respuestas_auth_invalidas_no_envian_peticion_de_negocio(self):
        invalidas = [None, [], ValueError('json sensible'), {},
                     tokens(ttl=True), tokens(ttl=0), tokens(ttl=-1),
                     tokens(ttl=float('nan')), tokens(ttl=float('inf')),
                     tokens(access='token\r\ninvalido'), tokens(refresh='')]
        for datos in invalidas:
            with self.subTest(datos=type(datos).__name__):
                sesion = Mock()
                sesion.request.return_value = Respuesta(datos=datos)
                with ClienteAPI(self.config, sesion) as cliente:
                    with self.assertRaises(ErrorAutenticacionAPI):
                        cliente.solicitar('GET', '/trabajos')
                self.assertEqual(sesion.request.call_count, 1)

    def test_login_fallido_no_expone_password(self):
        self.preparar(requests.ConnectionError('secreto-prueba'))
        with self.assertRaises(ErrorTemporalAPI) as error:
            self.cliente.solicitar('GET', '/trabajos')
        self.assertNotIn('secreto-prueba', str(error.exception))
        self.assertNotIn('secreto-prueba', repr(self.config))
        self.assertNotIn('secreto-prueba', repr(self.cliente))

    def test_url_login_insegura_se_rechaza_antes_de_enviar_credenciales(self):
        for login in ('http://login.test', 'https://user:pass@login.test',
                      'https://login.test/acceso?password=valor'):
            with self.subTest(login=login), self.assertRaises(ValueError):
                ConfiguracionAPI('https://api.example.test/v1', 'robot@example.test',
                                 'secret', login)
        self.session.request.assert_not_called()

    def test_http_transitorio_no_repite_operacion_automaticamente(self):
        for codigo in (429, 500, 503):
            sesion = Mock()
            respuesta = Respuesta(codigo)
            sesion.request.side_effect = [Respuesta(datos=tokens()), respuesta]
            with ClienteAPI(self.config, sesion) as cliente:
                with self.assertRaises(ErrorTemporalAPI):
                    cliente.solicitar('POST', '/trabajos/tomar', idempotencia='consulta-1')
            self.assertEqual(sesion.request.call_count, 2)
            self.assertTrue(respuesta.cerrada)

    def test_bloquea_redirecciones_y_origenes_distintos(self):
        for url in ('http://api.test', 'https://user:pass@api.test',
                    'https://api.test?token=x', 'https://api.test/#x'):
            with self.assertRaises(ValueError):
                ConfiguracionAPI(url, 'robot@example.test', 'secret',
                                 'https://api.example.test/login')
        for ruta in ('https://otro.test/trabajos', '//otro.test', '/../auth', '/x?token=y'):
            with self.assertRaises(ValueError):
                self.cliente.solicitar('GET', ruta)
        self.session.request.assert_not_called()
        self.preparar(Respuesta(datos=tokens()), Respuesta(302))
        with self.assertRaisesRegex(ErrorAPI, '302'):
            self.cliente.solicitar('GET', '/trabajos')

    def test_cerrar_elimina_tokens_y_no_permite_nuevas_peticiones(self):
        self.preparar(Respuesta(datos=tokens()), Respuesta())
        self.cliente.verificar()
        self.cliente.cerrar()
        self.assertIsNone(self.cliente._refresh_token)
        with self.assertRaises(ErrorAutenticacionAPI):
            self.cliente.verificar()

    def test_configuracion_inyectada_prevalece_sin_modificar_env(self):
        with tempfile.TemporaryDirectory() as carpeta:
            archivo = Path(carpeta)/'.env'
            archivo.write_text('BACKEND_API_URL=https://api.example.test/v1\n'
                               'BACKEND_LOGIN_URL=https://api.example.test/login\n'
                               'BACKEND_EMAIL=robot@example.test\nBACKEND_PASSWORD=archivo\n')
            with patch.dict(os.environ, {'BACKEND_PASSWORD': '  inyectado  '}, clear=True):
                with crear_cliente_api(archivo, session=self.session) as cliente:
                    self.assertEqual(cliente.config.password, '  inyectado  ')
            self.assertIn('BACKEND_PASSWORD=archivo', archivo.read_text())


class AdaptadorSimulado(BaseAdapter):
    """Comprueba serializacion real de requests sin sockets ni servicios externos."""
    def __init__(self):
        self.peticiones = []
        self.respuestas = iter([(200, tokens()), (401, {}),
                                (200, tokens('access-2', 'refresh-2')), (200, {})])

    def send(self, request, **kwargs):
        self.peticiones.append(request)
        codigo, datos = next(self.respuestas)
        respuesta = requests.Response()
        respuesta.status_code = codigo
        respuesta._content = json.dumps(datos).encode()
        respuesta._content_consumed = True
        respuesta.request = request
        return respuesta

    def close(self):
        pass


class TransporteRealTests(unittest.TestCase):
    def test_json_export_y_autorizacion_se_reconstruyen_tras_refresh(self):
        session = requests.Session()
        session.trust_env = False
        adaptador = AdaptadorSimulado()
        session.mount('https://api.example.test/', adaptador)
        config = ConfiguracionAPI('https://api.example.test/v1', 'robot@example.test',
                                  'secret', 'https://api.example.test/v1/auth/login')
        with ClienteAPI(config, session) as cliente:
            with cliente.solicitar('PUT', '/trabajos/1/resultado',
                                   datos={'export': 'ZXhjZWw='}, idempotencia='trabajo-1'):
                pass
        login, envio1, refresh, envio2 = adaptador.peticiones
        self.assertNotIn('Authorization', login.headers)
        self.assertNotIn('Authorization', refresh.headers)
        self.assertEqual(envio1.body, envio2.body)
        self.assertEqual(envio2.headers['Authorization'], 'Bearer access-2')
        self.assertEqual(envio2.headers['Content-Type'], 'application/json')


if __name__ == '__main__':
    unittest.main()
