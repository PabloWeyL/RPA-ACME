from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from config.configuracion import Configuracion
from integraciones.backend_api import ClienteBackend, ErrorAPIPermanente, validar_configuracion_backend
from test_api_http import Respuesta, tokens, AdaptadorSimulado


def configuracion(**cambios):
    datos = dict(backend_api_url='https://api.example.test/v1',
                 backend_login_url='https://api.example.test/v1/auth/login',
                 backend_email='robot@example.test', backend_password='password-ficticio',
                 robot_id='acme-01', backend_auth_mode='login')
    datos.update(cambios)
    return Configuracion(**datos)


class BackendLoginTests(unittest.TestCase):
    def test_worker_toma_solicitud_despues_de_login(self):
        sesion = Mock()
        sesion.request.side_effect = [Respuesta(datos=tokens()), Respuesta(204)]
        cliente = ClienteBackend(configuracion(), sesion)
        self.addCleanup(cliente.cerrar)
        self.assertIsNone(cliente.tomar())
        login, trabajo = sesion.request.call_args_list
        self.assertEqual(login.kwargs['json'], {
            'correo': 'robot@example.test', 'password': 'password-ficticio'})
        self.assertEqual(trabajo.kwargs['headers']['Authorization'], 'Bearer access-1')
        self.assertTrue(trabajo.args[1].endswith('/solicitudes/tomar'))

    def test_multipart_real_se_reconstruye_completo_despues_de_401(self):
        sesion = requests.Session()
        sesion.trust_env = False
        adaptador = AdaptadorSimulado()
        sesion.mount('https://api.example.test/', adaptador)
        cliente = ClienteBackend(configuracion(), sesion)
        self.addCleanup(cliente.cerrar)
        with tempfile.TemporaryDirectory() as temporal:
            reporte = Path(temporal)/'resultado.xlsx'
            reporte.write_bytes(b'export-de-prueba-completo')
            cliente.enviar_resultado('trabajo-1', {'estado': 'completado'}, reporte)
        login, envio1, refresh, envio2 = adaptador.peticiones
        self.assertNotIn('Authorization', login.headers)
        self.assertNotIn('Authorization', refresh.headers)
        for envio in (envio1, envio2):
            self.assertIn(b'export-de-prueba-completo', envio.body)
            self.assertIn(b'completado', envio.body)
        self.assertEqual(envio2.headers['Authorization'], 'Bearer access-2')

    def test_refresh_incierto_detiene_worker_sin_bucle(self):
        sesion = Mock()
        sesion.request.side_effect = [Respuesta(datos=tokens()), Respuesta(401),
                                     requests.Timeout('password-ficticio')]
        cliente = ClienteBackend(configuracion(), sesion)
        self.addCleanup(cliente.cerrar)
        with self.assertRaises(ErrorAPIPermanente) as error:
            cliente.tomar()
        self.assertNotIn('password-ficticio', str(error.exception))
        self.assertEqual(sesion.request.call_count, 3)

    def test_token_antiguo_no_es_fallback_si_faltan_credenciales_de_login(self):
        with self.assertRaises(ValueError):
            validar_configuracion_backend(configuracion(backend_email='', backend_api_token='antiguo'))
        with self.assertRaises(ValueError):
            validar_configuracion_backend(configuracion(backend_auth_mode='desconocido'))

    def test_verificacion_cli_no_crea_historial_ni_ejecuta_procesos(self):
        import main
        with patch('main.cargar_configuracion', return_value=configuracion()), \
                patch('config.api.crear_cliente_api') as crear, \
                patch('main.BaseDatos') as base, patch('main.consultar') as proceso:
            self.assertEqual(main.main(['api-verificar']), 0)
            crear.return_value.__enter__.return_value.verificar.assert_called_once()
        base.assert_not_called()
        proceso.assert_not_called()

    def test_configuracion_oculta_correo_y_password_del_backend(self):
        config = configuracion()
        for secreto in (config.backend_email, config.backend_password):
            self.assertNotIn(secreto, repr(config))
            self.assertEqual(config.ocultar(secreto), '[OCULTO]')
