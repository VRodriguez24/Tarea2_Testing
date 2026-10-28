"""Pruebas del localizador sin API ni entornos de los proyectos."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'agente'))
import localizador


class LocalizadorTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.carpeta = Path(self.temporal.name)
        self.repo = self.carpeta / 'repo'
        (self.repo / 'tests').mkdir(parents=True)
        self.fuente = self.repo / 'operaciones.py'
        self.fuente.write_text('def sumar(a, b):\n    return a - b\n\ndef otra():\n    return 1\n')
        (self.repo / 'tests/test_operaciones.py').write_text(
            'from operaciones import sumar\ndef test_suma():\n    assert sumar(2, 3) == 5\n')
        self.info = {'comandos_test': ['pytest tests/test_operaciones.py::test_suma'],
                     'archivos_test': ['tests/test_operaciones.py']}

    def ejecutar(self, salida=''):
        with patch.object(localizador, '_ejecutar_tests', return_value=(salida, '')):
            return localizador.localizar(self.carpeta, self.info)['regiones']

    def test_import_y_rango_sin_modificar_fuente(self):
        original = self.fuente.read_bytes()
        regiones = self.ejecutar()
        self.assertEqual((regiones[0]['archivo'], regiones[0]['inicio'], regiones[0]['fin']),
                         ('operaciones.py', 1, 2))
        self.assertEqual(original, self.fuente.read_bytes())
        json.dumps(regiones)

    def test_traceback_tiene_prioridad_sobre_imports(self):
        regiones = self.ejecutar(f'File "{self.fuente}", line 5, in otra')
        self.assertEqual(regiones[0]['inicio'], 4)
        self.assertIn('traceback', regiones[0]['razon'])

    def test_traceback_pytest(self):
        regiones = self.ejecutar('operaciones.py:5: AssertionError')
        self.assertEqual(regiones[0]['inicio'], 4)

    def test_excluye_tests_y_escapes(self):
        self.assertIsNone(localizador._ruta(self.repo, '../externo.py'))
        self.assertIsNone(localizador._ruta(self.repo, 'tests/test_operaciones.py'))
        self.assertTrue(all('tests/' not in r['archivo'] for r in self.ejecutar()))

    def test_funcion_grande_se_recorta_cerca_del_error(self):
        self.fuente.write_text('def sumar(a, b):\n' + '    a += 1\n' * 500 + '    return a\n')
        regiones = self.ejecutar('operaciones.py:400: AssertionError')
        self.assertLessEqual(regiones[0]['fin'] - regiones[0]['inicio'] + 1, 160)
        self.assertLessEqual(regiones[0]['inicio'], 400)
        self.assertGreaterEqual(regiones[0]['fin'], 400)

    def test_sin_fuente_no_inventa_regiones(self):
        self.fuente.unlink()
        self.assertEqual(self.ejecutar(), [])

    def test_palabras_del_test_en_el_codigo_desempatan(self):
        # Dos clases importadas por el test; solo una menciona "scheme" en su código.
        self.fuente.write_text(
            'class Pedido:\n    def poner(self, url):\n        raise ValueError("Missing scheme")\n\n'
            'class Otro:\n    def poner(self, x):\n        return x\n')
        (self.repo / 'tests/test_operaciones.py').write_text(
            'from operaciones import Pedido, Otro\ndef test_url_no_scheme():\n    Pedido(); Otro()\n')
        self.info['comandos_test'] = ['pytest tests/test_operaciones.py::test_url_no_scheme']
        regiones = self.ejecutar()
        self.assertEqual(regiones[0]['inicio'], 1)
        self.assertIn('palabras del test', regiones[0]['razon'])

    def test_agrupa_metodos_de_una_clase(self):
        self.fuente.write_text(
            'class Envoltura:\n    def host(self):\n        return 1\n\n'
            '    def tipo(self):\n        return 2\n\n    def otro(self):\n        return 3\n')
        (self.repo / 'tests/test_operaciones.py').write_text(
            'from operaciones import Envoltura\ndef test_suma():\n    Envoltura().host(); Envoltura().tipo()\n')
        regiones = self.ejecutar()
        self.assertEqual((regiones[0]['inicio'], regiones[0]['fin']), (1, 9))
        self.assertIn('agrupa', regiones[0]['razon'])

    def test_prioriza_modulos_importados_si_se_acaba_el_tiempo(self):
        (self.repo / 'aaa_relleno.py').write_text('def sumar_relleno():\n    return 0\n')
        with patch.object(localizador, 'PRESUPUESTO_SEGUNDOS', 0.0):
            regiones = self.ejecutar()
        self.assertEqual({r['archivo'] for r in regiones}, {'operaciones.py'})

    def test_gemini_solo_puede_reordenar_candidatos_validados(self):
        candidatos = [
            {'archivo': 'operaciones.py', 'inicio': 1, 'fin': 2,
             'razon': 'ranking local', 'puntaje': 35},
            {'archivo': 'operaciones.py', 'inicio': 4, 'fin': 5,
             'razon': 'ranking local', 'puntaje': 8},
        ]
        respuesta = {
            'regiones': [
                {'archivo': '../escape.py', 'inicio': 1, 'fin': 2, 'puntaje': 1},
                {'archivo': 'operaciones.py', 'inicio': 4, 'fin': 5,
                 'razon': 'mejor explicación', 'puntaje': 0.8},
            ]
        }
        ejecucion = unittest.mock.Mock()
        ejecucion.generar_json.return_value = (respuesta, '')
        with patch.object(localizador, 'obtener_ejecucion', return_value=ejecucion):
            regiones = localizador._ordenar_con_llm(
                self.repo, self.info, 'AssertionError', candidatos
            )
        self.assertEqual(regiones[0]['inicio'], 4)
        self.assertEqual(regiones[0]['razon'], 'mejor explicación')
        self.assertEqual(len(regiones), 2)
        self.assertTrue(all(r['archivo'] == 'operaciones.py' for r in regiones))

    def test_contexto_llm_incluye_solo_test_objetivo(self):
        (self.repo / 'tests/test_operaciones.py').write_text(
            'def test_otro():\n    assert False\n\n'
            'def test_suma():\n    assert sumar(2, 3) == 5\n'
        )
        fragmento = localizador._fragmentos_tests(self.repo, self.info)
        self.assertIn('def test_suma', fragmento)
        self.assertNotIn('def test_otro', fragmento)


if __name__ == '__main__':
    unittest.main()
