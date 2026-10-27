"""Orquestador robusto: localizar, reparar y escribir siempre ambos JSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from contratos import MODELO, Localizacion, reiniciar_metricas
from edicion_segura import DIRECTORIOS_PROHIBIDOS, es_archivo_test
from infraestructura import iniciar_ejecucion
from localizador import localizar
from reparador import reparar


def _escribir_json_atomico(ruta: Path, datos: dict) -> None:
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(json.dumps(datos, indent=2, ensure_ascii=False))
    temporal.replace(ruta)


def _localizacion_emergencia(repo: Path, error: str = "") -> Localizacion:
    """Mantiene el contrato si el localizador falla; no sustituye su estrategia normal."""
    for ruta in repo.rglob("*.py"):
        relativa = ruta.relative_to(repo)
        if any(parte in DIRECTORIOS_PROHIBIDOS for parte in relativa.parts):
            continue
        if es_archivo_test(relativa) or not ruta.is_file():
            continue
        total = len(ruta.read_text(errors="replace").splitlines())
        if total:
            return {"regiones": [{
                "archivo": relativa.as_posix(), "inicio": 1, "fin": min(120, total),
                "razon": f"fallback por error del localizador: {error[:200]}", "puntaje": 0.0,
            }]}
    return {"regiones": []}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Uso: python main.py <carpeta_del_bug>")
    carpeta = Path(sys.argv[1]).resolve()
    repo = carpeta / "repo"
    metricas = reiniciar_metricas()
    ejecucion = iniciar_ejecucion(metricas)
    info: dict = {"id": carpeta.name}
    localizacion: Localizacion = _localizacion_emergencia(repo)
    resultado = {"resuelto": False}
    localizador_completo = False

    try:
        info = json.loads((carpeta / "bug.json").read_text())
        try:
            localizacion = localizar(carpeta, info)
            localizador_completo = bool(localizacion.get("regiones"))
        except Exception as exc:
            print(f"[localizador] {type(exc).__name__}: {exc}", file=sys.stderr)
            localizacion = _localizacion_emergencia(repo, str(exc))
        _escribir_json_atomico(carpeta / "localizacion.json", localizacion)
        if localizador_completo:
            resultado = reparar(carpeta, info, localizacion)
    except Exception as exc:
        print(f"[agente] {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        salida_metricas = {
            "bug": str(info.get("id", carpeta.name)),
            "modelo": MODELO,
            "llamadas": metricas.llamadas,
            "tokens_entrada": metricas.tokens_entrada,
            "tokens_salida": metricas.tokens_salida,
            "segundos": round(ejecucion.transcurrido(), 1),
        }
        try:
            _escribir_json_atomico(carpeta / "localizacion.json", localizacion)
            _escribir_json_atomico(carpeta / "metricas.json", salida_metricas)
        except OSError as exc:
            print(f"[resultados] No se pudieron escribir los JSON: {exc}", file=sys.stderr)

    print(f"{info.get('id', carpeta.name)}: {'RESUELTO' if resultado.get('resuelto') else 'NO RESUELTO'}")


if __name__ == "__main__":
    main()
