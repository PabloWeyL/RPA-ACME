"""Contrato comun: pruebas sin datos de usuario, navegadores ni servicios productivos."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from zipfile import ZipFile

try:
    from weyland import ClienteWeyland, ConfiguracionWeyland, ErrorWeyland, ErrorHTTPWeyland, ErrorRedWeyland
    from weyland import ejecutar_solicitud, ResultadoBot
    from weyland.archivos import confinar
except ModuleNotFoundError:
    from integraciones.weyland import ClienteWeyland, ConfiguracionWeyland, ErrorWeyland, ErrorHTTPWeyland, ErrorRedWeyland
    from integraciones.weyland import ejecutar_solicitud, ResultadoBot
    from integraciones.weyland.archivos import confinar

PERFIL = {'usuario': {'correo': 'robot@example.test'}}


def detalle(identificador=123, estado='EN_COLA', con_input=False):
    return {'solicitud_id': identificador, 'ejecucion_id': identificador, 'estado': estado,
            'proceso': {'codigo': 'DEMO'}, 'requiere_input': con_input, 'parametros': {},
            'input': {'nombre': 'inventario.xlsx', 'size_bytes': 5,
                      'sha256': hashlib.sha256(b'input').hexdigest(),
                      'descarga': f'/api/bot/ejecuciones/{identificador}/input'} if con_input else None}


def respuesta(codigo=200, datos=None, contenido=b''):
    r = Mock(status_code=codigo, headers={})
    r.json.return_value = datos
    r.iter_content.return_value = [contenido]
    r.__enter__ = Mock(return_value=r)
    r.__exit__ = Mock(return_value=False)
    return r


def inicio():
    return [respuesta(datos={'csrf': 'primero'}), respuesta(datos=PERFIL),
            respuesta(datos={'csrf': 'rotado'}), respuesta(datos=PERFIL)]


def cliente(respuestas):
    sesion = Mock()
    sesion.request.side_effect = respuestas
    config = ConfiguracionWeyland('https://example.test/api', 'https://example.test/api/auth/login',
                                 'robot@example.test', 'contrasena_ficticia', 'clave_ficticia')
    return ClienteWeyland(config, sesion), sesion


class ClienteTests(unittest.TestCase):
    def test_login_siguiente_ids_csrf_y_idempotencia_sin_equipo(self):
        c, s = cliente(inicio() + [respuesta(datos=detalle(estado='EN_PROCESO'))])
        c.iniciar(123, 'DEMO', 'clave_inicio')
        llamada = s.request.call_args
        self.assertTrue(llamada.args[1].endswith('/bot/ejecuciones/siguiente'))
        self.assertEqual(llamada.kwargs['json'], {'solicitud_id': 123, 'ejecucion_id': 123})
        self.assertEqual(llamada.kwargs['headers']['X-CSRFToken'], 'rotado')
        self.assertEqual(llamada.kwargs['headers']['Idempotency-Key'], 'clave_inicio')
        self.assertEqual(llamada.kwargs['headers']['X-Api-Key'], 'clave_ficticia')
        self.assertFalse(llamada.kwargs['allow_redirects'])
        self.assertNotIn('contrasena_ficticia', repr(c.config))

    def test_backend_sin_get_por_id_no_muta(self):
        c, s = cliente(inicio() + [respuesta(404)])
        with self.assertRaisesRegex(ErrorWeyland, 'contrato por ID'):
            c.consultar(123)
        self.assertFalse(any(x.args[1].endswith('/siguiente') for x in s.request.call_args_list))

    def test_siguiente_rechaza_204_y_otro_id(self):
        for r in (respuesta(204), respuesta(datos=detalle(999, 'EN_PROCESO'))):
            c, _ = cliente(inicio() + [r])
            with self.assertRaises(ErrorWeyland):
                c.iniciar(123, 'DEMO', 'clave')

    def test_rechaza_solicitudes_invalidas(self):
        for cambios in ({'estado': 'COMPLETADA'}, {'ejecucion_id': 999}, {'solicitud_id': True},
                        {'proceso': None}, {'proceso': {'codigo': 'OTRO'}}, {'requiere_input': 'false'},
                        {'input': {}}, {'parametros': []}):
            with self.subTest(cambios=cambios), self.assertRaises(ErrorWeyland):
                ClienteWeyland.validar(dict(detalle(), **cambios), 123, 'DEMO', 'EN_COLA')

    def test_sesion_expirada_renueva_sin_cambiar_clave(self):
        respuestas = inicio() + [respuesta(401, {'error': {'codigo': 'SESION_EXPIRADA'}})]
        respuestas += inicio()[:3] + [respuesta(datos=detalle(estado='EN_PROCESO'))]
        c, s = cliente(respuestas)
        c.iniciar(123, 'DEMO', 'clave_persistida')
        posts = [x for x in s.request.call_args_list if x.args[1].endswith('/siguiente')]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].kwargs['headers']['Idempotency-Key'], posts[1].kwargs['headers']['Idempotency-Key'])

    def test_clave_invalida_no_reintenta_login(self):
        c, s = cliente(inicio() + [respuesta(401, {'error': {'codigo': 'API_KEY_INVALIDA'}})])
        with self.assertRaises(ErrorHTTPWeyland):
            c.iniciar(123, 'DEMO', 'clave')
        self.assertEqual(sum('/auth/login' in x.args[1] for x in s.request.call_args_list), 1)

    def test_csrf_renueva_una_vez(self):
        c, s = cliente(inicio() + [respuesta(403, {'error': {'codigo': 'CSRF_INVALIDO'}}),
            respuesta(datos={'csrf': 'nuevo'}), respuesta(datos=detalle(estado='EN_PROCESO'))])
        c.iniciar(123, 'DEMO', 'clave')
        self.assertEqual(s.request.call_args.kwargs['headers']['X-CSRFToken'], 'nuevo')

    def test_descarga_hash_tamano_y_limpieza_parcial(self):
        for contenido, correcto in ((b'input', True), (b'ERROR', False), (b'in', False), (b'demasiado', False)):
            c, _ = cliente(inicio() + [respuesta(contenido=contenido)])
            with tempfile.TemporaryDirectory() as temporal:
                destino = Path(temporal) / 'entrada.xlsx'
                if correcto:
                    c.descargar(123, destino, detalle(con_input=True)['input'])
                    self.assertEqual(destino.read_bytes(), b'input')
                else:
                    with self.assertRaises(ErrorWeyland):
                        c.descargar(123, destino, detalle(con_input=True)['input'])
                    self.assertFalse(destino.exists())
                self.assertFalse(list(Path(temporal).glob('*.part')))

    def test_no_acepta_ids_no_positivos_antes_de_conectar(self):
        for valor in (0, -1, True, '123'):
            c, s = cliente([])
            with self.assertRaises(ValueError):
                c.consultar(valor)
            s.request.assert_not_called()

    def test_post_sin_idempotencia_no_conecta(self):
        for clave in (None, '', 'clave con espacios'):
            c, s = cliente([])
            with self.assertRaises(ValueError):
                c.iniciar(123, 'DEMO', clave)
            s.request.assert_not_called()


class ClienteFalso:
    config = SimpleNamespace(url='https://example.test/api', correo='robot@example.test')
    validar = staticmethod(ClienteWeyland.validar)

    def __init__(self, con_input=False):
        self.con_input = con_input
        self.inicios, self.reportes, self.errores = [], [], []
        self.romper_inicio = self.romper_entrega = False

    def consultar(self, identificador):
        return detalle(identificador, con_input=self.con_input)

    def iniciar(self, identificador, codigo, clave):
        self.inicios.append((identificador, clave))
        if self.romper_inicio:
            self.romper_inicio = False
            raise ErrorRedWeyland('ACK perdido')
        return detalle(identificador, 'EN_PROCESO', self.con_input)

    def descargar(self, identificador, destino, info):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b'input')

    def entregar(self, identificador, archivo, metadatos, clave):
        self.reportes.append((identificador, Path(archivo).read_bytes(), deepcopy(metadatos), clave))
        if self.romper_entrega:
            self.romper_entrega = False
            raise ErrorRedWeyland('ACK perdido')

    def fallar(self, identificador, datos, clave):
        self.errores.append((identificador, datos, clave))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.cliente = ClienteFalso()
        self.llamadas = 0

    def runner(self, carpeta, entrada, parametros):
        self.llamadas += 1
        salida = carpeta / 'datos' / 'reportes' / 'resultado.txt'
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text('resultado', encoding='utf-8')
        return ResultadoBot((salida,))

    def ejecutar(self, runner=None, **kwargs):
        return ejecutar_solicitud(self.cliente, 123, 'DEMO', self.raiz, runner or self.runner, **kwargs)

    def test_flujo_sin_input_exporta_y_no_repite(self):
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.assertEqual(self.llamadas, 1)
        with ZipFile(io.BytesIO(self.cliente.reportes[0][1])) as archivo:
            datos = json.loads(archivo.read('reportes/ejecucion.json'))
            self.assertEqual(datos['solicitud_id'], datos['ejecucion_id'])
            self.assertEqual(datos['ejecucion_id'], 123)

    def test_flujo_con_input(self):
        self.cliente.con_input = True
        self.assertEqual(self.ejecutar()['fase'], 'COMPLETADA')
        self.assertEqual((self.raiz / '123/datos/input/entrada.xlsx').read_bytes(), b'input')
        with ZipFile(io.BytesIO(self.cliente.reportes[0][1])) as archivo:
            self.assertFalse(any('/input/' in n for n in archivo.namelist()))

    def test_inicio_incierto_repite_misma_clave_sin_otro_id(self):
        self.cliente.romper_inicio = True
        with self.assertRaises(ErrorRedWeyland):
            self.ejecutar()
        self.ejecutar()
        self.assertEqual(self.cliente.inicios[0], self.cliente.inicios[1])
        self.assertEqual(self.llamadas, 1)

    def test_entrega_incierta_repite_bytes_y_clave_sin_negocio(self):
        self.cliente.romper_entrega = True
        with self.assertRaises(ErrorRedWeyland):
            self.ejecutar()
        self.ejecutar()
        self.assertEqual(self.cliente.reportes[0], self.cliente.reportes[1])
        self.assertEqual(self.llamadas, 1)

    def test_interrupcion_durante_negocio_no_repite(self):
        def interrumpir(*args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.ejecutar(interrumpir)
        with self.assertRaisesRegex(ErrorWeyland, 'interrumpida'):
            self.ejecutar()

    def test_error_notifica_mismo_id_sin_traza_privada(self):
        def fallar(*args):
            raise ValueError('password secreto')
        self.assertEqual(self.ejecutar(fallar)['fase'], 'ERROR')
        self.assertEqual(self.cliente.errores[0][0], 123)
        self.assertNotIn('secreto', json.dumps(self.cliente.errores))
        self.assertFalse(self.cliente.reportes)

    def test_modalidad_no_admitida_no_comienza(self):
        with self.assertRaises(ErrorWeyland):
            self.ejecutar(requiere_input=True)
        self.assertFalse(self.cliente.inicios)

    def test_input_modificado_no_ejecuta_negocio(self):
        self.cliente.con_input = True
        def alterar(identificador, destino, info):
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(b'alterado')
        self.cliente.descargar = alterar
        self.assertEqual(self.ejecutar()['fase'], 'ERROR')
        self.assertEqual(self.llamadas, 0)

    def test_reporte_modificado_no_se_reenvia(self):
        self.cliente.romper_entrega = True
        with self.assertRaises(ErrorRedWeyland):
            self.ejecutar()
        estado = json.loads((self.raiz / '123/estado.json').read_text())
        (self.raiz / '123' / estado['reporte']).write_bytes(b'alterado')
        with self.assertRaisesRegex(ErrorWeyland, 'reporte cambio'):
            self.ejecutar()

    def test_export_no_puede_leer_fuera_del_trabajo(self):
        fuera = self.raiz / 'privado.txt'
        fuera.write_text('dato privado')
        self.assertEqual(self.ejecutar(lambda *args: ResultadoBot((fuera,)))['fase'], 'ERROR')
        self.assertFalse(self.cliente.reportes)
        with self.assertRaises(ErrorWeyland):
            confinar(self.raiz, '../privado.txt')

    def test_journal_antiguo_pendiente_no_migra_silenciosamente(self):
        carpeta = self.raiz / 'flujo_anterior'
        carpeta.mkdir()
        (carpeta / 'activo.json').write_text('{"fase":"EJECUTANDO"}')
        with self.assertRaisesRegex(ErrorWeyland, 'anterior pendiente'):
            self.ejecutar()
        self.assertFalse(self.cliente.inicios)

    def test_journal_alterado_no_se_sobrescribe(self):
        self.ejecutar()
        ruta = self.raiz / '123/estado.json'
        datos = json.loads(ruta.read_text())
        datos['claves']['inicio'] = '../otro'
        contenido = json.dumps(datos)
        ruta.write_text(contenido)
        with self.assertRaisesRegex(ErrorWeyland, 'Journal incompatible'):
            self.ejecutar()
        self.assertEqual(ruta.read_text(), contenido)


if __name__ == '__main__':
    unittest.main()
