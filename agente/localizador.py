"""Contrato de la etapa de localización.

Responsable: Integrante 2.

La implementación debe ejecutar los tests, usar señales de la falla, seleccionar contexto
acotado y devolver regiones válidas de código fuente. No modifica el repositorio.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from contratos import Localizacion, obtener_metricas


def localizar(carpeta: Path, info: dict) -> Localizacion:
    """Localiza regiones sospechosas dentro de ``carpeta/repo``.

    Entradas:
        carpeta: paquete del bug que contiene ``repo/`` y ``run_test.sh``.
        info: contenido ya validado de ``bug.json``.
    Salida:
        ``{"regiones": [...]}``, con entre una y cinco regiones ordenadas de mayor
        a menor sospecha. Las rutas son relativas a ``repo/``; las líneas comienzan
        en 1 y ambos extremos son inclusivos. Nunca devuelve archivos de test.

    Efectos permitidos:
        Ejecutar tests y consultar Gemini. No modifica archivos de ``repo/``.
    """
    metricas = obtener_metricas()
    raise NotImplementedError("El Integrante 2 debe implementar el localizador")


if __name__ == "__main__":
    carpeta = Path(sys.argv[1]).resolve()
    info = json.loads((carpeta / "bug.json").read_text())
    print(json.dumps(localizar(carpeta, info), indent=2, ensure_ascii=False))

