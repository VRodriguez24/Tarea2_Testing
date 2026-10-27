"""Contratos compartidos por las etapas del agente.

Este módulo define estructuras y constantes; no contiene la estrategia del localizador ni
del reparador. Ambos integrantes pueden importarlo sin crear dependencias entre sus partes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

MODELO = "gemini-3.5-flash-lite"
LIMITE_INTERNO_SEGUNDOS = 170.0
MAXIMO_LLAMADAS_LLM = 3


class RegionSospechosa(TypedDict):
    """Región inclusiva de código fuente, relativa a ``repo/``."""

    archivo: str
    inicio: int
    fin: int
    razon: NotRequired[str]
    puntaje: NotRequired[float]


class Localizacion(TypedDict):
    """Salida pública del localizador y entrada pública del reparador."""

    regiones: list[RegionSospechosa]
    diagnostico: NotRequired[dict]


class EdicionCodigo(TypedDict):
    """Reemplazo inclusivo que podrá solicitar el reparador al modelo."""

    archivo: str
    inicio: int
    fin: int
    contenido: str


class ResultadoReparacion(TypedDict):
    """Resultado mínimo que ``reparar`` entrega al orquestador."""

    resuelto: bool
    regresion_verificada: NotRequired[bool]
    error: NotRequired[str]


@dataclass(frozen=True)
class ResultadoProceso:
    """Contrato de cualquier comando de test ejecutado por el agente."""

    codigo: int
    salida: str
    agoto_tiempo: bool = False

    @property
    def exitoso(self) -> bool:
        return self.codigo == 0 and not self.agoto_tiempo


@dataclass
class Metricas:
    """Acumulador único que compartirán localizador y reparador."""

    llamadas: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0

    def registrar_respuesta(self, tokens_entrada: int, tokens_salida: int) -> None:
        """Cuenta solo llamadas que efectivamente obtuvieron una respuesta."""
        self.llamadas += 1
        self.tokens_entrada += max(0, int(tokens_entrada))
        self.tokens_salida += max(0, int(tokens_salida))


_metricas = Metricas()


def reiniciar_metricas() -> Metricas:
    """Inicia el acumulador compartido de una ejecución de ``main.py``."""
    global _metricas
    _metricas = Metricas()
    return _metricas


def obtener_metricas() -> Metricas:
    """Entrega el mismo acumulador a ambas etapas sin alterar sus firmas obligatorias."""
    return _metricas


@dataclass(frozen=True)
class ContextoBug:
    """Datos inmutables y rutas comunes a las dos etapas."""

    carpeta: Path
    repo: Path
    info: dict

    @classmethod
    def crear(cls, carpeta: Path, info: dict) -> "ContextoBug":
        carpeta = carpeta.resolve()
        return cls(carpeta=carpeta, repo=carpeta / "repo", info=info)

