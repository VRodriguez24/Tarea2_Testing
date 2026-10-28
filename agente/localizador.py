"""Localización determinista basada en tests, tracebacks y estructura AST.

No aplica parches ni consulta fuentes externas. Reserva unos 40 segundos para
esta etapa (55 como tope para los archivos con evidencia directa); los tests
necesitan el entorno del bug previamente instalado.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import sys
import time
import warnings
from collections import defaultdict

from contratos import Localizacion
from infraestructura import obtener_ejecucion, recortar

PRESUPUESTO_SEGUNDOS = 40.0
# Tope absoluto, solo para archivos del traceback o importados por los tests.
LIMITE_DURO_SEGUNDOS = 55.0
MAX_LINEAS_REGION = 160
EXCLUIDOS = {"test", "tests", "testing", "env", ".venv", "venv", ".git", "__pycache__", "site-packages", "node_modules"}
# Palabras de nombres de test que no dicen nada sobre el código bajo prueba.
PALABRAS_VACIAS = {"test", "tests", "testcase", "case", "with", "and", "the", "for", "from",
                   "not", "should", "when", "that", "this", "get", "set", "can", "does",
                   "none", "true", "false", "self", "works", "check"}


def _ruta(repo: Path, nombre: str, fuente: bool = True) -> Path | None:
    """Resuelve rutas sin permitir escapes ni enlaces simbólicos."""
    try:
        ruta = Path(nombre)
        ruta = ruta if ruta.is_absolute() else repo / ruta
        relativa = ruta.relative_to(repo)
        actual = repo
        for parte in relativa.parts:
            if parte == "..":
                return None
            actual /= parte
            if actual.is_symlink():
                return None
        ruta = ruta.resolve()
        relativa = ruta.relative_to(repo)
        if not ruta.is_file() or ruta.suffix != ".py":
            return None
        if fuente and (any(p.lower() in EXCLUIDOS for p in relativa.parts)
                       or ruta.name.startswith("test_")
                       or ruta.name.endswith("_test.py")
                       or ruta.name == "conftest.py"):
            return None
        return ruta
    except (OSError, ValueError):
        return None


def _es_fuente(nombre: str) -> bool:
    """Filtro rápido por nombre: archivo Python que no es de tests."""
    return (nombre.endswith(".py") and not nombre.startswith("test_")
            and not nombre.endswith("_test.py") and nombre != "conftest.py")


def _leer(ruta: Path) -> str:
    try:
        # Evita archivos generados desproporcionados y errores de codificación.
        if ruta.stat().st_size > 2_000_000:
            return ""
        import tokenize
        with tokenize.open(ruta) as archivo:
            return archivo.read()
    except (OSError, UnicodeError, SyntaxError):
        return ""


def _arbol(texto: str) -> ast.AST | None:
    try:
        # El código de los proyectos es antiguo: evita SyntaxWarning por escapes como "\d".
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(texto)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _palabras(nombre: str) -> set[str]:
    """Separa un identificador (snake_case o camelCase) en palabras útiles."""
    nombre = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", nombre).lower()
    return {p for p in re.split(r"[^a-z0-9]+", nombre)
            if len(p) >= 3 and not p.isdigit() and p not in PALABRAS_VACIAS}


def _modulo(relativa: str) -> str:
    """Convierte 'pkg/sub/__init__.py' en 'pkg.sub' y 'pkg/mod.py' en 'pkg.mod'."""
    modulo = relativa[:-3].replace("/", ".")
    return modulo[:-len(".__init__")] if modulo.endswith(".__init__") else modulo


def _ejecutar_tests(repo: Path, info: dict, limite: float) -> tuple[str, str]:
    """Ejecuta el script objetivo usando el reloj y control de procesos compartidos."""
    if os.name != "posix":
        return "", "Tests no ejecutados: usar Linux, macOS o WSL con el entorno del bug instalado."
    activacion = repo / "env/bin/activate"
    if not activacion.is_file():
        return "", "Tests no ejecutados: falta preparar repo/env con setup.sh."
    carpeta = repo.parent
    timeout = max(0.0, limite - time.monotonic())
    resultado = obtener_ejecucion().ejecutar(
        ["bash", str(carpeta / "run_test.sh")], carpeta, timeout, reserva=115,
    )
    if resultado.agoto_tiempo:
        estado = "Se agotó el tiempo reservado para tests."
    else:
        estado = f"Los tests terminaron con código {resultado.codigo}."
    return recortar(resultado.salida, 60_000), estado


def _fragmento(repo: Path, region: dict) -> str:
    """Devuelve un fragmento numerado de una región determinista ya validada."""
    ruta = _ruta(repo, region["archivo"])
    if ruta is None:
        return ""
    lineas = _leer(ruta).splitlines()
    inicio, fin = region["inicio"], region["fin"]
    return "\n".join(
        f"{numero:>5}: {lineas[numero - 1]}"
        for numero in range(inicio, min(fin, len(lineas)) + 1)
    )


def _fragmentos_tests(repo: Path, info: dict) -> str:
    """Extrae solo las funciones objetivo de los archivos de test declarados."""
    objetivos = set()
    for comando in info.get("comandos_test", []):
        if isinstance(comando, str):
            objetivos.update(re.findall(r"\btest_[A-Za-z0-9_]+", comando))
    fragmentos = []
    for nombre in info.get("archivos_test", [])[:5]:
        ruta = _ruta(repo, nombre, fuente=False) if isinstance(nombre, str) else None
        if ruta is None:
            continue
        texto = _leer(ruta)
        lineas = texto.splitlines()
        arbol = _arbol(texto)
        nodos = [
            nodo for nodo in ast.walk(arbol)
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
            and nodo.name in objetivos
        ] if arbol else []
        for nodo in nodos:
            inicio = min([nodo.lineno] + [d.lineno for d in nodo.decorator_list])
            fin = min(nodo.end_lineno or nodo.lineno, inicio + 119)
            contenido = "\n".join(
                f"{numero:>5}: {lineas[numero - 1]}"
                for numero in range(inicio, fin + 1)
            )
            fragmentos.append(f"### TEST {nombre}:{inicio}-{fin}\n{contenido}")
    return recortar("\n\n".join(fragmentos), 8_000)


def _ordenar_con_llm(
    repo: Path, info: dict, salida: str, candidatos: list[dict]
) -> list[dict]:
    """Pide a Gemini ordenar candidatos; ante cualquier error conserva el ranking local."""
    if not candidatos:
        return []
    completos = []
    for region in candidatos[:3]:
        completos.append(
            f"### {region['archivo']}:{region['inicio']}-{region['fin']}\n"
            f"Señal: {region.get('razon', '')}\n{_fragmento(repo, region)}"
        )
    resumen = [
        {clave: region.get(clave) for clave in ("archivo", "inicio", "fin", "razon", "puntaje")}
        for region in candidatos[:8]
    ]
    prompt = f"""Eres la etapa LOCALIZADORA de un agente de reparación automática de Python.
Ordena entre una y cinco de las regiones candidatas que probablemente contienen la causa raíz.
Usa solo la evidencia incluida. No inventes rutas ni rangos y no propongas un parche.
Devuelve exclusivamente JSON estricto:
{{"regiones":[{{"archivo":"ruta.py","inicio":1,"fin":20,"razon":"explicación breve","puntaje":0.9}}]}}

COMANDOS OBJETIVO:
{json.dumps(info.get('comandos_test', []), ensure_ascii=False)}

FALLO OBSERVADO:
{recortar(salida, 10_000)}

CANDIDATOS DETERMINISTAS:
{json.dumps(resumen, ensure_ascii=False)}

CÓDIGO DE LAS TRES REGIONES PRINCIPALES:
{recortar(chr(10).join(completos), 24_000)}

FRAGMENTOS DE TEST OBJETIVO:
{_fragmentos_tests(repo, info) or 'No se pudo extraer una función objetivo.'}
"""
    datos, _ = obtener_ejecucion().generar_json(prompt, reserva=100)
    regiones_modelo = datos.get("regiones") if isinstance(datos, dict) else None
    if not isinstance(regiones_modelo, list):
        return candidatos[:5]

    por_rango = {
        (r["archivo"], r["inicio"], r["fin"]): r
        for r in candidatos
    }
    resultado = []
    seleccionadas = set()
    for propuesta in regiones_modelo:
        if not isinstance(propuesta, dict):
            continue
        clave = (propuesta.get("archivo"), propuesta.get("inicio"), propuesta.get("fin"))
        original = por_rango.get(clave)
        if original is None or clave in seleccionadas:
            continue
        validada = dict(original)
        if isinstance(propuesta.get("razon"), str):
            validada["razon"] = propuesta["razon"][:400]
        puntaje = propuesta.get("puntaje")
        if isinstance(puntaje, (int, float)) and not isinstance(puntaje, bool):
            validada["puntaje"] = max(0.0, min(1.0, float(puntaje)))
        resultado.append(validada)
        seleccionadas.add(clave)
        if len(resultado) == 5:
            break
    for candidato in candidatos:
        if len(resultado) == 5:
            break
        clave = (candidato["archivo"], candidato["inicio"], candidato["fin"])
        if clave not in seleccionadas:
            resultado.append(candidato)
            seleccionadas.add(clave)
    return resultado


def _evidencia_tests(repo: Path, info: dict) -> tuple[set[str], set[str], set[str]]:
    """Extrae símbolos de los tests objetivo y módulos importados como evidencia."""
    objetivos = set()
    for comando in info.get("comandos_test", []):
        objetivos.update(re.findall(r"\btest_[A-Za-z0-9_]+", comando))
    simbolos, modulos, palabras = set(), set(), set()
    for nombre in info.get("archivos_test", []):
        ruta = _ruta(repo, nombre, fuente=False)
        if ruta is None:
            continue
        texto = _leer(ruta)
        arbol = _arbol(texto)
        if arbol is None:
            continue
        aliases = {}
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.ImportFrom):
                modulo = nodo.module or ""
                modulos.add(modulo)
                for alias in nodo.names:
                    aliases[alias.asname or alias.name] = alias.name
                    simbolos.add(alias.name)
            elif isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    modulos.add(alias.name)
        seleccion = [n for n in ast.walk(arbol)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name in objetivos]
        for nodo in seleccion or [arbol]:
            for elemento in ast.walk(nodo):
                if isinstance(elemento, ast.Call):
                    funcion = elemento.func
                    if isinstance(funcion, ast.Name):
                        simbolos.add(aliases.get(funcion.id, funcion.id))
                    elif isinstance(funcion, ast.Attribute):
                        simbolos.add(funcion.attr)
                elif isinstance(elemento, ast.Attribute):
                    simbolos.add(elemento.attr)
            if seleccion:
                palabras.update(_palabras(nodo.name))
    return simbolos, modulos, palabras


def localizar(carpeta: Path, info: dict) -> Localizacion:
    """Devuelve hasta cinco regiones fuente, ordenadas por evidencia de la falla.

    Las líneas se refieren al código original y son inclusivas. No usa nombres
    específicos de proyectos o casos. Sin entorno disponible usa el código de
    los tests como señal; si no hay fuente válida devuelve regiones vacías.
    """
    inicio = time.monotonic()
    carpeta = Path(carpeta).resolve()
    repo = carpeta / "repo"
    if not repo.is_dir() or repo.is_symlink():
        return {"regiones": []}
    repo = repo.resolve()
    salida, estado = _ejecutar_tests(repo, info, inicio + 20.0)
    if estado:
        print(estado, file=sys.stderr)
    simbolos, modulos, palabras = _evidencia_tests(repo, info)
    frames = defaultdict(list)
    patrones = [r'File "([^"\n]+\.py)", line (\d+)',
                r'(?:^|\n)([^\n:]+\.py):(\d+):']
    for patron in patrones:
        for nombre, linea in re.findall(patron, salida):
            ruta = _ruta(repo, nombre.strip())
            if ruta:
                frames[ruta].append(int(linea))
    def _afinidad(modulo: str) -> bool:
        return any(m and (modulo == m or modulo.endswith("." + m)
                          or m.startswith(modulo + ".")) for m in modulos)

    # Lista barata de archivos; se analizan primero los del traceback y los
    # módulos importados por los tests, para no perderlos si se acaba el tiempo.
    # El listado es barato a propósito (sin resolve por archivo): os.walk ya
    # descarta directorios enlazados y _es_fuente filtra tests y no-Python.
    archivos = []
    for raiz, directorios, nombres in os.walk(repo, followlinks=False):
        if time.monotonic() - inicio >= LIMITE_DURO_SEGUNDOS:
            break
        directorios[:] = sorted(d for d in directorios
                               if d.lower() not in EXCLUIDOS
                               and not (Path(raiz) / d).is_symlink())
        archivos.extend(Path(raiz) / n for n in sorted(nombres) if _es_fuente(n))
    vistos = set(archivos)
    archivos.extend(r for r in list(frames) if r not in vistos)

    def _prioridad(ruta: Path) -> int:
        if frames[ruta]:
            return 0
        return 1 if _afinidad(_modulo(ruta.relative_to(repo).as_posix())) else 2
    archivos.sort(key=_prioridad)

    candidatos = []
    for ruta in archivos:
        prioridad = _prioridad(ruta)
        # Los archivos con evidencia directa tienen un margen extra para no quedar fuera.
        limite = LIMITE_DURO_SEGUNDOS if prioridad < 2 else PRESUPUESTO_SEGUNDOS
        if time.monotonic() - inicio >= limite:
            break
        if ruta.is_symlink():
            continue
        texto = _leer(ruta)
        lineas = texto.splitlines()
        if not lineas:
            continue
        arbol = _arbol(texto)
        relativa = ruta.relative_to(repo).as_posix()
        afinidad = _afinidad(_modulo(relativa))
        nodos = [n for n in ast.walk(arbol)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))] if arbol else []
        puntajes = {}  # nodo -> [puntuación, desde, hasta, razones, líneas del traceback]
        for nodo in nodos:
            desde = min([nodo.lineno] + [d.lineno for d in nodo.decorator_list])
            hasta = nodo.end_lineno or nodo.lineno
            impacto = [l for l in frames[ruta] if desde <= l <= hasta]
            puntuacion = 0
            razones = []
            if impacto:
                puntuacion += 100
                razones.append("región presente en traceback")
            if nodo.name in simbolos:
                puntuacion += 35
                razones.append("símbolo usado o importado por tests")
            if afinidad:
                puntuacion += 8
                razones.append("módulo importado por tests")
            # Palabras del nombre del test objetivo en el nombre y en el código de la región.
            en_nombre = sum(p in nodo.name.lower() for p in palabras)
            contenido = "\n".join(lineas[desde - 1:hasta]).lower()
            en_codigo = sum(p in contenido for p in palabras)
            puntuacion += min(12, en_nombre * 3) + min(9, en_codigo * 3)
            if en_nombre or en_codigo:
                razones.append("palabras del test objetivo")
            if puntuacion == 0:
                continue
            # Desempata a favor de la función frente a su clase. La penalización es
            # pequeña a propósito: si la clase acumula más evidencia (p. ej. palabras
            # del test en otros métodos), conviene reportarla entera, porque el arreglo
            # suele tocar el estado o métodos vecinos y no solo la línea que falla.
            if isinstance(nodo, ast.ClassDef):
                puntuacion -= 2
            puntajes[nodo] = [puntuacion, desde, hasta, razones, impacto]
        # Si varios métodos de una clase pequeña tienen evidencia y ninguno aparece
        # en el traceback, se reporta la clase completa en vez de trozos sueltos.
        for clase in (n for n in nodos if isinstance(n, ast.ClassDef)):
            hijos = [puntajes[h] for h in clase.body if h in puntajes]
            if len(hijos) < 2 or any(h[4] for h in hijos):
                continue
            desde = min([clase.lineno] + [d.lineno for d in clase.decorator_list])
            hasta = clase.end_lineno or clase.lineno
            if hasta - desde + 1 > MAX_LINEAS_REGION:
                continue
            propio = puntajes.get(clase, [0, desde, hasta, [], []])
            puntajes[clase] = [max(propio[0], max(h[0] for h in hijos) + 1), desde, hasta,
                               propio[3] + [f"agrupa {len(hijos)} métodos con evidencia"], []]
        regiones = []
        for puntuacion, desde, hasta, razones, impacto in puntajes.values():
            if hasta - desde + 1 > MAX_LINEAS_REGION:
                centro = impacto[-1] if impacto else desde + MAX_LINEAS_REGION // 2
                desde = max(desde, centro - MAX_LINEAS_REGION // 2)
                hasta = min(hasta, desde + MAX_LINEAS_REGION - 1)
            regiones.append((puntuacion, desde, hasta, "; ".join(razones)))
        for linea in frames[ruta]:
            if 1 <= linea <= len(lineas) and not any(a <= linea <= b for _, a, b, _ in regiones):
                regiones.append((100, max(1, linea - 15), min(len(lineas), linea + 15), "línea del traceback"))
        if not regiones and afinidad:
            regiones.append((5, 1, min(len(lineas), MAX_LINEAS_REGION), "módulo importado por tests"))
        candidatos.extend((p, relativa, a, b, razon) for p, a, b, razon in regiones)
    candidatos.sort(key=lambda c: (-c[0], c[3] - c[2], c[1], c[2]))
    resultado = []
    for puntos, archivo, desde, hasta, razon in candidatos:
        if any(r["archivo"] == archivo and not (hasta < r["inicio"] or desde > r["fin"])
               for r in resultado):
            continue
        resultado.append({"archivo": archivo, "inicio": desde, "fin": hasta,
                          "puntaje": puntos, "razon": razon})
        if len(resultado) == 5:
            break
    regiones = _ordenar_con_llm(repo, info, salida, resultado)
    return {
        "regiones": regiones,
        "diagnostico": {
            "salida": recortar(salida, 12_000),
            "estado": estado,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Uso: python localizador.py <carpeta_del_bug>")
    carpeta = Path(sys.argv[1]).resolve()
    info = json.loads((carpeta / "bug.json").read_text(encoding="utf-8"))
    print(json.dumps(localizar(carpeta, info), indent=2, ensure_ascii=False))
