from contextlib import contextmanager
import os


@contextmanager
def bloqueo_proyecto(ruta):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a+b") as archivo:
        if archivo.tell() == 0:
            archivo.write(b"0")
            archivo.flush()
        archivo.seek(0)
        adquirido = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            adquirido = True
        except OSError as error:
            raise RuntimeError("Ya hay una consulta ACME ejecutandose en este proyecto") from error
        try:
            yield
        finally:
            if adquirido:
                archivo.seek(0)
                if os.name == "nt":
                    msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
