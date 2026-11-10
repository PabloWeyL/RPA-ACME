"""Runner completo en subproceso, aislado del .env privado y del entorno operativo."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from integraciones.weyland.archivos import guardar_json


class InformePytest:
    def __init__(self):
        self.ids, self.fallas, self.errores, self.omitidas = set(), set(), set(), set()

    def pytest_runtest_logreport(self, report):
        self.ids.add(report.nodeid)
        if report.skipped or getattr(report, 'wasxfail', None):
            self.omitidas.add(report.nodeid)
        if report.failed:
            (self.fallas if report.when == 'call' else self.errores).add(report.nodeid)

    def pytest_collectreport(self, report):
        if report.failed:
            self.errores.add(report.nodeid)
        if report.skipped:
            self.omitidas.add(report.nodeid)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--resultado', type=Path, required=True)
    args = parser.parse_args(argv)
    raiz = Path(__file__).resolve().parents[1]
    import dotenv
    original_values, original_load = dotenv.dotenv_values, dotenv.load_dotenv
    def privado(archivo):
        return archivo is None or Path(archivo).resolve() == (raiz / '.env').resolve()
    def valores(archivo=None, *args, **kwargs):
        return {} if privado(archivo) else original_values(archivo, *args, **kwargs)
    def cargar(archivo=None, *args, **kwargs):
        return False if privado(archivo) else original_load(archivo, *args, **kwargs)
    # Quedan solo variables necesarias para Python/navegadores locales.
    entorno = {k: v for k, v in os.environ.items() if k.upper() in (
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA',
        'USERPROFILE', 'HOME', 'COMSPEC', 'PATHEXT', 'PLAYWRIGHT_BROWSERS_PATH', 'CHROME_PATH')}
    entorno.update(BOT_PRUEBAS_EN_CURSO='1', MODULO_CORREO='false', CORREO_HABILITADO='false',
                   MODULO_CORREO_INICIAL='false', BACKEND_CONTRATO='por_id')
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, entorno, clear=True))
        stack.enter_context(patch('dotenv.dotenv_values', side_effect=valores))
        stack.enter_context(patch('dotenv.load_dotenv', side_effect=cargar))
        if (raiz / 'pruebas').is_dir():
            import pytest
            informe = InformePytest()
            codigo = int(pytest.main([str(raiz / 'pruebas'), '-q', '-p', 'no:cacheprovider'], plugins=[informe]))
            datos = {'ejecutadas': len(informe.ids), 'fallas': len(informe.fallas),
                     'errores': len(informe.errores), 'omitidas': len(informe.omitidas)}
        else:
            suite = unittest.defaultTestLoader.discover(str(raiz / 'tests'))
            resultado = unittest.TextTestRunner(verbosity=2).run(suite)
            codigo = 0 if resultado.wasSuccessful() else 1
            datos = {'ejecutadas': resultado.testsRun, 'fallas': len(resultado.failures),
                'errores': len(resultado.errors),
                'omitidas': len(resultado.skipped) + len(resultado.expectedFailures) + len(resultado.unexpectedSuccesses)}
    datos['aprobadas'] = codigo == 0 and datos['ejecutadas'] > 0 and not any(datos[k] for k in ('fallas','errores','omitidas'))
    guardar_json(args.resultado, datos)
    return 0 if datos['aprobadas'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
