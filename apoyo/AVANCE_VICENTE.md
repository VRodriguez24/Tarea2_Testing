# Avance de la rama `vicente`

## Implementado

- Presupuesto global monotónico de 170 segundos.
- Subprocesos en grupos independientes con timeout y terminación completa.
- Cliente `google-genai` con el modelo obligatorio, timeout de red y backoff para 429/503.
- Límite compartido de tres respuestas del LLM y métricas reales de tokens.
- Construcción de contexto con máximo tres regiones, 180 líneas por región y tests acotados.
- Ediciones JSON por rangos inclusivos.
- Protección contra rutas absolutas, `..`, symlinks externos, tests y entornos.
- Aplicación transaccional, validación AST y rollback byte a byte.
- Tests objetivo, módulos de regresión y segundo intento con feedback del diff/error.
- `main.py` escribe siempre `localizacion.json` y `metricas.json` mediante `finally`.
- Trece pruebas aisladas bajo `apoyo/tests/`.

## Integración requerida del localizador

El localizador conserva la firma obligatoria:

```python
def localizar(carpeta: Path, info: dict) -> Localizacion:
```

Debe reutilizar la ejecución ya creada por `main.py`:

```python
from infraestructura import obtener_ejecucion

ejecucion = obtener_ejecucion()
resultado_test = ejecucion.ejecutar(...)
datos, error = ejecucion.generar_json(prompt, reserva=...)
```

Esto garantiza que localizador y reparador compartan:

- el mismo reloj de 170 segundos;
- el máximo total de tres respuestas;
- `llamadas`, `tokens_entrada` y `tokens_salida` reales.

La salida mínima sigue siendo:

```json
{"regiones": [{"archivo": "modulo.py", "inicio": 10, "fin": 40}]}
```

Opcionalmente puede incluir el fallo ya capturado para evitar que el reparador repita el test:

```json
{
  "regiones": [...],
  "diagnostico": {"salida": "traceback y mensaje de aserción"}
}
```

Las rutas deben ser relativas a `repo/`, solo código Python, y los rangos parten en 1 y son
inclusivos. El reparador vuelve a validarlos y descarta regiones inseguras.

## Validación realizada

- Suite aislada: 13/13 pruebas correctas con Python 3.14.10.
- API: modelo `gemini-3.5-flash-lite` accesible con la clave de `config.env`.
- Bug sintético real: reparado con una llamada, 472 tokens de entrada, 100 de salida y 19,2 s.

## Pendiente de integración

- Reemplazar el `NotImplementedError` de `localizador.py` con la rama de Josemi.
- Preparar un dataset local íntegro: varios repositorios entregados tienen objetos Git vacíos
  o archivos perdidos, lo que actualmente impide una medición pública confiable.
- Ejecutar lote y verificador sobre los 12 bugs una vez reparado el dataset.
