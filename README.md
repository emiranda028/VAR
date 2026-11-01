# VARgento

Cuando el árbitro va a revisar una jugada al monitor del VAR, VARgento le da la
**probabilidad de cobrar (SÍ) o no cobrar (NO)**. El cálculo se basa en jugadas
similares del histórico y en cómo **ese mismo árbitro** resolvió jugadas
comparables.

> Es una herramienta de apoyo: la decisión es siempre del árbitro. Los datos que
> trae la app son **sintéticos** (árbitros y partidos ficticios), sólo para demo.

## Cómo arrancar

```bash
pip install -r requirements.txt
python -m vargento            # http://127.0.0.1:8000
```

La primera vez crea `vargento.db` (SQLite) y la carga con 1600 jugadas de demo.
Variables de entorno:

- `VARGENTO_DB`: ruta de la base (por defecto `vargento.db`).
- `VARGENTO_SEED_DEMO=0`: arrancar sin datos de demo (los lotes aprobados se cargan igual).
- `VARGENTO_APROBADAS`: carpeta de lotes aprobados que se cargan al arrancar (por
  defecto `data/aprobadas/`; vacía desactiva la carga).

Tests:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Tipos de jugada

Definidos en `vargento/domain.py`. Cada uno tiene sus atributos, con un peso que
indica cuánto importan al comparar jugadas:

| Tipo | Atributos |
| --- | --- |
| Penal por falta | decisión de campo, intensidad del contacto, toca pelota primero, intenta jugar la pelota, indicios de simulación, contacto dentro del área |
| Mano en el área | decisión de campo, brazo antinatural, movimiento hacia la pelota, rebote propio, distancia del remate, brazo de apoyo |
| Fuera de juego | decisión de campo, distancia adelantado (cm), interfiere en el juego, jugada deliberada de un rival |
| Roja directa | decisión de campo, intensidad, con tapones, pierna extendida, DOGSO, disputa de la pelota |

## Cómo se calcula la probabilidad (`vargento/model.py`)

1. **Similitud**: distancia euclídea ponderada entre atributos normalizados y un
   kernel gaussiano (100% = jugada idéntica).
2. **Probabilidad general**: promedio ponderado por similitud de la decisión final
   de las 30 jugadas más parecidas, suavizado hacia el promedio del tipo cuando
   hay poca evidencia.
3. **Ajuste por árbitro**: en un vecindario más amplio de jugadas comparables se
   mide cuánto cobra el árbitro frente a sus colegas **en esas mismas jugadas**
   (diferencia en log-odds con un prior bayesiano). Ese sesgo se aplica sobre la
   probabilidad general. Con pocas jugadas del árbitro, casi no mueve el
   resultado.

El resultado es **COBRAR** (≥ 60%), **NO COBRAR** (≤ 40%) o **DUDOSA**, con un
nivel de confianza según cuánta evidencia hay. La pantalla muestra las jugadas
parecidas que respaldan el número, para que el árbitro pueda verlas.

En los datos de demo (validación con 600 jugadas separadas) acierta ~80%, y el
ajuste por árbitro mejora el log-loss de 0,445 a 0,432. Los parámetros están al
principio de `model.py` y conviene recalibrarlos con datos reales.

## API

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `/api/play-types` | Tipos de jugada y sus atributos |
| GET/POST | `/api/referees` | Listar / crear árbitros |
| GET | `/api/referees/{id}/stats` | Tasa de cobro del árbitro por tipo vs. promedio |
| GET/POST | `/api/plays` | Listar / registrar jugadas con su decisión final |
| POST | `/api/predict` | Probabilidad de cobro para una jugada |
| GET | `/api/health` | Estado, versión y conteos: `plays`, `plays_demo`, `plays_real`, `referees` |

Cada jugada tiene `origen`: `demo` (datos sintéticos del seed) o `real` (importada
o registrada por `POST /api/plays`). `GET /api/plays` lo devuelve en cada ítem. Al
abrir una base vieja se agrega la columna sola: las jugadas con fuentes
(`| Fuentes: http...`) quedan como `real` y el resto como `demo`.

Ejemplo:

```bash
curl -X POST localhost:8000/api/predict -H 'content-type: application/json' -d '{
  "play_type": "offside",
  "referee_id": 1,
  "features": {"decision_campo": false, "distancia_cm": 12,
               "interfiere_juego": true, "jugada_deliberada_rival": false}
}'
```

Documentación interactiva en `/docs`.

## Importar jugadas reales

Las jugadas reales se preparan en `data/candidatas/*.jsonl` (una jugada por línea,
con fuentes) y se cargan con:

```bash
python -m vargento.importar data/candidatas/lote.jsonl            # sólo simula
python -m vargento.importar data/candidatas/lote.jsonl --aplicar  # carga en la base
```

Valida cada jugada contra `domain.py`, exige al menos una fuente, crea los árbitros
que falten, omite duplicadas y deja afuera las de confianza baja (salvo
`--incluir-baja`). El formato está en `.claude/agents/scout-datos.md`.

`data/aprobadas/` contiene **sólo lotes aprobados por el dueño** (copias de
candidatas revisadas). Al arrancar, la app carga cada `*.jsonl` de esa carpeta en
orden alfabético, incluidas las de confianza baja; las ya cargadas se omiten como
duplicadas, así que reiniciar no duplica nada. Si un lote falla, se registra un
aviso y la app arranca igual.

## Evaluar el modelo

Mide qué tan bien predice el modelo las jugadas con `origen = 'real'`:

```bash
python -m vargento.evaluar                  # demo (seed) + data/aprobadas/, en memoria
python -m vargento.evaluar --db vargento.db # evalúa una base existente (no la modifica)
python -m vargento.evaluar --json           # sólo JSON por stdout, floats sin redondear
```

Muestra, en total y por tipo, acierto (cobrar si P ≥ 50%), log-loss (con la
probabilidad recortada a [0,001; 0,999]) y tasa de DUDOSA, en dos variantes:

- **Sólo demo**: cada jugada real se predice con el histórico sin ninguna jugada
  real. Mide cuánto sirven los datos sintéticos para casos reales.
- **Leave-one-out**: cada jugada real se predice con todo el histórico menos ella
  misma (incluye las demás reales). Mide cuánto aportan las reales.

Si `--db` no existe, sale con código 2 sin crear el archivo; si no hay jugadas
reales, sale con código 1.

## Equipo de IA

El proyecto tiene un equipo de agentes de Claude Code (`.claude/agents/`):
coordinador, desarrollador, scout-datos y revisor. Plan y guía de uso en
`docs/equipo-ia/BLUEPRINT.md`.

## Próximos pasos

- Cargar datos reales (informes arbitrales, revisiones VAR publicadas) con `POST /api/plays`.
- Recalibrar los parámetros del modelo y medir calibración con datos reales.
- Autenticación y roles (árbitro, operador VAR, analista).
- Registro rápido de la decisión final tras cada revisión para que el histórico crezca solo.
