"""El adaptador conserva comandos, aislamiento, parametros y artefactos del negocio."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from integraciones.weyland_worker import crear_runner, ejecutar_subproceso, artefactos


class AdaptadorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.carpeta = self.raiz / '123'
        self.carpeta.mkdir()

    def test_comandos_de_negocio_no_arbitrarios(self):
        with self.assertRaises(ValueError):
            crear_runner('acme', 'comando_no_permitido', self.raiz)

    def test_subproceso_aisla_base_entrada_y_salida(self):
        entrada = self.carpeta / 'datos/input/entrada.xlsx'
        with patch('integraciones.weyland_worker.subprocess.run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(ejecutar_subproceso('acme', 'consultar', self.raiz, self.carpeta, entrada), 0)
        variables = run.call_args.kwargs['env']
        self.assertEqual(variables['EXCEL_ENTRADA'], str(entrada))
        self.assertEqual(variables['DB_PATH'], str(self.carpeta / 'datos/historial.db'))
        self.assertEqual(variables['ID_EJECUCION'], '')
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_artefactos_no_incluyen_inputs_logs_ni_base(self):
        for nombre in ('input/a.xlsx', 'logs/a.txt', 'historial.db', 'reportes/a.csv', 'descargas/b.xlsx'):
            ruta = self.carpeta / 'datos' / nombre
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(b'prueba')
        self.assertEqual({p.name for p in artefactos(self.carpeta)}, {'a.csv', 'b.xlsx'})

    def test_acme_y_vencimientos_necesitan_input(self):
        for proyecto in ('acme', 'vencimientos'):
            with self.subTest(proyecto=proyecto), self.assertRaises(Exception):
                crear_runner(proyecto, 'procesar', self.raiz)(self.carpeta, None, {})

    def test_acme_mensual_conserva_modo(self):
        entrada = self.carpeta / 'datos/input/entrada.xlsx'
        def ejecutar(proyecto, comando, raiz, carpeta, archivo):
            self.assertEqual((proyecto, comando, archivo), ('acme', 'descargar-reportes', entrada))
            reporte = carpeta / 'datos/descargas/reporte.xlsx'
            reporte.parent.mkdir(parents=True)
            reporte.write_bytes(b'reporte')
            return 0
        resultado = crear_runner('acme', 'descargar-reportes', self.raiz, ejecutar)(self.carpeta, entrada, {})
        self.assertEqual(len(resultado.archivos), 1)
