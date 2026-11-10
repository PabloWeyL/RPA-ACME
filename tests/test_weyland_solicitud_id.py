from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


class SeleccionCliTests(unittest.TestCase):
    def setUp(self):
        parche = patch('config.weyland.leer_valores', return_value={'BACKEND_CONTRATO': 'por_id'})
        parche.start()
        self.addCleanup(parche.stop)

    def test_id_obligatorio_y_positivo(self):
        import main
        for argumentos in [['web-procesar'], ['web-procesar', '--id-solicitud', '0']]:
            with self.subTest(args=argumentos), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main.main(argumentos)

    def test_cli_conserva_id_y_modo_acme(self):
        import main
        config = Mock(modulo_scraping=True, root=Path('.'))
        with patch.object(main, 'cargar_configuracion', return_value=config), \
             patch('integraciones.weyland_worker.ejecutar_desde_config', return_value=0) as ejecutar:
            self.assertEqual(main.main(['web-procesar', '--id-solicitud', '12', '--proceso', 'descargar-reportes']), 0)
        self.assertEqual(ejecutar.call_args.kwargs['solicitud_id'], 12)
        self.assertEqual(ejecutar.call_args.args[2], 'descargar-reportes')
