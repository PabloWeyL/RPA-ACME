"""Pruebas del framework reusable contra HTTP local del contrato actual."""
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from servidor_cola_actual import ServidorColaActual

prefijo = 'weyland' if importlib.util.find_spec('weyland') else 'integraciones.weyland'
api = importlib.import_module(prefijo + '.cola_actual')
core = importlib.import_module(prefijo + '.ejecutor_cola')
base = importlib.import_module(prefijo)
ResultadoBot, ErrorWeyland, ErrorRedWeyland = base.ResultadoBot, base.ErrorWeyland, base.ErrorRedWeyland


class ColaActualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.servidor = ServidorColaActual()
        self.addCleanup(self.servidor.cerrar)
        self.cliente = api.crear_cliente_cola({'BACKEND_API_URL': self.servidor.url,
            'BACKEND_EMAIL': 'bot@example.test', 'BACKEND_PASSWORD': 'password_ficticia',
            'BACKEND_BOT_API_KEY': 'clave_ficticia', 'BACKEND_PERMITIR_HTTP_LOCAL': 'true'})
        self.addCleanup(self.cliente.cerrar)
        self.runner = Mock(side_effect=self.negocio)

    def negocio(self, carpeta, entrada, parametros):
        salida = carpeta / 'precios.txt'
        salida.write_text('resultado ficticio', encoding='utf-8')
        return ResultadoBot((salida,))

    def ejecutar(self, *, requiere_input=None):
        return core.ejecutar_cola(self.cliente, 1, self.raiz, {'DEMO': core.ProcesoBot(self.runner, requiere_input)})

    def estado(self):
        return json.loads((self.raiz / 'coordinador.json').read_text(encoding='utf-8'))

    def test_flujo_real_del_backend_sin_input_y_con_zip(self):
        estado = self.ejecutar()
        self.assertEqual(estado['fase'], 'COMPLETADA')
        self.assertEqual(estado['ejecucion_id'], 123)
        self.runner.assert_called_once()
        self.assertIsNone(self.runner.call_args.args[1])
        self.assertTrue(self.servidor.reporte.startswith(b'PK'))
        siguientes = [r for r in self.servidor.registro if r[1].endswith('/siguiente')]
        self.assertEqual(set(json.loads(siguientes[0][3])), {'maquina_id', 'id_externo'})
        self.assertNotIn('Idempotency-Key', siguientes[0][2])
        self.assertFalse(any(r[1] == '/api/bot/ejecuciones/123' for r in self.servidor.registro))

    def test_204_no_ejecuta_no_lee_json_ni_reutiliza_ultima(self):
        self.servidor.estado = 'COMPLETADA'
        self.assertEqual(self.ejecutar()['fase'], 'SIN_TRABAJO')
        self.runner.assert_not_called()
        self.assertEqual(self.servidor.entregas, 0)
        self.assertEqual(self.servidor.fallos, 0)

    def test_siguiente_invocacion_no_repite_trabajo_terminado(self):
        self.ejecutar()
        self.assertEqual(self.ejecutar()['fase'], 'SIN_TRABAJO')
        self.runner.assert_called_once()
        self.assertEqual(self.servidor.entregas, 1)

    def test_input_sin_hash_servidor_se_descarga_y_se_verifica_localmente(self):
        self.servidor.entrada = b'input ficticio'
        estado = self.ejecutar(requiere_input=True)
        self.assertEqual(estado['fase'], 'COMPLETADA')
        self.assertEqual(self.runner.call_args.args[1].read_bytes(), b'input ficticio')
        self.assertEqual(estado['snapshot_input']['size_bytes'], len(b'input ficticio'))

    def test_input_obligatorio_ausente_notifica_error_mismo_id(self):
        self.assertEqual(self.ejecutar(requiere_input=True)['fase'], 'ERROR')
        self.runner.assert_not_called()
        self.assertEqual(self.servidor.fallos, 1)

    def test_sin_export_envia_comprobante_json_como_archivo(self):
        self.runner.side_effect = None
        self.runner.return_value = ResultadoBot((), detalles={'actualizados': 3}, permite_sin_export=True)
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.assertTrue(self.servidor.nombre_reporte.endswith('.json'))
        recibo = json.loads(self.servidor.reporte)
        self.assertFalse(recibo['tiene_export_negocio'])
        self.assertEqual(recibo['ejecucion_id'], 123)

    def test_proceso_incompatible_queda_bloqueado_sin_tomar_otro(self):
        self.servidor.codigo = 'OTRO_PROCESO'
        for _ in range(2):
            with self.assertRaises(ErrorWeyland): self.ejecutar()
        self.runner.assert_not_called()
        self.assertEqual(self.servidor.inicios, 1)
        self.assertEqual(self.estado()['fase'], 'ASIGNACION_INCOMPATIBLE')

    def test_negocio_falla_sin_enviar_mensajes_privados(self):
        self.runner.side_effect = RuntimeError('PASSWORD_SECRETA')
        self.assertEqual(self.ejecutar()['fase'], 'ERROR')
        post = [r for r in self.servidor.registro if r[1].endswith('/error')][0]
        self.assertNotIn(b'PASSWORD_SECRETA', post[3])
        self.assertEqual(self.servidor.entregas, 0)

    def test_ack_toma_perdido_concilia_sin_segundo_post(self):
        self.servidor.cortar_toma = True
        with self.assertRaises(ErrorRedWeyland): self.ejecutar()
        self.assertEqual(self.estado()['fase'], 'TOMA_INCIERTA')
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.runner.assert_called_once()
        self.assertEqual(self.servidor.inicios, 1)

    def test_toma_incierta_sin_lectura_no_reenvia(self):
        self.servidor.cortar_toma = True
        with self.assertRaises(ErrorRedWeyland): self.ejecutar()
        self.servidor.sin_lectura = True
        with self.assertRaises(ErrorWeyland): self.ejecutar()
        self.runner.assert_not_called()
        self.assertEqual(self.servidor.inicios, 1)

    def test_toma_con_correlacion_duplicada_requiere_revision(self):
        self.servidor.cortar_toma = True
        with self.assertRaises(ErrorRedWeyland): self.ejecutar()
        self.servidor.correlacion_duplicada = True
        with self.assertRaises(ErrorWeyland): self.ejecutar()
        self.assertEqual(self.servidor.inicios, 1)

    def test_ack_reporte_perdido_verifica_archivo_web_sin_repost(self):
        self.servidor.cortar_entrega = True
        with self.assertRaises(ErrorRedWeyland): self.ejecutar()
        self.assertEqual(self.estado()['fase'], 'ENTREGA_INCIERTA')
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.runner.assert_called_once()
        self.assertEqual(self.servidor.entregas, 1)

    def test_entrega_503_no_reintenta_a_ciegas(self):
        self.servidor.entrega_503 = True
        with self.assertRaises(ErrorWeyland): self.ejecutar()
        self.servidor.entrega_503 = False
        with self.assertRaisesRegex(ErrorWeyland, 'incierta'): self.ejecutar()
        self.runner.assert_called_once()
        self.assertEqual(self.servidor.entregas, 1)

    def test_reporte_web_diferente_no_confirma_exito(self):
        self.servidor.cortar_entrega = True
        with self.assertRaises(ErrorRedWeyland): self.ejecutar()
        self.servidor.reporte = b'otro archivo'
        with self.assertRaisesRegex(ErrorWeyland, 'no coincide'): self.ejecutar()
        self.assertEqual(self.estado()['fase'], 'ENTREGA_INCIERTA')

    def test_corte_de_descarga_no_ejecuta_parcial_y_retoma_mismo_id(self):
        self.servidor.entrada = b'input ficticio'
        self.servidor.cortar_input = True
        with self.assertRaises(ErrorRedWeyland): self.ejecutar()
        self.runner.assert_not_called()
        self.assertEqual(self.estado()['fase'], 'DESCARGANDO')
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.assertEqual(self.servidor.inicios, 1)

    def test_negocio_interrumpido_no_se_repite(self):
        self.runner.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.ejecutar()
        with self.assertRaisesRegex(ErrorWeyland, 'interrumpido'): self.ejecutar()
        self.runner.assert_called_once()

    def test_empaquetado_interrumpido_retoma_sin_repetir_negocio(self):
        with patch.object(core, 'empaquetar_resultado', side_effect=OSError('disco no disponible')):
            with self.assertRaises(OSError):
                self.ejecutar()
        self.assertEqual(self.estado()['fase'], 'EXPORT_PENDIENTE')
        self.assertEqual(self.servidor.entregas, 0)
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.runner.assert_called_once()
        self.assertEqual(self.servidor.inicios, 1)

    def test_export_modificado_tras_corte_no_se_envia(self):
        with patch.object(core, 'empaquetar_resultado', side_effect=OSError('disco no disponible')):
            with self.assertRaises(OSError):
                self.ejecutar()
        self.runner.call_args.args[0].joinpath('precios.txt').write_text('modificado', encoding='utf-8')
        with self.assertRaises(ErrorWeyland):
            self.ejecutar()
        self.runner.assert_called_once()
        self.assertEqual(self.servidor.entregas, 0)

    def test_parametros_con_secretos_no_se_persisten(self):
        self.servidor.parametros = {'password': 'NO_GUARDAR_ESTE_VALOR'}
        with self.assertRaises(ErrorWeyland): self.ejecutar()
        self.assertNotIn('NO_GUARDAR_ESTE_VALOR', (self.raiz / 'coordinador.json').read_text())

    def test_renueva_sesion_sin_repetir_toma(self):
        self.cliente.verificar()
        self.servidor.expirar_perfil = True
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.assertEqual(self.servidor.inicios, 1)
