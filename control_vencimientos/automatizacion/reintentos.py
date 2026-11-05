"""Reintentos tecnicos acotados; el llamador clasifica los fallos transitorios."""
import time


def reintentar(accion, config, logger, transitorio):
    for intento in range(1, config.max_reintentos + 1):
        try:
            return accion()
        except Exception as error:
            error.intentos_realizados = intento
            if not transitorio(error) or intento == config.max_reintentos:
                raise
            logger.warning('Intento %s/%s fallido; nuevo intento en %s segundos', intento,
                           config.max_reintentos, config.espera_reintento)
            time.sleep(config.espera_reintento)
