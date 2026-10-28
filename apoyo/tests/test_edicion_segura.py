import sys
import tempfile
import unittest
from pathlib import Path

AGENTE = Path(__file__).resolve().parents[2] / "agente"
sys.path.insert(0, str(AGENTE))

from edicion_segura import aplicar_ediciones, es_archivo_test, resolver_fuente_segura


class EdicionSeguraTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporal.name) / "repo"
        (self.repo / "paquete").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        self.fuente = self.repo / "paquete" / "calculo.py"
        self.original = b"def sumar(a, b):\n    return a - b\n"
        self.fuente.write_bytes(self.original)
        (self.repo / "tests" / "test_calculo.py").write_text("def test_x():\n    pass\n")

    def tearDown(self):
        self.temporal.cleanup()

    def test_reconoce_tests(self):
        self.assertTrue(es_archivo_test("tests/test_calculo.py"))
        self.assertTrue(es_archivo_test("paquete/conftest.py"))
        self.assertFalse(es_archivo_test("paquete/calculo.py"))

    def test_rechaza_escape_test_y_symlink_externo(self):
        with self.assertRaises(ValueError):
            resolver_fuente_segura(self.repo, "../fuera.py")
        with self.assertRaises(ValueError):
            resolver_fuente_segura(self.repo, "tests/test_calculo.py")
        externo = Path(self.temporal.name) / "externo.py"
        externo.write_text("x = 1\n")
        (self.repo / "enlace.py").symlink_to(externo)
        with self.assertRaises(ValueError):
            resolver_fuente_segura(self.repo, "enlace.py")

    def test_aplica_y_revierte_byte_a_byte(self):
        cambio = aplicar_ediciones(self.repo, {"ediciones": [{
            "archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
            "contenido": "    return a + b",
        }]})
        self.assertIn("a + b", self.fuente.read_text())
        cambio.revertir()
        self.assertEqual(self.fuente.read_bytes(), self.original)

    def test_error_sintactico_revierte_automaticamente(self):
        with self.assertRaises(SyntaxError):
            aplicar_ediciones(self.repo, {"ediciones": [{
                "archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                "contenido": "    return (",
            }]})
        self.assertEqual(self.fuente.read_bytes(), self.original)

    def test_rechaza_rangos_solapados(self):
        with self.assertRaises(ValueError):
            aplicar_ediciones(self.repo, {"ediciones": [
                {"archivo": "paquete/calculo.py", "inicio": 1, "fin": 2,
                 "contenido": "def sumar(a, b):\n    return a + b"},
                {"archivo": "paquete/calculo.py", "inicio": 2, "fin": 2,
                 "contenido": "    return 0"},
            ]})

    def test_rechaza_edicion_sin_cambios(self):
        with self.assertRaisesRegex(ValueError, "no produce cambios"):
            aplicar_ediciones(self.repo, {"ediciones": [{
                "archivo": "paquete/calculo.py", "inicio": 1, "fin": 2,
                "contenido": "def sumar(a, b):\n    return a - b",
            }]})
        self.assertEqual(self.fuente.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
