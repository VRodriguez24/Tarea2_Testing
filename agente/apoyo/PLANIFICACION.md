# Plan de implementación para dos integrantes

Este documento es material de coordinación. El agente evaluado conserva las firmas y salidas
definidas en `contratos.py`, `main.py`, `localizador.py` y `reparador.py`.

## Contrato congelado

No cambiar durante el trabajo paralelo:

- `localizar(carpeta, info) -> {"regiones": [...]}`.
- `reparar(carpeta, info, localizacion) -> {"resuelto": bool}`.
- Las rutas de las regiones y ediciones son relativas a `repo/`.
- `inicio` y `fin` parten en 1 y son inclusivos.
- Solo se modifica código fuente; nunca tests, fixtures ni `conftest.py`.
- Las dos etapas registran las respuestas del LLM mediante `obtener_metricas()`.
- `main.py` sigue siendo el único encargado de escribir los dos JSON finales.
- Modelo único: `gemini-3.5-flash-lite`.

## Integrante 1 — reparación, seguridad y orquestación (Vicente)

Responsabilidades principales:

1. Infraestructura compartida
   - reloj monotónico con límite interno de 170 segundos;
   - procesos en grupos independientes y terminación por timeout;
   - cliente Gemini con backoff para 429/503;
   - extracción de `usage_metadata` y actualización de `Metricas`;
   - respuestas JSON y manejo de errores permanentes o inválidos.
2. Selección del contexto para reparar
   - leer y numerar las regiones entregadas;
   - incluir fallo inicial y código relevante de tests;
   - recortar funciones grandes y controlar el tamaño del prompt;
   - impedir el envío del repositorio completo.
3. Formato y seguridad del parche
   - pedir ediciones JSON por archivo y rango inclusivo;
   - validar rutas absolutas, `..`, symlinks y permanencia dentro de `repo/`;
   - rechazar tests, entornos, archivos no Python y rangos superpuestos;
   - limitar cantidad de archivos y tamaño del cambio;
   - respaldar archivos antes de escribir y validar con AST;
   - revertir exactamente ante cualquier fallo.
4. Verificación y reintentos
   - ejecutar tests objetivo después de cada cambio;
   - ejecutar `modulos_test` para regresión cuando el objetivo pase;
   - revertir regresiones y realizar un segundo intento;
   - incluir diff y salida del fallo como retroalimentación;
   - conservar solo un parche sintácticamente válido y verificado.
5. Orquestación robusta
   - garantizar ambos JSON mediante `try/finally`;
   - respetar el máximo de tres respuestas del LLM por bug;
   - reservar tiempo para escribir resultados antes de 180 segundos;
   - producir métricas con exactamente los seis campos exigidos.
6. Pruebas propias
   - rutas maliciosas y symlinks;
   - solapamientos y errores de sintaxis;
   - rollback byte a byte;
   - 429/503 y JSON inválido;
   - timeout de procesos y presupuesto agotado;
   - primer parche fallido, regresión y segundo parche correcto.

Entregables de esta parte sugeridos:

- `infraestructura.py`: reloj, procesos, Gemini y métricas.
- `edicion_segura.py`: validación, aplicación y rollback.
- implementación de `reparador.py` y robustecimiento de `main.py`.
- pruebas bajo `apoyo/tests/` mientras se desarrolla.

## Integrante 2 — localización y señales (Josemi)

Responsabilidades:

1. Ejecutar `run_test.sh` y capturar código, traceback y mensajes de aserción.
2. Extraer frames del repositorio, excluyendo tests, `env/` y dependencias.
3. Leer únicamente archivos declarados en `archivos_test`.
4. Obtener mediante AST imports, llamadas, clases, métodos y funciones relevantes.
5. Buscar definiciones de esos símbolos en código fuente.
6. Expandir líneas candidatas a límites de función, método o clase.
7. Puntuar señales deterministas y generar entre una y cinco regiones.
8. Consultar Gemini para reordenar o complementar candidatos.
9. Validar rutas, rangos, tamaño y exclusión de tests.
10. Mantener un fallback determinista si la API falla o devuelve JSON inválido.

Entregables de esta parte sugeridos:

- implementación completa de `localizador.py`;
- utilidades propias de AST/búsqueda si son necesarias;
- pruebas de traceback, ranking, límites AST y recorte de contexto.

## Trabajo independiente e integración

Para que ninguno bloquee al otro:

- Integrante 1 usa `localizacion_ejemplo.json` y luego localizaciones manuales de bugs reales.
- Integrante 2 usa una instancia falsa de `Metricas`/cliente y prueba solo la salida del localizador.
- Cada componente se prueba por su comando individual antes de integrarse.
- No se importan funciones internas del módulo del otro integrante.
- La primera integración solo comprueba tipos, JSON, rutas y flujo; después se mide efectividad.

Puntos de integración diarios:

1. Mediodía: actualizar contrato, ejecutar pruebas rápidas y resolver incompatibilidades.
2. Final del día: ejecutar un bug representativo de Black, FastAPI y Scrapy.

## Orden recomendado

### Día 1

- Instalar Python 3.14, Python 3.8 y tres entornos representativos.
- Probar la API sin imprimir la clave.
- Implementar en paralelo localización e infraestructura/reparación.
- Integrar un flujo completo al final del día.

### Día 2

- Desarrollo: siete bugs públicos.
- Validación reservada: cinco bugs que no se inspeccionan manualmente.
- Ajustar solo reglas generales, nunca nombres de proyectos, funciones o tests concretos.
- Registrar bug, etapa de fallo, tiempo, llamadas y tokens.

### Día 3

- Congelar contratos y prompts.
- Ejecutar verificador sobre reparaciones exitosas.
- Ejecutar lote final y revisar todos los JSON y diffs.
- Preparar video con un éxito completo y un fallo clasificado por etapa.

## Lista de aceptación

- `python main.py <bug>` termina sin intervención y antes de 180 segundos.
- Siempre existen `localizacion.json` y `metricas.json` válidos.
- La localización contiene código fuente y usa evidencia de tests.
- Ningún cambio toca tests ni sale de `repo/`.
- El parche conservado pasa objetivo y verificación de regresión.
- Los fallos de API o del modelo no derriban el proceso.
- Las métricas cuentan cada respuesta y sus tokens reales.
- No existe lógica específica de los bugs públicos o de sus proyectos.

## Preparación local

`config.env` ya contiene la clave y no debe subirse ni mostrarse. La dependencia necesaria ya
está declarada en `requirements.txt`:

```text
google-genai>=1.30
```

Antes de implementar:

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
set -a; source config.env; set +a
```
