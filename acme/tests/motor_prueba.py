"""Motor bloqueante para probar la cancelacion real entre procesos."""
from pathlib import Path
import time


def escribir_despues(ruta, segundos):
    time.sleep(segundos)
    Path(ruta).write_text('No debe ejecutarse despues del timeout')
