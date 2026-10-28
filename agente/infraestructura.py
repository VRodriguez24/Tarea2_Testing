"""Infraestructura compartida para tiempo, procesos y llamadas a Gemini.

No contiene decisiones de localización o reparación. Centraliza las piezas que deben usar
ambas etapas para respetar el límite global y contabilizar el costo real una sola vez.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from contratos import (
    LIMITE_INTERNO_SEGUNDOS,
    MAXIMO_LLAMADAS_LLM,
    MODELO,
    Metricas,
    ResultadoProceso,
    obtener_metricas,
)


class PresupuestoAgotado(RuntimeError):
    """No queda tiempo seguro para comenzar una nueva operación."""


class EjecucionAgente:
    """Reloj global, procesos controlados y cliente LLM de una ejecución."""

    def __init__(
        self,
        metricas: Metricas | None = None,
        limite_segundos: float = LIMITE_INTERNO_SEGUNDOS,
    ) -> None:
        self.metricas = metricas or obtener_metricas()
        self.limite_segundos = float(limite_segundos)
        self.inicio = time.perf_counter()
        self._cliente: Any = None

    def transcurrido(self) -> float:
        return time.perf_counter() - self.inicio

    def restante(self, reserva: float = 0.0) -> float:
        return max(0.0, self.limite_segundos - self.transcurrido() - reserva)

    def limitar_timeout(self, solicitado: float, reserva: float = 0.0) -> float:
        return max(0.0, min(float(solicitado), self.restante(reserva)))

    def ejecutar(
        self,
        comando: list[str] | str,
        cwd: Path,
        timeout: float,
        *,
        shell: bool = False,
        reserva: float = 0.0,
    ) -> ResultadoProceso:
        """Ejecuta un proceso y mata todo su grupo cuando vence el timeout."""
        disponible = self.limitar_timeout(timeout, reserva)
        if disponible < 0.2:
            return ResultadoProceso(-1, "Sin tiempo suficiente para ejecutar el proceso", True)
        try:
            proceso = subprocess.Popen(
                comando,
                cwd=cwd,
                shell=shell,
                executable="/bin/bash" if shell else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                start_new_session=True,
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
            )
        except Exception as exc:
            return ResultadoProceso(-1, f"No se pudo iniciar el proceso: {exc}")

        try:
            salida, _ = proceso.communicate(timeout=disponible)
            return ResultadoProceso(proceso.returncode, salida or "")
        except subprocess.TimeoutExpired:
            os.killpg(proceso.pid, signal.SIGTERM)
            try:
                salida, _ = proceso.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proceso.pid, signal.SIGKILL)
                salida, _ = proceso.communicate()
            return ResultadoProceso(-1, (salida or "") + "\n[TIMEOUT]", True)

    def _obtener_cliente(self) -> Any:
        if self._cliente is None:
            if not os.environ.get("GEMINI_API_KEY"):
                raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")
            from google import genai
            from google.genai import types

            # Evita que una petición de red consuma por sí sola el límite global del bug.
            self._cliente = genai.Client(http_options=types.HttpOptions(timeout=25_000))
        return self._cliente

    @staticmethod
    def _es_error_temporal(exc: Exception) -> bool:
        descripcion = f"{type(exc).__name__}: {exc}".lower()
        return any(
            marca in descripcion
            for marca in (
                "429", "503", "504", "resource_exhausted", "unavailable",
                "deadline_exceeded", "temporarily", "timed out",
            )
        )

    def generar_json(
        self,
        prompt: str,
        *,
        reserva: float = 15.0,
    ) -> tuple[dict[str, Any] | None, str]:
        """Obtiene JSON de Gemini, contando toda respuesta recibida y sus tokens."""
        if self.metricas.llamadas >= MAXIMO_LLAMADAS_LLM:
            return None, "Se alcanzó el máximo de respuestas del LLM"

        ultimo_error = ""
        for espera in (0, 1, 2, 4, 8):
            if espera:
                if self.restante(reserva) <= espera + 2:
                    break
                time.sleep(espera)
            if self.restante(reserva) < 3:
                break
            try:
                cliente = self._obtener_cliente()
                from google.genai import types

                respuesta = cliente.models.generate_content(
                    model=MODELO,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                uso = getattr(respuesta, "usage_metadata", None)
                entrada = int(getattr(uso, "prompt_token_count", 0) or 0)
                salida = int(getattr(uso, "candidates_token_count", 0) or 0)
                self.metricas.registrar_respuesta(entrada, salida)
                texto = (getattr(respuesta, "text", None) or "").strip()
                try:
                    datos = json.loads(texto)
                except (json.JSONDecodeError, TypeError) as exc:
                    return None, f"Respuesta JSON inválida: {exc}; texto={texto[:1200]}"
                if not isinstance(datos, dict):
                    return None, "La respuesta JSON no es un objeto"
                return datos, ""
            except Exception as exc:
                ultimo_error = f"{type(exc).__name__}: {exc}"
                if not self._es_error_temporal(exc):
                    break
        return None, ultimo_error or "Sin tiempo para consultar Gemini"


_ejecucion: EjecucionAgente | None = None


def iniciar_ejecucion(
    metricas: Metricas | None = None,
    limite_segundos: float = LIMITE_INTERNO_SEGUNDOS,
) -> EjecucionAgente:
    global _ejecucion
    _ejecucion = EjecucionAgente(metricas, limite_segundos)
    return _ejecucion


def obtener_ejecucion() -> EjecucionAgente:
    global _ejecucion
    if _ejecucion is None:
        _ejecucion = EjecucionAgente()
    return _ejecucion


def recortar(texto: str, max_caracteres: int, conservar_final: bool = True) -> str:
    """Acota prompts/salidas conservando evidencia del principio y del final."""
    if len(texto) <= max_caracteres:
        return texto
    marca = "\n... [contenido recortado] ...\n"
    disponible = max(0, max_caracteres - len(marca))
    if conservar_final:
        inicio = disponible // 3
        final = disponible - inicio
        return texto[:inicio] + marca + texto[-final:] if final else texto[:inicio] + marca
    return texto[:disponible] + marca

