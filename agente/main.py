"""Orquestador del agente de reparación automática.

Contrato externo obligatorio:
    python main.py <carpeta_del_bug>

Al finalizar debe escribir ``localizacion.json`` y ``metricas.json`` en la carpeta del bug.
La implementación posterior debe conservar este comando y los formatos del enunciado.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from contratos import MODELO, reiniciar_metricas
from localizador import localizar
from reparador import reparar


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Uso: python main.py <carpeta_del_bug>")

    carpeta = Path(sys.argv[1]).resolve()
    info = json.loads((carpeta / "bug.json").read_text())
    inicio = time.perf_counter()
    metricas = reiniciar_metricas()

    # Contrato de integración: localizar produce exactamente la entrada de reparar.
    localizacion = localizar(carpeta, info)
    (carpeta / "localizacion.json").write_text(
        json.dumps(localizacion, indent=2, ensure_ascii=False)
    )
    resultado = reparar(carpeta, info, localizacion)

    salida_metricas = {
        "bug": info["id"],
        "modelo": MODELO,
        "llamadas": metricas.llamadas,
        "tokens_entrada": metricas.tokens_entrada,
        "tokens_salida": metricas.tokens_salida,
        "segundos": round(time.perf_counter() - inicio, 1),
    }
    (carpeta / "metricas.json").write_text(json.dumps(salida_metricas, indent=2))
    print(f"{info['id']}: {'RESUELTO' if resultado['resuelto'] else 'NO RESUELTO'}")


if __name__ == "__main__":
    main()

