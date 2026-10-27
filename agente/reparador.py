"""Etapa de reparación: contexto, parche seguro, verificación y reintentos."""
from __future__ import annotations

import difflib
import json
import shlex
import sys
from pathlib import Path
from typing import Any

from contratos import Localizacion, ResultadoProceso, ResultadoReparacion
from edicion_segura import CambioAplicado, aplicar_ediciones, es_archivo_test, resolver_fuente_segura
from infraestructura import obtener_ejecucion, recortar

MAXIMO_LINEAS_REGION = 180
MAXIMO_REGIONES_CONTEXTO = 3


def _fragmento_numerado(ruta: Path, inicio: int, fin: int) -> str:
    lineas = ruta.read_text(errors="replace").splitlines()
    return "\n".join(f"{numero:>5}: {lineas[numero - 1]}" for numero in range(inicio, fin + 1))


def _normalizar_region(repo: Path, region: Any) -> tuple[Path, int, int]:
    if not isinstance(region, dict):
        raise ValueError("La región no es un objeto")
    ruta = resolver_fuente_segura(repo, region.get("archivo"))
    inicio, fin = region.get("inicio"), region.get("fin")
    total = len(ruta.read_text(errors="replace").splitlines())
    if not isinstance(inicio, int) or isinstance(inicio, bool):
        raise ValueError("inicio inválido")
    if not isinstance(fin, int) or isinstance(fin, bool) or not 1 <= inicio <= fin <= total:
        raise ValueError("rango de localización inválido")
    if fin - inicio + 1 > MAXIMO_LINEAS_REGION:
        centro = (inicio + fin) // 2
        mitad = MAXIMO_LINEAS_REGION // 2
        inicio = max(1, centro - mitad)
        fin = min(total, inicio + MAXIMO_LINEAS_REGION - 1)
        inicio = max(1, fin - MAXIMO_LINEAS_REGION + 1)
    return ruta, inicio, fin


def construir_contexto(repo: Path, info: dict, localizacion: Localizacion) -> str:
    """Construye contexto acotado sin recorrer ni enviar el repositorio completo."""
    fuentes: list[str] = []
    for region in localizacion.get("regiones", [])[:MAXIMO_REGIONES_CONTEXTO]:
        try:
            ruta, inicio, fin = _normalizar_region(repo, region)
        except (ValueError, OSError):
            continue
        relativa = ruta.relative_to(repo.resolve()).as_posix()
        fuentes.append(
            f"### CÓDIGO {relativa}:{inicio}-{fin}\n"
            f"Razón: {str(region.get('razon', ''))[:300]}\n"
            f"{_fragmento_numerado(ruta, inicio, fin)}"
        )
    if not fuentes:
        raise ValueError("La localización no contiene regiones de código válidas")

    tests: list[str] = []
    for nombre in info.get("archivos_test", [])[:5]:
        if not isinstance(nombre, str) or not es_archivo_test(nombre):
            continue
        ruta = (repo / nombre).resolve()
        try:
            ruta.relative_to(repo.resolve())
        except ValueError:
            continue
        if ruta.is_file() and ruta.suffix == ".py":
            tests.append(f"### TEST {nombre}\n{recortar(ruta.read_text(errors='replace'), 6000)}")

    codigo = recortar("\n\n".join(fuentes), 25_000)
    codigo_tests = recortar("\n\n".join(tests), 12_000)
    return f"{codigo}\n\n{codigo_tests}" if codigo_tests else codigo


def _ejecutar_objetivo(carpeta: Path, timeout: float, reserva: float = 0) -> ResultadoProceso:
    return obtener_ejecucion().ejecutar(
        ["bash", str(carpeta / "run_test.sh")], carpeta, timeout, reserva=reserva
    )


def _ejecutar_regresion(carpeta: Path, info: dict, timeout: float, reserva: float) -> ResultadoProceso:
    modulos = info.get("modulos_test", [])
    if not modulos:
        return ResultadoProceso(0, "No hay módulos de regresión declarados")
    repo = carpeta / "repo"
    usa_pytest = any("pytest" in str(comando) for comando in info.get("comandos_test", []))
    if usa_pytest:
        argumentos = " ".join(shlex.quote(str(modulo)) for modulo in modulos)
        comando = f"source env/bin/activate && python -m pytest -p no:cacheprovider -q {argumentos}"
    else:
        nombres = [
            str(modulo)[:-3].replace("/", ".") if str(modulo).endswith(".py") else str(modulo)
            for modulo in modulos
        ]
        comando = "source env/bin/activate && python -m unittest -q " + " ".join(
            shlex.quote(nombre) for nombre in nombres
        )
    return obtener_ejecucion().ejecutar(
        comando, repo, timeout, shell=True, reserva=reserva
    )


def _diff_cambio(cambio: CambioAplicado, repo: Path) -> str:
    partes: list[str] = []
    for ruta, anterior_bytes in cambio.respaldos.items():
        anterior = anterior_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        actual = ruta.read_text(errors="replace").splitlines(keepends=True)
        relativa = ruta.relative_to(repo.resolve()).as_posix()
        partes.extend(
            difflib.unified_diff(anterior, actual, fromfile=f"a/{relativa}", tofile=f"b/{relativa}")
        )
    return recortar("".join(partes), 9000)


def _prompt(info: dict, contexto: str, fallo: str, retroalimentacion: str) -> str:
    return f"""Eres la etapa REPARADORA de un agente de reparación automática de Python.
Diagnostica la causa raíz y produce el parche mínimo que corrige el bug sin regresiones.
No modifiques tests, fixtures, configuración, dependencias ni archivos fuera del código fuente.
Usa solamente la evidencia incluida. No devuelvas Markdown, comandos ni unified diff.

Devuelve JSON estricto con esta forma:
{{"analisis":"causa raíz breve","ediciones":[{{"archivo":"ruta.py","inicio":10,"fin":12,"contenido":"código de reemplazo"}}]}}

Reglas: rutas relativas a repo/; rangos inclusivos desde 1; contenido sin números de línea;
máximo 5 ediciones, 3 archivos y 30000 caracteres; conserva indentación y compatibilidad.

COMANDOS OBJETIVO:
{json.dumps(info.get('comandos_test', []), ensure_ascii=False)}

FALLO OBSERVADO:
{recortar(fallo, 12_000)}

CONTEXTO ACOTADO:
{contexto}

RETROALIMENTACIÓN:
{recortar(retroalimentacion, 10_000) if retroalimentacion else 'Primer intento; no existe parche anterior.'}
"""


def reparar(carpeta: Path, info: dict, localizacion: Localizacion) -> ResultadoReparacion:
    """Genera hasta dos parches; conserva únicamente uno seguro y verificado."""
    ejecucion = obtener_ejecucion()
    repo = carpeta / "repo"
    try:
        contexto = construir_contexto(repo, info, localizacion)
    except Exception as exc:
        return {"resuelto": False, "regresion_verificada": False, "error": str(exc)}

    diagnostico = localizacion.get("diagnostico", {})
    fallo = diagnostico.get("salida", "") if isinstance(diagnostico, dict) else ""
    if not fallo:
        inicial = _ejecutar_objetivo(carpeta, timeout=30, reserva=90)
        fallo = inicial.salida
        if inicial.exitoso:
            regresion = _ejecutar_regresion(carpeta, info, timeout=45, reserva=5)
            return {"resuelto": regresion.exitoso, "regresion_verificada": regresion.exitoso}

    feedback = ""
    for intento in (1, 2):
        if ejecucion.restante(reserva=8) < 5:
            break
        datos, error = ejecucion.generar_json(
            _prompt(info, contexto, fallo, feedback), reserva=12
        )
        if datos is None:
            feedback = f"La respuesta anterior no fue procesable: {error}"
            continue

        cambio: CambioAplicado | None = None
        try:
            cambio = aplicar_ediciones(repo, datos)
        except Exception as exc:
            feedback = f"El parche no se pudo aplicar o no compila: {type(exc).__name__}: {exc}"
            continue

        diff = _diff_cambio(cambio, repo)
        reserva_reintento = 38 if intento == 1 else 5
        objetivo = _ejecutar_objetivo(carpeta, timeout=45, reserva=reserva_reintento)
        if not objetivo.exitoso:
            cambio.revertir()
            feedback = (
                f"Los tests objetivo siguen fallando (código {objetivo.codigo}).\n"
                f"PARCHE ANTERIOR:\n{diff}\nSALIDA:\n{recortar(objetivo.salida, 7000)}"
            )
            continue

        tiempo_regresion = ejecucion.limitar_timeout(55, reserva=35 if intento == 1 else 4)
        if tiempo_regresion < 2:
            return {"resuelto": True, "regresion_verificada": False}
        regresion = _ejecutar_regresion(
            carpeta, info, timeout=tiempo_regresion, reserva=35 if intento == 1 else 4
        )
        if regresion.exitoso:
            return {"resuelto": True, "regresion_verificada": True}
        if regresion.agoto_tiempo:
            # El evaluador externo todavía puede aprobar el parche; se conserva el objetivo válido.
            return {"resuelto": True, "regresion_verificada": False}

        cambio.revertir()
        feedback = (
            "El parche pasa el objetivo pero introduce o mantiene fallos en la regresión. "
            "Propón un cambio más específico.\n"
            f"PARCHE ANTERIOR:\n{diff}\nREGRESIÓN:\n{recortar(regresion.salida, 7000)}"
        )

    return {"resuelto": False, "regresion_verificada": False}


if __name__ == "__main__":
    carpeta = Path(sys.argv[1]).resolve()
    info = json.loads((carpeta / "bug.json").read_text())
    localizacion: Localizacion = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(reparar(carpeta, info, localizacion), indent=2, ensure_ascii=False))

