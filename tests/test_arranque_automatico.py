"""La puerta automatica bloquea el negocio ante configuracion, BD o tests fallidos."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from integraciones import arranque


class ArranqueAutomaticoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.valores = {'BACKEND_CONTRATO': 'cola_actual', 'BACKEND_AUTH_MODE': 'weyland',
            'BACKEND_MAQUINA_ID': '1', 'BACKEND_PROCESO_CODIGO': 'DEMO',
            'BACKEND_API_URL': 'http://127.0.0.1:8000/api', 'BACKEND_PERMITIR_HTTP_LOCAL': 'true',
            'BACKEND_TRABAJOS_DIR': str(self.raiz / 'trabajos')}
        self.datos = {'ejecutadas': 5, 'fallas': 0, 'errores': 0, 'omitidas': 0, 'aprobadas': True}
        self.tecnico = Mock(return_value={'logs': self.raiz / 'logs'})
        self.proceso = Mock(return_value=0)
        self.parche = patch.dict(os.environ, {'BOT_PRUEBAS_EN_CURSO': '0'})
        self.parche.start()
        self.addCleanup(self.parche.stop)

    def ejecutar(self):
        return arranque.ejecutar_automatico(self.raiz, self.valores, self.tecnico, self.proceso)

    def test_sin_parametros_pasa_por_arranque_completo(self):
        import main
        with patch('integraciones.arranque_web.ejecutar_sin_argumentos', return_value=0) as ejecutar:
            self.assertEqual(main.main([]), 0)
        ejecutar.assert_called_once_with()

    def test_cli_fallo_de_arranque_devuelve_uno(self):
        import main
        with patch('integraciones.arranque_web.ejecutar_sin_argumentos', side_effect=arranque.ErrorArranque('validacion fallida')):
            self.assertEqual(main.main([]), 1)

    def test_orden_validar_tests_backend(self):
        pasos = []
        self.tecnico.side_effect = lambda: pasos.append('config') or {'logs': self.raiz / 'logs'}
        self.proceso.side_effect = lambda: pasos.append('backend') or 0
        with patch.object(arranque, 'ejecutar_pruebas', side_effect=lambda *a: pasos.append('tests') or self.datos):
            self.assertEqual(self.ejecutar(), 0)
        self.assertEqual(pasos, ['config', 'tests', 'backend'])

    def test_vm_ausente_bloquea_antes_de_tests_o_backend(self):
        self.valores['BACKEND_MAQUINA_ID'] = ''
        with patch.object(arranque, 'ejecutar_pruebas') as pruebas, self.assertRaises(ValueError):
            self.ejecutar()
        pruebas.assert_not_called()
        self.proceso.assert_not_called()

    def test_modo_anterior_no_cae_a_local(self):
        self.valores['BACKEND_CONTRATO'] = 'por_id'
        with self.assertRaises(arranque.ErrorArranque): self.ejecutar()
        self.proceso.assert_not_called()

    def test_fallo_configuracion_no_consulta_backend(self):
        self.tecnico.side_effect = ValueError('Configuracion incompleta')
        with patch.object(arranque, 'ejecutar_pruebas') as pruebas, self.assertRaises(ValueError): self.ejecutar()
        pruebas.assert_not_called()
        self.proceso.assert_not_called()

    def test_fallo_suite_no_consulta_backend(self):
        with patch.object(arranque, 'ejecutar_pruebas', side_effect=arranque.ErrorArranque('tests fallidos')):
            with self.assertRaises(arranque.ErrorArranque): self.ejecutar()
        self.proceso.assert_not_called()

    def test_no_permite_recursion(self):
        with patch.dict(os.environ, {'BOT_PRUEBAS_EN_CURSO': '1'}), \
                patch.object(arranque, 'ejecutar_pruebas') as pruebas, self.assertRaises(arranque.ErrorArranque):
            self.ejecutar()
        pruebas.assert_not_called()
        self.proceso.assert_not_called()

    def test_base_corrupta_bloquea_antes_del_backend(self):
        base = self.raiz / 'trabajos/cola_actual/maquina_1/ejecuciones/123/datos'
        base.mkdir(parents=True)
        (base / 'operativa.db').write_bytes(b'no es una base SQLite')
        with patch.object(arranque, 'ejecutar_pruebas') as pruebas, self.assertRaises(sqlite3.DatabaseError): self.ejecutar()
        pruebas.assert_not_called()
        self.proceso.assert_not_called()

    def test_revision_sqlite_no_modifica_datos(self):
        ruta = self.raiz / 'base.db'
        with closing(sqlite3.connect(ruta)) as con:
            with con:
                con.execute('CREATE TABLE prueba (id INTEGER PRIMARY KEY)')
                con.execute('INSERT INTO prueba VALUES (1)')
        original = ruta.read_bytes()
        arranque.revisar_integridad(ruta)
        self.assertEqual(ruta.read_bytes(), original)

    def simular_suite(self, datos=None, codigo=0):
        def ejecutar(comando, **kwargs):
            if datos is not None:
                Path(comando[comando.index('--resultado') + 1]).write_text(json.dumps(datos), encoding='utf-8')
            return SimpleNamespace(returncode=codigo)
        return ejecutar

    def test_subproceso_no_hereda_secretos(self):
        with patch.dict(os.environ, {'BACKEND_PASSWORD': 'NO_HEREDAR', 'SECRET_SMTP_PASSWORD': 'NO_HEREDAR'}), \
                patch.object(arranque.subprocess, 'run', side_effect=self.simular_suite(self.datos)) as ejecutar:
            self.assertEqual(arranque.ejecutar_pruebas(self.raiz, self.valores, self.raiz / 'logs'), self.datos)
        entorno = ejecutar.call_args.kwargs['env']
        self.assertNotIn('BACKEND_PASSWORD', entorno)
        self.assertNotIn('SECRET_SMTP_PASSWORD', entorno)
        self.assertEqual(entorno['BOT_PRUEBAS_EN_CURSO'], '1')

    def test_suite_vacia_omitida_o_fallida_no_aprueba(self):
        for cambios in ({'ejecutadas': 0}, {'ejecutadas': True}, {'omitidas': 1}, {'errores': 1}, {'aprobadas': False}):
            with self.subTest(cambios=cambios), \
                    patch.object(arranque.subprocess, 'run', side_effect=self.simular_suite({**self.datos, **cambios})), \
                    self.assertRaises(arranque.ErrorArranque):
                arranque.ejecutar_pruebas(self.raiz, self.valores, self.raiz / 'logs')

    def test_sin_informe_o_proceso_fallido_no_aprueba(self):
        for datos, codigo in ((None, 0), (self.datos, 1)):
            with patch.object(arranque.subprocess, 'run', side_effect=self.simular_suite(datos, codigo)), \
                    self.assertRaises(arranque.ErrorArranque):
                arranque.ejecutar_pruebas(self.raiz, self.valores, self.raiz / 'logs')

    def test_timeout_de_suite_bloquea(self):
        with patch.object(arranque.subprocess, 'run', side_effect=subprocess.TimeoutExpired('tests', 1)), \
                self.assertRaises(arranque.ErrorArranque):
            arranque.ejecutar_pruebas(self.raiz, self.valores, self.raiz / 'logs')

    def test_timeout_excesivo_no_lanza_subproceso(self):
        self.valores['PRUEBAS_TIMEOUT_SEGUNDOS'] = '3601'
        with patch.object(arranque.subprocess, 'run') as ejecutar, self.assertRaises(arranque.ErrorArranque):
            arranque.ejecutar_pruebas(self.raiz, self.valores, self.raiz / 'logs')
        ejecutar.assert_not_called()
