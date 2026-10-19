"""Contrato de la etapa de reparación y verificación.

Responsable: Integrante 1 (tú). Esta parte puede desarrollarse usando el ejemplo guardado
en ``../apoyo/localizacion_ejemplo.json``, sin esperar la implementación del localizador.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from contratos import Localizacion, ResultadoReparacion, obtener_metricas


def reparar(
    carpeta: Path,
    info: dict,
    localizacion: Localizacion,
) -> ResultadoReparacion:
    """Genera, aplica y verifica una reparación de forma segura.

    Entradas:
        carpeta: paquete del bug; los cambios válidos quedan en ``carpeta/repo``.
        info: metadatos del ``bug.json``.
        localizacion: salida validada del localizador.
    Salida mínima:
        ``{"resuelto": bool}``.

    Responsabilidades de esta parte:
        - cliente Gemini, manejo 429/503 y conteo real de tokens;
        - construcción acotada del contexto de reparación;
        - respuesta JSON con ediciones por rangos inclusivos;
        - validación de rutas y prohibición absoluta de editar tests;
        - respaldo, aplicación, validación sintáctica y rollback;
        - tests objetivo, tests de regresión y hasta dos intentos;
        - presupuesto global de 170 segundos y cierre seguro.
    """
    metricas = obtener_metricas()
    raise NotImplementedError("El Integrante 1 debe implementar el reparador")


if __name__ == "__main__":
    carpeta = Path(sys.argv[1]).resolve()
    info = json.loads((carpeta / "bug.json").read_text())
    localizacion: Localizacion = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(reparar(carpeta, info, localizacion), indent=2, ensure_ascii=False))

