import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

AGENTE = Path(__file__).resolve().parents[2] / "agente"
sys.path.insert(0, str(AGENTE))

from contratos import Metricas
from infraestructura import EjecucionAgente, recortar, sanitizar_error


class InfraestructuraTests(unittest.TestCase):
    def test_presupuesto_disminuye_y_respeta_reserva(self):
        ejecucion = EjecucionAgente(Metricas(), limite_segundos=2)
        antes = ejecucion.restante()
        time.sleep(0.01)
        self.assertLess(ejecucion.restante(), antes)
        self.assertLessEqual(ejecucion.limitar_timeout(10, reserva=1), 1)

    def test_timeout_mata_proceso(self):
        ejecucion = EjecucionAgente(Metricas(), limite_segundos=2)
        resultado = ejecucion.ejecutar(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            Path.cwd(),
            timeout=0.1,
        )
        self.assertTrue(resultado.agoto_tiempo)
        self.assertFalse(resultado.exitoso)

    def test_error_permanente_de_api_no_reintenta_con_esperas(self):
        anterior = os.environ.pop("GEMINI_API_KEY", None)
        try:
            ejecucion = EjecucionAgente(Metricas(), limite_segundos=5)
            inicio = time.perf_counter()
            datos, error = ejecucion.generar_json("prompt", reserva=1)
            self.assertIsNone(datos)
            self.assertIn("GEMINI_API_KEY", error)
            self.assertLess(time.perf_counter() - inicio, 1)
            self.assertEqual(ejecucion.metricas.llamadas, 0)
        finally:
            if anterior is not None:
                os.environ["GEMINI_API_KEY"] = anterior

    def test_timeout_de_api_es_error_temporal(self):
        self.assertTrue(
            EjecucionAgente._es_error_temporal(
                RuntimeError("504 DEADLINE_EXCEEDED")
            )
        )

    def test_error_oculta_api_key(self):
        anterior = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "clave-super-secreta"
        try:
            limpio = sanitizar_error(
                "falló https://api.test?key=clave-super-secreta GEMINI_API_KEY: otra"
            )
            self.assertNotIn("clave-super-secreta", limpio)
            self.assertNotIn("otra", limpio)
            self.assertIn("API_KEY_OCULTA", limpio)
        finally:
            if anterior is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = anterior

    def test_recorte_conserva_extremos_y_limite(self):
        texto = "INICIO" + "x" * 500 + "FINAL"
        resultado = recortar(texto, 100)
        self.assertLessEqual(len(resultado), 100)
        self.assertTrue(resultado.startswith("INICIO"))
        self.assertTrue(resultado.endswith("FINAL"))


if __name__ == "__main__":
    unittest.main()

