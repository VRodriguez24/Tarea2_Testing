import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AGENTE = Path(__file__).resolve().parents[2] / "agente"
sys.path.insert(0, str(AGENTE))

from contratos import ResultadoProceso
from reparador import construir_contexto, reparar


class EjecucionFalsa:
    def __init__(self, fuente, respuestas):
        self.fuente = fuente
        self.respuestas = iter(respuestas)

    def restante(self, reserva=0):
        return 120 - reserva

    def limitar_timeout(self, solicitado, reserva=0):
        return min(solicitado, self.restante(reserva))

    def generar_json(self, prompt, reserva=0):
        return next(self.respuestas), ""

    def ejecutar(self, comando, cwd, timeout, *, shell=False, reserva=0):
        codigo = self.fuente.read_text()
        if shell:
            if "return a + b + 0" in codigo:
                return ResultadoProceso(0, "regresión correcta")
            return ResultadoProceso(1, "falló una regresión")
        if "return a - b" in codigo:
            return ResultadoProceso(1, "AssertionError: 1 + 2 != -1")
        return ResultadoProceso(0, "objetivo correcto")


class ReparadorTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.bug = Path(self.temporal.name) / "bug-1"
        self.repo = self.bug / "repo"
        (self.repo / "paquete").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        (self.repo / "env" / "bin").mkdir(parents=True)
        self.fuente = self.repo / "paquete" / "calculo.py"
        self.fuente.write_text("def sumar(a, b):\n    return a - b\n")
        (self.repo / "tests" / "test_calculo.py").write_text(
            "from paquete.calculo import sumar\n\ndef test_sumar():\n    assert sumar(1, 2) == 3\n"
        )
        (self.bug / "run_test.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
        self.info = {
            "comandos_test": ["pytest tests/test_calculo.py::test_sumar"],
            "archivos_test": ["tests/test_calculo.py"],
            "modulos_test": ["tests/test_calculo.py"],
        }
        self.localizacion = {"regiones": [{
            "archivo": "paquete/calculo.py", "inicio": 1, "fin": 2, "razon": "traceback"
        }]}

    def tearDown(self):
        self.temporal.cleanup()

    def test_contexto_incluye_fuente_y_test(self):
        contexto = construir_contexto(self.repo, self.info, self.localizacion)
        self.assertIn("paquete/calculo.py:1-2", contexto)
        self.assertIn("assert sumar", contexto)

    def test_contexto_incluye_encabezado_para_editar_imports(self):
        self.fuente.write_text(
            "from decimal import Decimal\n\n\n"
            "def sumar(a, b):\n    return a - b\n"
        )
        localizacion = {"regiones": [{
            "archivo": "paquete/calculo.py", "inicio": 4, "fin": 5,
        }]}
        contexto = construir_contexto(self.repo, self.info, localizacion)
        self.assertIn("### ENCABEZADO paquete/calculo.py:1-5", contexto)
        self.assertIn("from decimal import Decimal", contexto)

    def test_regresion_revierte_y_segundo_intento_queda_aplicado(self):
        respuestas = [
            {"ediciones": [{"archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                             "contenido": "    return a + b"}]},
            {"ediciones": [{"archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                             "contenido": "    return a + b + 0"}]},
        ]
        falsa = EjecucionFalsa(self.fuente, respuestas)
        with patch("reparador.obtener_ejecucion", return_value=falsa):
            resultado = reparar(self.bug, self.info, self.localizacion)
        self.assertTrue(resultado["resuelto"])
        self.assertTrue(resultado["regresion_verificada"])
        self.assertIn("return a + b + 0", self.fuente.read_text())

    def test_dos_parches_invalidos_dejan_original(self):
        respuestas = [
            {"ediciones": [{"archivo": "tests/test_calculo.py", "inicio": 1, "fin": 1,
                             "contenido": "# prohibido"}]},
            {"ediciones": [{"archivo": "../escape.py", "inicio": 1, "fin": 1,
                             "contenido": "x = 1"}]},
        ]
        falsa = EjecucionFalsa(self.fuente, respuestas)
        with patch("reparador.obtener_ejecucion", return_value=falsa):
            resultado = reparar(self.bug, self.info, self.localizacion)
        self.assertFalse(resultado["resuelto"])
        self.assertIn("return a - b", self.fuente.read_text())


if __name__ == "__main__":
    unittest.main()
