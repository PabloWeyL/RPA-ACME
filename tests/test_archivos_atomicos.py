"""Los reintentos solo afectan la sustitucion atomica del journal."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    from weyland.archivos import reemplazar_atomico, guardar_json
    import weyland.archivos as archivos
except ModuleNotFoundError:
    from integraciones.weyland.archivos import reemplazar_atomico, guardar_json
    import integraciones.weyland.archivos as archivos


class AtomicosTests(unittest.TestCase):
    def test_bloqueo_transitorio_reintenta_solo_replace(self):
        bloqueo = PermissionError('Archivo bloqueado de prueba')
        bloqueo.winerror = 32
        parcial = Mock()
        parcial.replace.side_effect = [bloqueo, None]
        with patch.object(archivos.time, 'sleep') as esperar:
            reemplazar_atomico(parcial, Path('estado.json'))
        self.assertEqual(parcial.replace.call_count, 2)
        esperar.assert_called_once_with(0.05)

    def test_bloqueo_persistente_se_detiene(self):
        bloqueo = PermissionError('Acceso denegado de prueba')
        bloqueo.winerror = 5
        parcial = Mock()
        parcial.replace.side_effect = bloqueo
        with patch.object(archivos.time, 'sleep'), self.assertRaises(PermissionError):
            reemplazar_atomico(parcial, Path('estado.json'))
        self.assertEqual(parcial.replace.call_count, 5)

    def test_no_reintenta_errores_ajenos_a_bloqueos_windows(self):
        parcial = Mock()
        parcial.replace.side_effect = FileNotFoundError('No existe')
        with patch.object(archivos.time, 'sleep') as esperar, self.assertRaises(FileNotFoundError):
            reemplazar_atomico(parcial, Path('estado.json'))
        parcial.replace.assert_called_once()
        esperar.assert_not_called()

    def test_guardar_json_sigue_siendo_atomico(self):
        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / 'estado.json'
            guardar_json(ruta, {'fase': 'INICIO'})
            guardar_json(ruta, {'fase': 'PREPARADO'})
            self.assertIn('PREPARADO', ruta.read_text())
            self.assertFalse(list(Path(temporal).glob('*.tmp')))
