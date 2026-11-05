"""Cliente del contrato RPA remoto. No ejecuta el navegador ni modifica su estado."""
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

try:
    import requests
except ModuleNotFoundError:
    requests = None

ERRORES_RED = (requests.RequestException,) if requests is not None else (OSError,)


MAX_ENTRADA = 20 * 1024 * 1024


class ErrorAPI(RuntimeError):
    """Fallo transitorio: se conserva el trabajo para reintentarlo."""


class ErrorAPIPermanente(RuntimeError):
    """Configuracion, autorizacion o contrato incorrectos: requiere intervencion."""


class EntradaInvalida(ValueError):
    pass


def validar_configuracion_backend(config):
    url = urlsplit(config.backend_api_url)
    if (url.scheme != 'https' or not url.netloc or url.username or url.password
            or url.query or url.fragment):
        raise ValueError('BACKEND_API_URL debe ser una URL HTTPS sin credenciales, query ni fragmento')
    if not config.backend_api_token or any(c in config.backend_api_token for c in '\r\n'):
        raise ValueError('Falta BACKEND_API_TOKEN o su formato es invalido')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', config.robot_id):
        raise ValueError('ROBOT_ID debe contener entre 1 y 128 letras ASCII, numeros, guiones o guiones bajos')
    if not math.isfinite(config.intervalo_consulta_segundos) or config.intervalo_consulta_segundos <= 0:
        raise ValueError('INTERVALO_CONSULTA_SEGUNDOS debe ser positivo y finito')


class ClienteBackend:
    def __init__(self, config, session=None):
        validar_configuracion_backend(config)
        if session is None and requests is None:
            raise RuntimeError('Falta requests: ejecute .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt')
        self.config = config
        self.session = session if session is not None else requests.Session()

    def cerrar(self):
        self.session.close()

    def _peticion(self, metodo, ruta, **kwargs):
        try:
            respuesta = self.session.request(
                metodo, self.config.backend_api_url + ruta,
                headers={'Authorization': 'Bearer ' + self.config.backend_api_token},
                timeout=(10, 60), allow_redirects=False, **kwargs)
        except ERRORES_RED:
            raise ErrorAPI('No se pudo completar la comunicacion con el backend') from None
        if respuesta.status_code == 429 or respuesta.status_code >= 500:
            codigo = respuesta.status_code
            respuesta.close()
            raise ErrorAPI(f'Backend temporalmente no disponible (HTTP {codigo})')
        if not 200 <= respuesta.status_code < 300:
            codigo = respuesta.status_code
            respuesta.close()
            raise ErrorAPIPermanente(f'Backend rechazo la operacion (HTTP {codigo}); revise URL, token y contrato API')
        return respuesta

    def tomar(self):
        with self._peticion('POST', '/solicitudes/tomar', json={'robot_id': self.config.robot_id}) as respuesta:
            if respuesta.status_code == 204:
                return None
            try:
                solicitud = respuesta.json()
            except ValueError:
                raise ErrorAPIPermanente('La asignacion no contiene un JSON valido') from None
            if (not isinstance(solicitud, dict)
                    or not isinstance(solicitud.get('id'), str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', solicitud['id'])
                    or not isinstance(solicitud.get('proceso'), str)
                    or not isinstance(solicitud.get('nombre_archivo'), str)):
                raise ErrorAPIPermanente('La asignacion necesita id, proceso y nombre_archivo validos')
            return {c: solicitud[c] for c in ('id', 'proceso', 'nombre_archivo')}

    def descargar(self, remoto_id, destino):
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_suffix(destino.suffix + '.part')
        try:
            with self._peticion('GET', f'/solicitudes/{remoto_id}/entrada', stream=True) as respuesta:
                total = 0
                with parcial.open('wb') as archivo:
                    for bloque in respuesta.iter_content(chunk_size=65536):
                        total += len(bloque)
                        if total > MAX_ENTRADA:
                            raise EntradaInvalida('El Excel de entrada supera 20 MiB')
                        archivo.write(bloque)
                if not total:
                    raise EntradaInvalida('El archivo de entrada esta vacio')
            parcial.replace(destino)
        except ERRORES_RED:
            raise ErrorAPI('Se interrumpio la descarga del Excel') from None
        finally:
            parcial.unlink(missing_ok=True)

    def enviar_resultado(self, remoto_id, resumen, reporte):
        datos = {'estado': resumen['estado'], 'resumen': json.dumps(resumen, ensure_ascii=False)}
        # files hace multipart incluso si no hay archivo por un fallo de generacion.
        campos = {nombre: (None, valor) for nombre, valor in datos.items()}
        if reporte:
            with Path(reporte).open('rb') as archivo:
                campos['archivo'] = (Path(reporte).name, archivo,
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                with self._peticion('PUT', f'/solicitudes/{remoto_id}/resultado', files=campos):
                    pass
        else:
            with self._peticion('PUT', f'/solicitudes/{remoto_id}/resultado', files=campos):
                pass
