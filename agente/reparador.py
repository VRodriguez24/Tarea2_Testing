"""Etapa de reparación: contexto, parche seguro, verificación y reintentos."""
from __future__ import annotations

import ast
import difflib
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from contratos import Localizacion, ResultadoProceso, ResultadoReparacion
from edicion_segura import CambioAplicado, aplicar_ediciones, es_archivo_test, resolver_fuente_segura
from infraestructura import obtener_ejecucion, recortar

MAXIMO_LINEAS_REGION = 180
MAXIMO_REGIONES_CONTEXTO = 3
MAXIMO_LINEAS_TEST_OBJETIVO = 180
MINIMO_SEGUNDOS_TERCER_INTENTO = 45.0


def _fragmento_numerado(ruta: Path, inicio: int, fin: int) -> str:
    lineas = ruta.read_text(errors="replace").splitlines()
    return "\n".join(f"{numero:>5}: {lineas[numero - 1]}" for numero in range(inicio, fin + 1))


def _nombres_tests_objetivo(info: dict) -> set[str]:
    """Obtiene nombres de tests desde los comandos declarados por el paquete."""
    nombres: set[str] = set()
    for comando in info.get("comandos_test", []):
        if isinstance(comando, str):
            nombres.update(re.findall(r"\btest_[A-Za-z0-9_]+", comando))
    return nombres


def _contexto_archivo_test(ruta: Path, nombre: str, objetivos: set[str]) -> str:
    """Extrae las funciones objetivo; usa un fallback corto para fixtures o datos."""
    texto = ruta.read_text(errors="replace")
    lineas = texto.splitlines()
    try:
        arbol = ast.parse(texto)
    except (SyntaxError, ValueError, RecursionError):
        arbol = None

    nodos = [
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
        and nodo.name in objetivos
    ] if arbol is not None else []
    if not nodos:
        return f"### TEST/DATO {nombre}\n{recortar(texto, 2500)}"

    fragmentos = []
    for nodo in nodos:
        inicio = min([nodo.lineno] + [decorador.lineno for decorador in nodo.decorator_list])
        fin = min(nodo.end_lineno or nodo.lineno, inicio + MAXIMO_LINEAS_TEST_OBJETIVO - 1)
        fragmentos.append(
            f"### TEST OBJETIVO {nombre}:{inicio}-{fin}\n"
            f"{_fragmento_numerado(ruta, inicio, fin)}"
        )
    return "\n\n".join(fragmentos)


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
    encabezados: set[Path] = set()
    for region in localizacion.get("regiones", [])[:MAXIMO_REGIONES_CONTEXTO]:
        try:
            ruta, inicio, fin = _normalizar_region(repo, region)
        except (ValueError, OSError):
            continue
        relativa = ruta.relative_to(repo.resolve()).as_posix()
        if inicio > 1 and ruta not in encabezados:
            total = len(ruta.read_text(errors="replace").splitlines())
            fin_encabezado = min(40, total)
            fuentes.append(
                f"### ENCABEZADO {relativa}:1-{fin_encabezado}\n"
                f"{_fragmento_numerado(ruta, 1, fin_encabezado)}"
            )
            encabezados.add(ruta)
        fuentes.append(
            f"### CÓDIGO {relativa}:{inicio}-{fin}\n"
            f"Razón: {str(region.get('razon', ''))[:300]}\n"
            f"{_fragmento_numerado(ruta, inicio, fin)}"
        )
    if not fuentes:
        raise ValueError("La localización no contiene regiones de código válidas")

    tests: list[str] = []
    objetivos = _nombres_tests_objetivo(info)
    for nombre in info.get("archivos_test", [])[:5]:
        if not isinstance(nombre, str) or not es_archivo_test(nombre):
            continue
        ruta = (repo / nombre).resolve()
        try:
            ruta.relative_to(repo.resolve())
        except ValueError:
            continue
        if ruta.is_file() and ruta.suffix == ".py":
            tests.append(_contexto_archivo_test(ruta, nombre, objetivos))

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


def _feedback_edicion_invalida(repo: Path, datos: Any, error: Exception) -> str:
    """Describe una propuesta rechazada sin leer rutas inseguras ni contexto excesivo."""
    partes = [
        f"El parche no se pudo aplicar o no compila: {type(error).__name__}: {error}",
        "PROPUESTA JSON ANTERIOR:",
        recortar(json.dumps(datos, ensure_ascii=False, indent=2), 6000),
    ]
    ediciones = datos.get("ediciones", []) if isinstance(datos, dict) else []
    for indice, edicion in enumerate(ediciones[:5], 1) if isinstance(ediciones, list) else []:
        if not isinstance(edicion, dict):
            continue
        try:
            ruta = resolver_fuente_segura(repo, edicion.get("archivo"))
            inicio, fin = edicion.get("inicio"), edicion.get("fin")
            lineas = ruta.read_text(errors="replace").splitlines()
            if not isinstance(inicio, int) or isinstance(inicio, bool):
                continue
            if not isinstance(fin, int) or isinstance(fin, bool):
                continue
            if not 1 <= inicio <= fin <= len(lineas):
                continue
            desde, hasta = max(1, inicio - 3), min(len(lineas), fin + 3)
            relativa = ruta.relative_to(repo.resolve()).as_posix()
            partes.append(
                f"CÓDIGO ORIGINAL ALREDEDOR DE EDICIÓN {indice} "
                f"({relativa}:{desde}-{hasta}):\n"
                f"{_fragmento_numerado(ruta, desde, hasta)}"
            )
        except (OSError, UnicodeError, ValueError):
            # Una ruta prohibida o inválida ya está explicada por el error principal.
            continue
    partes.append(
        "Corrige la propuesta usando los rangos originales. Conserva todas las líneas "
        "no relacionadas y verifica la indentación del contenido completo de reemplazo."
    )
    return recortar("\n".join(partes), 10_000)


def _prompt(info: dict, contexto: str, fallo: str, retroalimentacion: str) -> str:
    return f"""Eres la etapa REPARADORA de un agente de reparación automática de Python.
Diagnostica la causa raíz y produce el parche mínimo que corrige el bug sin regresiones.
No modifiques tests, fixtures, configuración, dependencias ni archivos fuera del código fuente.
Usa solamente la evidencia incluida. No devuelvas Markdown, comandos ni unified diff.

Devuelve JSON estricto con esta forma:
{{"analisis":"causa raíz breve","ediciones":[{{"archivo":"ruta.py","inicio":10,"fin":12,"contenido":"código de reemplazo"}}]}}

Reglas: rutas relativas a repo/; rangos inclusivos desde 1; contenido sin números de línea;
máximo 5 ediciones, 3 archivos y 30000 caracteres; conserva indentación y compatibilidad.
- Usa el rango mínimo necesario y produce un cambio real; no devuelvas contenido idéntico.
- El contenido reemplaza todo el rango: incluye completas las líneas que deban conservarse.
- No elimines asignaciones, ramas, imports ni comportamiento no relacionado con la causa raíz.
- Todo nombre nuevo debe estar definido o importado por el mismo parche. Si agregas un import,
  usa el encabezado real incluido y conserva todos los imports no relacionados.
- Revisa todas las aserciones y casos del TEST OBJETIVO, dando prioridad a la línea exacta del
  traceback. No rompas casos anteriores del mismo test para hacer pasar el caso que falla.
- No ocultes excepciones ni debilites validaciones solo para hacer pasar el test.
- Antes de responder, comprueba mentalmente sintaxis, indentación y compatibilidad con Python 3.8.

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
    """Genera dos parches y un tercero opcional; conserva solo uno verificado."""
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
    for intento in (1, 2, 3):
        if intento == 3 and ejecucion.restante() < MINIMO_SEGUNDOS_TERCER_INTENTO:
            print(
                "[reparador] tercer intento omitido: no quedan 45 segundos",
                file=sys.stderr,
            )
            break
        if ejecucion.restante(reserva=8) < 5:
            break
        datos, error = ejecucion.generar_json(
            _prompt(info, contexto, fallo, feedback), reserva=12
        )
        if datos is None:
            feedback = f"La respuesta anterior no fue procesable: {error}"
            print(f"[reparador intento {intento}] {feedback}", file=sys.stderr)
            continue

        cambio: CambioAplicado | None = None
        try:
            cambio = aplicar_ediciones(repo, datos)
        except Exception as exc:
            feedback = _feedback_edicion_invalida(repo, datos, exc)
            print(f"[reparador intento {intento}] {feedback}", file=sys.stderr)
            continue

        diff = _diff_cambio(cambio, repo)
        analisis = str(datos.get("analisis", ""))[:500]
        print(
            f"[reparador intento {intento}] análisis: {analisis}\n{recortar(diff, 4000)}",
            file=sys.stderr,
        )
        reserva_reintento = 38 if intento == 1 else 5
        objetivo = _ejecutar_objetivo(carpeta, timeout=45, reserva=reserva_reintento)
        if not objetivo.exitoso:
            cambio.revertir()
            feedback = (
                f"Los tests objetivo siguen fallando (código {objetivo.codigo}).\n"
                f"PARCHE ANTERIOR:\n{diff}\nSALIDA:\n{recortar(objetivo.salida, 7000)}"
            )
            print(
                f"[reparador intento {intento}] objetivo falló:\n"
                f"{recortar(objetivo.salida, 3000)}",
                file=sys.stderr,
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
            cambio.revertir()
            feedback = (
                "El parche pasó el objetivo, pero la regresión agotó el tiempo y no pudo "
                "verificarse. El cambio fue revertido; propón un parche mínimo y eficiente.\n"
                f"PARCHE ANTERIOR:\n{diff}\n"
                f"SALIDA DE REGRESIÓN:\n{recortar(regresion.salida, 3000)}"
            )
            print(
                f"[reparador intento {intento}] regresión agotó el tiempo; parche revertido",
                file=sys.stderr,
            )
            continue

        cambio.revertir()
        feedback = (
            "El parche pasa el objetivo pero introduce o mantiene fallos en la regresión. "
            "Propón un cambio más específico.\n"
            f"PARCHE ANTERIOR:\n{diff}\nREGRESIÓN:\n{recortar(regresion.salida, 7000)}"
        )
        print(
            f"[reparador intento {intento}] regresión falló:\n"
            f"{recortar(regresion.salida, 3000)}",
            file=sys.stderr,
        )

    return {"resuelto": False, "regresion_verificada": False}


if __name__ == "__main__":
    carpeta = Path(sys.argv[1]).resolve()
    info = json.loads((carpeta / "bug.json").read_text())
    localizacion: Localizacion = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(reparar(carpeta, info, localizacion), indent=2, ensure_ascii=False))

