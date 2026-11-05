"""Llamadas seriales a bibliotecas bloqueantes en un proceso descartable."""
from contextlib import contextmanager
import importlib
import multiprocessing
import os
import subprocess
import time


class CanalInterrumpido(TimeoutError):
    """El proceso fue descartado; no se puede reintentar sobre esta sesion."""


def _atender(conexion, modulo, directorio):
    try:
        if directorio is not None:
            os.chdir(directorio)
        biblioteca = importlib.import_module(modulo)
        while True:
            nombre, args, kwargs = conexion.recv()
            try:
                conexion.send((True, getattr(biblioteca, nombre)(*args, **kwargs)))
            except Exception as error:
                conexion.send((False, str(error)))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        conexion.close()


class ProcesoAislado:
    def __init__(self, modulo, timeout=25, directorio=None):
        self.modulo, self.timeout, self.directorio = modulo, timeout, directorio
        self.proceso = self.conexion = None
        self.interrumpido = False
        self.fin = None

    @contextmanager
    def plazo(self, segundos):
        anterior = self.fin
        self.fin = min(anterior, time.monotonic() + segundos) if anterior else time.monotonic() + segundos
        try:
            yield
        finally:
            self.fin = anterior

    def llamar(self, nombre, *args, **kwargs):
        if self.interrumpido:
            raise CanalInterrumpido('La sesion del motor esta interrumpida')
        fin = min(self.fin or float('inf'), time.monotonic() + self.timeout)
        if self.proceso is None:
            contexto = multiprocessing.get_context('spawn')
            self.conexion, hijo = contexto.Pipe()
            self.proceso = contexto.Process(target=_atender, args=(hijo, self.modulo, self.directorio), daemon=True)
            self.proceso.start()
            hijo.close()
        try:
            self.conexion.send((nombre, args, kwargs))
            if not self.conexion.poll(max(0, fin - time.monotonic())):
                raise TimeoutError()
            correcto, resultado = self.conexion.recv()
        except (TimeoutError, EOFError, BrokenPipeError, OSError):
            self.interrumpido = True
            self.cerrar()
            raise CanalInterrumpido('El motor no respondio; se descarto su proceso y la sesion') from None
        if not correcto:
            raise RuntimeError(resultado)
        return resultado

    def cerrar(self):
        if self.proceso is not None:
            if self.proceso.is_alive():
                if os.name == 'nt':
                    # Solo el arbol del proceso creado por esta instancia.
                    try:
                        subprocess.run(['taskkill', '/PID', str(self.proceso.pid), '/T', '/F'],
                                       capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if self.proceso.is_alive():
                    self.proceso.terminate()
                self.proceso.join(2)
            self.proceso.close()
            self.proceso = None
        if self.conexion is not None:
            self.conexion.close()
            self.conexion = None
        self.interrumpido = True

    def __getattr__(self, nombre):
        if nombre.startswith('_'):
            raise AttributeError(nombre)
        return lambda *args, **kwargs: self.llamar(nombre, *args, **kwargs)
