"""Servidor de contrato local: reproduce la API vigente, sin idempotencia."""
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
from urllib.parse import parse_qs, urlsplit


class ServidorColaActual(ThreadingHTTPServer):
    def __init__(self, entrada=None, parametros=None, codigo='DEMO'):
        self.entrada = entrada
        self.parametros = parametros or {}
        self.codigo = codigo
        self.estado = 'EN_COLA'
        self.id_externo = ''
        self.reporte = None
        self.nombre_reporte = ''
        self.registro = []
        self.inicios = self.entregas = self.fallos = 0
        self.cortar_toma = self.cortar_entrega = self.cortar_input = False
        self.entrega_503 = self.sin_lectura = self.correlacion_duplicada = self.expirar_perfil = False
        super().__init__(('127.0.0.1', 0), Handler)
        self.hilo = threading.Thread(target=self.serve_forever, daemon=True)
        self.hilo.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_port}/api'

    def cerrar(self):
        self.shutdown()
        self.server_close()
        self.hilo.join()

    def asignacion(self):
        return {'ejecucion_id': 123, 'id_externo': self.id_externo, 'reproceso_de': None,
                'empresa': {'id': 1, 'nombre': 'EMPRESA_FICTICIA', 'rut': 'RUT_FICTICIO'},
                'proceso': {'codigo': self.codigo, 'nombre': 'Proceso ficticio', 'version': '1'},
                'maquina': {'id': 1, 'nombre': 'MVTESTING', 'hostname': 'ficticio'},
                'parametros': self.parametros, 'configuracion': None,
                'input': {'nombre': 'datos.xlsx', 'url': None, 'descarga': '/api/bot/ejecuciones/123/input'}
                         if self.entrada is not None else None}

    def detalle(self):
        datos = self.asignacion()
        archivos = []
        if self.entrada is not None:
            archivos.append({'id': 6, 'tipo': 'INPUT', 'nombre': 'datos.xlsx', 'tamano_bytes': len(self.entrada)})
        if self.reporte is not None:
            archivos.append({'id': 7, 'tipo': 'REPORTE', 'nombre': self.nombre_reporte,
                             'tamano_bytes': len(self.reporte)})
        return {'id': 123, 'id_externo': self.id_externo, 'estado': self.estado,
                'proceso': datos['proceso'], 'maquina': datos['maquina'], 'empresa': datos['empresa'],
                'parametros': self.parametros, 'archivos': archivos}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def responder(self, codigo, datos):
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json')
        if self.path.endswith('/csrf'):
            self.send_header('Set-Cookie', 'wa_csrf=csrf_ficticio; Path=/')
        if self.path.endswith('/login') and codigo == 200:
            self.send_header('Set-Cookie', 'wa_sesion=sesion_ficticia; Path=/')
        contenido = json.dumps(datos).encode() if datos is not None else b''
        self.send_header('Content-Length', str(len(contenido)))
        self.end_headers()
        self.wfile.write(contenido)

    def cortar(self):
        self.connection.shutdown(socket.SHUT_RDWR)
        self.close_connection = True

    def autenticar(self, ruta):
        if ruta.startswith('/api/bot/'):
            if self.headers.get('X-Api-Key') != 'clave_ficticia':
                self.responder(401, {'error': {'codigo': 'NO_AUTENTICADO'}})
                return False
        elif ruta not in ('/api/auth/csrf', '/api/auth/login'):
            if 'wa_sesion=sesion_ficticia' not in self.headers.get('Cookie', ''):
                self.responder(401, {'error': {'codigo': 'NO_AUTENTICADO'}})
                return False
        return True

    def archivo(self, contenido):
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(contenido)))
        self.end_headers()
        self.wfile.write(contenido)

    def do_GET(self):
        s = self.server
        ruta = urlsplit(self.path).path
        s.registro.append(('GET', ruta, dict(self.headers), b''))
        if not self.autenticar(ruta): return
        if ruta == '/api/auth/csrf': return self.responder(200, {'csrf': 'csrf_ficticio'})
        if ruta == '/api/auth/perfil':
            if s.expirar_perfil:
                s.expirar_perfil = False
                return self.responder(401, {'error': {'codigo': 'NO_AUTENTICADO'}})
            return self.responder(200, {'usuario': {'correo': 'bot@example.test'}})
        if ruta.startswith('/api/ejecuciones') and s.sin_lectura:
            return self.responder(403, {'error': {'codigo': 'SIN_PERMISOS'}})
        if ruta == '/api/ejecuciones':
            if parse_qs(urlsplit(self.path).query).get('maquina_id') != ['1']:
                return self.responder(400, {})
            filas = [s.detalle()]
            if s.correlacion_duplicada: filas.append(dict(s.detalle(), id=124))
            return self.responder(200, {'count': len(filas), 'results': filas, 'next': None, 'previous': None})
        if ruta == '/api/ejecuciones/123': return self.responder(200, s.detalle())
        if ruta == '/api/ejecuciones/123/archivos/7' and s.reporte is not None:
            return self.archivo(s.reporte)
        if ruta == '/api/bot/ejecuciones/123/input' and s.entrada is not None and s.estado == 'EN_PROCESO':
            if s.cortar_input:
                s.cortar_input = False
                self.send_response(200)
                self.send_header('Content-Length', str(len(s.entrada)))
                self.end_headers()
                self.wfile.write(s.entrada[:1])
                self.wfile.flush()
                return self.cortar()
            return self.archivo(s.entrada)
        self.responder(404, {'error': {'codigo': 'NO_ENCONTRADO'}})

    def do_POST(self):
        s = self.server
        ruta = urlsplit(self.path).path
        cuerpo = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        s.registro.append(('POST', ruta, dict(self.headers), cuerpo))
        if not self.autenticar(ruta): return
        if ruta == '/api/auth/login':
            datos = json.loads(cuerpo)
            if (datos != {'correo': 'bot@example.test', 'password': 'password_ficticia'}
                    or self.headers.get('X-CSRFToken') != 'csrf_ficticio'):
                return self.responder(401, {'error': {'codigo': 'NO_AUTENTICADO'}})
            return self.responder(200, {'usuario': {'correo': 'bot@example.test'}})
        if ruta == '/api/bot/ejecuciones/siguiente':
            s.inicios += 1
            datos = json.loads(cuerpo)
            if set(datos) != {'maquina_id', 'id_externo'} or datos['maquina_id'] != 1:
                return self.responder(400, {'error': {'codigo': 'SOLICITUD_INVALIDA'}})
            if s.estado != 'EN_COLA': return self.responder(204, None)
            s.estado, s.id_externo = 'EN_PROCESO', datos['id_externo']
            if s.cortar_toma:
                s.cortar_toma = False
                return self.cortar()
            return self.responder(200, s.asignacion())
        if ruta == '/api/bot/ejecuciones/123/reporte':
            s.entregas += 1
            if s.estado != 'EN_PROCESO': return self.responder(409, {'error': {'codigo': 'CONFLICTO'}})
            if s.entrega_503: return self.responder(503, {'error': {'codigo': 'TEMPORAL'}})
            mensaje = BytesParser(policy=policy.default).parsebytes(
                b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + cuerpo)
            partes = {p.get_param('name', header='content-disposition'): p for p in mensaje.iter_parts()}
            if set(partes) != {'archivo'}:
                return self.responder(400, {'error': {'codigo': 'SOLICITUD_INVALIDA'}})
            s.reporte, s.nombre_reporte = partes['archivo'].get_payload(decode=True), partes['archivo'].get_filename()
            s.estado = 'COMPLETADA'
            if s.cortar_entrega:
                s.cortar_entrega = False
                return self.cortar()
            return self.responder(200, {'ejecucion_id': 123, 'estado': 'COMPLETADA'})
        if ruta == '/api/bot/ejecuciones/123/error':
            s.fallos += 1
            datos = json.loads(cuerpo)
            if set(datos) != {'mensaje'} or not datos['mensaje']:
                return self.responder(400, {})
            if s.estado != 'EN_PROCESO': return self.responder(409, {})
            s.estado = 'ERROR'
            return self.responder(200, {'ejecucion_id': 123, 'estado': 'ERROR'})
        self.responder(404, {})
