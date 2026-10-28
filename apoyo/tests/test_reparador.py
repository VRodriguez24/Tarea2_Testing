import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AGENTE = Path(__file__).resolve().parents[2] / "agente"
sys.path.insert(0, str(AGENTE))

from contratos import ResultadoProceso
from reparador import _contexto_archivo_test, _prompt, construir_contexto, reparar


class EjecucionFalsa:
    def __init__(self, fuente, respuestas, restante_segundos=120):
        self.fuente = fuente
        self.respuestas = iter(respuestas)
        self.prompts = []
        self.restante_segundos = restante_segundos

    def restante(self, reserva=0):
        return max(0, self.restante_segundos - reserva)

    def limitar_timeout(self, solicitado, reserva=0):
        return min(solicitado, self.restante(reserva))

    def generar_json(self, prompt, reserva=0):
        self.prompts.append(prompt)
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


class EjecucionRegresionTimeout(EjecucionFalsa):
    def ejecutar(self, comando, cwd, timeout, *, shell=False, reserva=0):
        if shell:
            return ResultadoProceso(-1, "[TIMEOUT]", True)
        if "return a - b" in self.fuente.read_text():
            return ResultadoProceso(1, "objetivo falla")
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

    def test_contexto_extrae_test_objetivo_aunque_este_lejos(self):
        ruta_test = self.repo / "tests" / "test_calculo.py"
        ruta_test.write_text(
            "def test_no_relacionado():\n    assert False\n\n"
            + "# relleno\n" * 300
            + "def test_sumar():\n    assert sumar(1, 2) == 3\n"
        )
        contexto = construir_contexto(self.repo, self.info, self.localizacion)
        self.assertIn("### TEST OBJETIVO tests/test_calculo.py:304-305", contexto)
        self.assertIn("assert sumar(1, 2) == 3", contexto)
        self.assertNotIn("assert False", contexto)

    def test_contexto_test_tiene_fallback_para_dato_sin_funcion(self):
        ruta = self.repo / "tests" / "dato.py"
        ruta.write_text("CASO = {'entrada': 1}\n")
        contexto = _contexto_archivo_test(ruta, "tests/dato.py", {"test_sumar"})
        self.assertIn("### TEST/DATO tests/dato.py", contexto)
        self.assertIn("CASO", contexto)

    def test_prompt_exige_parche_minimo_completo_y_autocontenido(self):
        prompt = _prompt(self.info, "contexto", "fallo", "")
        self.assertIn("rango mínimo necesario", prompt)
        self.assertIn("incluye completas las líneas", prompt)
        self.assertIn("Todo nombre nuevo debe estar definido o importado", prompt)
        self.assertIn("todas las aserciones y casos del TEST OBJETIVO", prompt)
        self.assertIn("línea exacta del", prompt)
        self.assertIn("compatibilidad con Python 3.8", prompt)

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

    def test_tres_parches_invalidos_dejan_original(self):
        respuestas = [
            {"ediciones": [{"archivo": "tests/test_calculo.py", "inicio": 1, "fin": 1,
                             "contenido": "# prohibido"}]},
            {"ediciones": [{"archivo": "../escape.py", "inicio": 1, "fin": 1,
                             "contenido": "x = 1"}]},
            {"ediciones": [{"archivo": "paquete/calculo.py", "inicio": 1, "fin": 2,
                             "contenido": "def sumar(a, b):\n    return a - b"}]},
        ]
        falsa = EjecucionFalsa(self.fuente, respuestas)
        with patch("reparador.obtener_ejecucion", return_value=falsa):
            resultado = reparar(self.bug, self.info, self.localizacion)
        self.assertFalse(resultado["resuelto"])
        self.assertIn("return a - b", self.fuente.read_text())

    def test_segundo_prompt_incluye_propuesta_invalida_y_original(self):
        respuestas = [
            {"analisis": "indentación incorrecta", "ediciones": [{
                "archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                "contenido": "    if True:\nreturn a + b",
            }]},
            {"analisis": "corregido", "ediciones": [{
                "archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                "contenido": "    return a + b + 0",
            }]},
        ]
        falsa = EjecucionFalsa(self.fuente, respuestas)
        with patch("reparador.obtener_ejecucion", return_value=falsa):
            resultado = reparar(self.bug, self.info, self.localizacion)
        self.assertTrue(resultado["resuelto"])
        segundo_prompt = falsa.prompts[1]
        self.assertIn("IndentationError", segundo_prompt)
        self.assertIn("PROPUESTA JSON ANTERIOR", segundo_prompt)
        self.assertIn("return a + b", segundo_prompt)
        self.assertIn("CÓDIGO ORIGINAL ALREDEDOR", segundo_prompt)
        self.assertIn("return a - b", segundo_prompt)

    def test_tercer_intento_se_usa_si_quedan_45_segundos(self):
        respuestas = [
            {"ediciones": [{
                "archivo": "tests/test_calculo.py", "inicio": 1, "fin": 1,
                "contenido": "# prohibido",
            }]},
            {"ediciones": [{
                "archivo": "../escape.py", "inicio": 1, "fin": 1,
                "contenido": "x = 1",
            }]},
            {"ediciones": [{
                "archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                "contenido": "    return a + b + 0",
            }]},
        ]
        falsa = EjecucionFalsa(self.fuente, respuestas, restante_segundos=45)
        with patch("reparador.obtener_ejecucion", return_value=falsa):
            resultado = reparar(self.bug, self.info, self.localizacion)
        self.assertTrue(resultado["resuelto"])
        self.assertEqual(len(falsa.prompts), 3)

    def test_tercer_intento_se_omite_si_no_quedan_45_segundos(self):
        respuestas = [
            {"ediciones": [{
                "archivo": "tests/test_calculo.py", "inicio": 1, "fin": 1,
                "contenido": "# prohibido",
            }]},
            {"ediciones": [{
                "archivo": "../escape.py", "inicio": 1, "fin": 1,
                "contenido": "x = 1",
            }]},
            {"ediciones": [{
                "archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                "contenido": "    return a + b + 0",
            }]},
        ]
        falsa = EjecucionFalsa(self.fuente, respuestas, restante_segundos=44.9)
        with patch("reparador.obtener_ejecucion", return_value=falsa):
            resultado = reparar(self.bug, self.info, self.localizacion)
        self.assertFalse(resultado["resuelto"])
        self.assertEqual(len(falsa.prompts), 2)

    def test_timeout_de_regresion_revierte_y_nunca_declara_resuelto(self):
        respuesta = {"ediciones": [{
            "archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
            "contenido": "    return a + b",
        }]}
        falsa = EjecucionRegresionTimeout(
            self.fuente, [respuesta, respuesta, respuesta], restante_segundos=120
        )
        with patch("reparador.obtener_ejecucion", return_value=falsa):
            resultado = reparar(self.bug, self.info, self.localizacion)
        self.assertFalse(resultado["resuelto"])
        self.assertFalse(resultado["regresion_verificada"])
        self.assertEqual(self.fuente.read_bytes(), b"def sumar(a, b):\n    return a - b\n")
        self.assertEqual(len(falsa.prompts), 3)
        self.assertIn("regresión agotó el tiempo", falsa.prompts[1])


if __name__ == "__main__":
    unittest.main()
