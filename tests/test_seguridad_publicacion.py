"""La proteccion de publicacion inspecciona contenido, no solo patrones gitignore."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.verificar_publicacion import evaluar_archivo, secretos_locales


class SeguridadPublicacionTests(unittest.TestCase):
    def test_bloquea_archivos_privados_y_datos_fuera_de_su_carpeta(self):
        for ruta in ('.env', '.ENV', 'input/filas.py', 'tmp/generador.py', 'reportes/datos.json',
                     'datos.XLSX', 'cartera.csv', 'base.db-wal', 'credentials.json', 'imagen.png'):
            with self.subTest(ruta=ruta):
                self.assertTrue(evaluar_archivo(ruta, b'dato'))

    def test_permite_codigo_y_gitkeep_vacio(self):
        self.assertEqual(evaluar_archivo('integraciones/cliente.py', b'def conectar(): pass\n'), [])
        self.assertEqual(evaluar_archivo('input/.gitkeep', b''), [])
        self.assertTrue(evaluar_archivo('input/.gitkeep', b'dato sensible'))

    def test_plantilla_solo_permite_credenciales_vacias_o_referencias(self):
        self.assertEqual(evaluar_archivo('.env.example', b'BACKEND_PASSWORD=\nSECRET_BACKEND_PASSWORD=ref-vault\n'), [])
        self.assertTrue(evaluar_archivo('.env.local.example', b'BACKEND_PASSWORD=valor_ficticio_no_permitido\n'))
        self.assertTrue(evaluar_archivo('.env.example', b'BACKEND_EMAIL=bot@example.test\n'))

    def test_detecta_credencial_local_sin_exponerla_en_motivo(self):
        valor = 'PRIVADO_' + 'SOLO_PRUEBA_123'
        motivos = evaluar_archivo('config.py', valor.encode(), (valor,))
        self.assertTrue(motivos)
        self.assertNotIn(valor, ' '.join(motivos))

    def test_detecta_token_y_clave_privada(self):
        token = ('ghp_' + 'A' * 36).encode()
        clave = ('-----BEGIN ' + 'PRIVATE KEY-----').encode()
        self.assertTrue(evaluar_archivo('config.py', token))
        self.assertTrue(evaluar_archivo('config.py', clave))

    def test_bloquea_binarios_y_archivos_grandes(self):
        self.assertTrue(evaluar_archivo('archivo.bin', b'\xff\xfe'))
        self.assertTrue(evaluar_archivo('archivo.txt', b'x' * (1024 * 1024 + 1)))

    def test_usuario_del_runner_no_se_confunde_con_credencial(self):
        with TemporaryDirectory() as temporal, patch.dict('os.environ',
                {'USER': 'runner', 'USERNAME': 'runner', 'BACKEND_PASSWORD': 'valor_privado_ficticio'}, clear=True):
            secretos = secretos_locales(Path(temporal))
        self.assertNotIn('runner', secretos)
        self.assertIn('valor_privado_ficticio', secretos)
