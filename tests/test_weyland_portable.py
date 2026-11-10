"""Portabilidad real fuera del repositorio y configuracion sin entorno implicito."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

try:
    from weyland import crear_cliente, ClienteWeyland as ClienteCompatible
except ModuleNotFoundError:
    from integraciones.weyland import crear_cliente, ClienteWeyland as ClienteCompatible


VALORES = {'BACKEND_API_URL': 'https://weyland.example.test/api',
           'BACKEND_TIMEOUT_CONEXION': '7', 'BACKEND_TIMEOUT_LECTURA': '90'}
SECRETOS = {'BACKEND_EMAIL': 'bot@example.test', 'BACKEND_PASSWORD': ' test ',
            'BACKEND_BOT_API_KEY': 'test-key'}


class PortabilidadTests(unittest.TestCase):
    def test_construir_no_conecta_ni_exige_registro(self):
        sesion = Mock()
        with crear_cliente(VALORES, SECRETOS, session=sesion) as cliente:
            self.assertIsInstance(cliente, ClienteCompatible)
            self.assertEqual(cliente.config.login_url, VALORES['BACKEND_API_URL']+'/auth/login')
            self.assertEqual(cliente.config.password, ' test ')
            sesion.request.assert_not_called()
        sesion.cookies.clear.assert_called_once()
        sesion.close.assert_called_once()

    def test_configuracion_no_toma_credenciales_del_entorno(self):
        with patch.dict(os.environ, {**SECRETOS, 'BACKEND_API_URL': 'http://mal-configurado'}):
            with self.assertRaisesRegex(ValueError, 'BACKEND_EMAIL'):
                crear_cliente(VALORES, credenciales={})

    def test_configuraciones_independientes_en_dos_maquinas(self):
        primero = crear_cliente(VALORES, SECRETOS, session=Mock())
        segundo = crear_cliente({**VALORES, 'BACKEND_API_URL': 'https://otra.example.test/api'},
                               {**SECRETOS, 'BACKEND_EMAIL': 'otro@example.test'}, session=Mock())
        self.addCleanup(primero.cerrar)
        self.addCleanup(segundo.cerrar)
        self.assertNotEqual(primero.config.url, segundo.config.url)
        self.assertEqual(primero.config.correo, 'bot@example.test')

    def test_timeout_configurado_llega_al_transporte(self):
        sesion = Mock()
        with crear_cliente(VALORES, SECRETOS, session=sesion) as cliente:
            with self.assertRaisesRegex(RuntimeError, 'fin de prueba'):
                # Detener en la primera respuesta simulada; basta comprobar la peticion.
                sesion.request.side_effect = RuntimeError('fin de prueba')
                cliente.verificar()
        self.assertEqual(sesion.request.call_args.kwargs['timeout'], (7, 90))

    def test_timeout_invalido_rechazado_antes_de_conectar(self):
        for valor in ['nan', 'inf', '0', '-2', 'invalido', None]:
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                crear_cliente({**VALORES, 'BACKEND_TIMEOUT_LECTURA': valor}, SECRETOS)

    def test_login_explicito_conservado(self):
        with crear_cliente({**VALORES, 'BACKEND_LOGIN_URL': 'https://weyland.example.test/otro/login'},
                           SECRETOS, session=Mock()) as cliente:
            self.assertTrue(cliente.config.login_url.endswith('/otro/login'))

    def test_paquete_copiado_funciona_sin_bots_dotenv_azure_ni_ids(self):
        origen = Path(__file__).resolve().parents[1]/'weyland'
        if not origen.is_dir():
            origen = Path(__file__).resolve().parents[1]/'integraciones'/'weyland'
        with TemporaryDirectory() as temporal:
            destino = Path(temporal)
            shutil.copytree(origen, destino/'weyland', ignore=shutil.ignore_patterns('__pycache__'))
            (destino/'.env').write_text('BACKEND_API_URL=invalid\nBACKEND_PASSWORD=incorrect\n')
            codigo = '''
import sys
from unittest.mock import Mock
sys.path.insert(0, sys.argv[1])
from weyland import crear_cliente
valores = {'BACKEND_API_URL': 'https://local.example.test/api'}
secretos = {'BACKEND_EMAIL': 'bot@example.test', 'BACKEND_PASSWORD': 'test'}
with crear_cliente(valores, secretos, session=Mock()) as cliente:
    assert cliente.config.url == valores['BACKEND_API_URL']
    assert cliente.config.login_url.endswith('/api/auth/login')
assert not any(nombre.split('.')[0] in ('dotenv', 'azure', 'config', 'integraciones') for nombre in sys.modules)
print('portable OK')
'''
            resultado = subprocess.run([sys.executable, '-I', '-B', '-c', codigo, str(destino)],
                cwd=destino, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(resultado.returncode, 0, resultado.stderr)
            self.assertEqual(resultado.stdout.strip(), 'portable OK')
