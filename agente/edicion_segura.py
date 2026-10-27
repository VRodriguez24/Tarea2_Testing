"""Validación y aplicación transaccional de cambios sobre código fuente."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PATRON_TEST = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$"
)
DIRECTORIOS_PROHIBIDOS = {
    ".git", ".venv", "env", "venv", "site-packages", "__pycache__", "build", "dist"
}
MAXIMO_EDICIONES = 5
MAXIMO_ARCHIVOS = 3
MAXIMO_CARACTERES_NUEVOS = 30_000


def es_archivo_test(ruta: str | Path) -> bool:
    return bool(PATRON_TEST.search(str(ruta).replace("\\", "/")))


def resolver_fuente_segura(repo: Path, nombre: Any) -> Path:
    """Resuelve una ruta del modelo y garantiza que sea fuente dentro de repo/."""
    if not isinstance(nombre, str) or not nombre or Path(nombre).is_absolute():
        raise ValueError("La ruta debe ser relativa a repo/")
    repo_real = repo.resolve()
    ruta = (repo / nombre).resolve()
    try:
        relativa = ruta.relative_to(repo_real)
    except ValueError as exc:
        raise ValueError("La ruta escapa de repo/") from exc
    if any(parte in DIRECTORIOS_PROHIBIDOS for parte in relativa.parts):
        raise ValueError("La ruta apunta a un entorno o directorio prohibido")
    if es_archivo_test(relativa):
        raise ValueError("Está prohibido modificar archivos de test")
    if ruta.suffix != ".py" or not ruta.is_file():
        raise ValueError("La edición debe apuntar a un archivo Python existente")
    return ruta


@dataclass
class CambioAplicado:
    """Respaldos byte a byte de una transacción ya aplicada."""

    respaldos: dict[Path, bytes]

    @property
    def archivos(self) -> list[Path]:
        return list(self.respaldos)

    def revertir(self) -> None:
        for ruta, contenido in self.respaldos.items():
            ruta.write_bytes(contenido)


def _validar_y_normalizar(repo: Path, datos: Any) -> list[tuple[Path, int, int, str]]:
    ediciones = datos.get("ediciones") if isinstance(datos, dict) else None
    if not isinstance(ediciones, list) or not 1 <= len(ediciones) <= MAXIMO_EDICIONES:
        raise ValueError(f"Se requieren entre 1 y {MAXIMO_EDICIONES} ediciones")

    resultado: list[tuple[Path, int, int, str]] = []
    rangos: dict[Path, list[tuple[int, int]]] = {}
    caracteres = 0
    for edicion in ediciones:
        if not isinstance(edicion, dict):
            raise ValueError("Cada edición debe ser un objeto JSON")
        ruta = resolver_fuente_segura(repo, edicion.get("archivo"))
        inicio, fin, contenido = (
            edicion.get("inicio"), edicion.get("fin"), edicion.get("contenido")
        )
        total = len(ruta.read_text(errors="replace").splitlines())
        if not isinstance(inicio, int) or isinstance(inicio, bool):
            raise ValueError("inicio debe ser un entero")
        if not isinstance(fin, int) or isinstance(fin, bool):
            raise ValueError("fin debe ser un entero")
        if not 1 <= inicio <= fin <= total:
            raise ValueError(f"Rango fuera de límites en {ruta.name}: {inicio}-{fin}")
        if not isinstance(contenido, str):
            raise ValueError("contenido debe ser texto")
        caracteres += len(contenido)
        rangos.setdefault(ruta, []).append((inicio, fin))
        resultado.append((ruta, inicio, fin, contenido))

    if len(rangos) > MAXIMO_ARCHIVOS:
        raise ValueError(f"El parche modifica más de {MAXIMO_ARCHIVOS} archivos")
    if caracteres > MAXIMO_CARACTERES_NUEVOS:
        raise ValueError("El contenido nuevo excede el límite permitido")
    for lista in rangos.values():
        ordenados = sorted(lista)
        if any(actual[0] <= anterior[1] for anterior, actual in zip(ordenados, ordenados[1:])):
            raise ValueError("El parche contiene rangos superpuestos")
    return resultado


def aplicar_ediciones(repo: Path, datos: Any) -> CambioAplicado:
    """Aplica todo o nada; ante cualquier error restaura los bytes originales."""
    ediciones = _validar_y_normalizar(repo, datos)
    rutas = {edicion[0] for edicion in ediciones}
    respaldos = {ruta: ruta.read_bytes() for ruta in rutas}
    try:
        for ruta in rutas:
            original_bytes = respaldos[ruta]
            original = original_bytes.decode("utf-8")
            conserva_salto = original.endswith("\n")
            lineas = original.splitlines()
            propias = [e for e in ediciones if e[0] == ruta]
            for _, inicio, fin, contenido in sorted(propias, key=lambda e: e[1], reverse=True):
                lineas[inicio - 1 : fin] = contenido.splitlines()
            nuevo = "\n".join(lineas) + ("\n" if conserva_salto else "")
            ast.parse(nuevo, filename=str(ruta))
            ruta.write_text(nuevo, encoding="utf-8")
    except Exception:
        for ruta, contenido in respaldos.items():
            ruta.write_bytes(contenido)
        raise
    return CambioAplicado(respaldos)

