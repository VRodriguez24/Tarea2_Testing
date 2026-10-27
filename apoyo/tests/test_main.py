import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

AGENTE = Path(__file__).resolve().parents[2] / "agente"
sys.path.insert(0, str(AGENTE))

import main


class MainTests(unittest.TestCase):
    def test_escribe_ambos_json_si_localizador_falla(self):
        with tempfile.TemporaryDirectory() as temporal:
            bug = Path(temporal) / "bug-x"
            repo = bug / "repo" / "paquete"
            repo.mkdir(parents=True)
            (repo / "modulo.py").write_text("def funcion():\n    return 1\n")
            (bug / "bug.json").write_text(json.dumps({"id": "bug-x"}))
            with (
                patch.object(sys, "argv", ["main.py", str(bug)]),
                patch("main.localizar", side_effect=RuntimeError("fallo controlado")),
                patch("main.reparar") as reparar,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                main.main()

            reparar.assert_not_called()
            localizacion = json.loads((bug / "localizacion.json").read_text())
            metricas = json.loads((bug / "metricas.json").read_text())
            self.assertEqual(localizacion["regiones"][0]["archivo"], "paquete/modulo.py")
            self.assertEqual(
                set(metricas),
                {"bug", "modelo", "llamadas", "tokens_entrada", "tokens_salida", "segundos"},
            )
            self.assertEqual(metricas["llamadas"], 0)


if __name__ == "__main__":
    unittest.main()
