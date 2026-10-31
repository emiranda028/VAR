# Tu primer equipo de IA: blueprint para VARgento

> Pensado para tu situación: **menos de 3 h por semana**, **algo de código** y
> **GitHub + Claude Code**. Los agentes ya están creados en `.claude/agents/` y
> las reglas compartidas en `CLAUDE.md`.

---

## 1. Caso de uso recomendado

Pediste "todo". Con menos de 3 h por semana, hacer todo a la vez termina en
nada bien hecho. Así se comparan las opciones:

| Opción | Valor para VARgento | Esfuerzo tuyo | Riesgo | Veredicto |
| --- | --- | --- | --- | --- |
| **Desarrollo del repo** (planificar, programar, testear) | Alto: el backlog ya existe en el README | Bajo: revisás y aprobás | Bajo: tests + ramas | ✅ **v1** |
| **Datos reales de jugadas** | Muy alto: hoy los datos son sintéticos | Medio: hay que verificar fuentes | Medio: datos mal cargados ensucian el modelo | ✅ **v1, en lotes chicos** |
| Contenido / difusión | Medio, recién sirve con datos reales | Alto: cada post requiere tu voz y aprobación | Alto: es público | ⏳ v2 (mes 2) |
| Soporte, leads, operaciones | Bajo hoy: todavía no hay usuarios | — | — | ❌ todavía no |

**Recomendación:** un equipo de desarrollo y datos para VARgento. Se ocupa del
código y además consigue jugadas reales, que es lo que convierte la demo en un
producto. El contenido se suma después, cuando haya datos reales para contar.

**Meta de la versión 1 (4 semanas):** cargar ≥ 100 jugadas reales verificadas y
cerrar 3 tareas del backlog, con ≤ 2,5 h por semana de tu tiempo.

---

## 2. Estructura del equipo (4 agentes)

```
             Vos (dueño: decide y aprueba)
                         │ pedido
                         ▼
                  ┌─────────────┐
                  │ coordinador │  plan + criterios de aceptación
                  └──────┬──────┘
             código ┌────┴────┐ datos
                    ▼         ▼
          ┌──────────────┐ ┌─────────────┐
          │ desarrollador│ │ scout-datos │
          └──────┬───────┘ └──────┬──────┘
                 └───────┬────────┘
                         ▼
                   ┌──────────┐   CAMBIOS (máx. 2 rondas)
                   │ revisor  │ ──────────▶ vuelve al que entregó
                   └────┬─────┘
                        │ APROBADO
                        ▼
              Vos: push / merge / carga de datos
```

| Agente | Es dueño de | Recibe | Entrega | Pasa a |
| --- | --- | --- | --- | --- |
| `coordinador` | Alcance y criterios | Tu pedido en lenguaje natural | Plan de 1 tarea | desarrollador o scout-datos |
| `desarrollador` | Código y tests | Plan | Rama `equipo/…` con commit y resumen | revisor |
| `scout-datos` | Jugadas reales con fuente | Plan de búsqueda | `data/candidatas/*.jsonl` | revisor |
| `revisor` | Calidad | Plan + entrega | APROBADO / CAMBIOS / ESCALAR | vos |

**Cómo se evita que se pisen:**
- Sólo el coordinador decide el alcance. Sólo el desarrollador edita código.
  Sólo el scout escribe en `data/candidatas/`. El revisor no edita nada.
- Las herramientas de cada agente están limitadas en su archivo
  (`tools:`): el coordinador y el revisor no tienen `Edit`/`Write`.
- Nadie hace push, merge ni carga datos en la base: eso lo hacés vos.

---

## 3. Diseño de cada agente

Los prompts completos, listos para usar, están en `.claude/agents/`.
Este es el resumen:

### coordinador — `.claude/agents/coordinador.md`
- **Propósito:** que cada pedido se convierta en una tarea chica y verificable.
- **Responsabilidades:** leer el contexto, partir pedidos grandes, escribir
  criterios de aceptación y asignar la tarea.
- **Reglas de decisión:** una tarea = un objetivo; todo cambio de código lleva
  test; los cambios al modelo se miden con métricas antes/después.
- **Entrada:** pedido libre. **Salida:** bloque "Tarea / Objetivo / Criterios / Fuera de alcance".
- **Éxito:** el desarrollador arranca sin hacer preguntas.
- **Escala si:** la tarea implica borrar datos, cambiar el esquema, sumar
  dependencias, gastar dinero o publicar.

### desarrollador — `.claude/agents/desarrollador.md`
- **Propósito:** implementar el cambio más chico que cumpla los criterios.
- **Responsabilidades:** crear la rama, escribir el test primero, programar,
  correr los tests y hacer commit.
- **Reglas de decisión:** nada fuera de alcance (las ideas extra van a
  "Sugerencias"); nunca desactivar tests.
- **Entrada:** plan. **Salida:** "Entrega" con rama, commit, tests y criterios.
- **Éxito:** tests en verde, diff chico, criterios cubiertos.
- **Escala si:** un criterio es imposible o los tests fallan 3 veces sin explicación.

### scout-datos — `.claude/agents/scout-datos.md`
- **Propósito:** traer jugadas VAR reales, cada una con su fuente.
- **Responsabilidades:** buscar en fuentes públicas, mapear la jugada a los
  atributos de `domain.py` y marcar lo que infirió.
- **Reglas de decisión:** sin URL no hay jugada; sin decisión final clara, se
  descarta; con más de 2 atributos inferidos, la confianza es baja.
- **Entrada:** pedido de búsqueda. **Salida:** archivo `.jsonl` y resumen del lote.
- **Éxito:** el revisor valida ≥ 90% de las jugadas sin cambios.
- **Escala si:** la fuente es dudosa o hay que entrar con login o pagar.

### revisor — `.claude/agents/revisor.md`
- **Propósito:** que te llegue sólo lo que está bien hecho.
- **Responsabilidades:** correr los tests, leer el diff, verificar los criterios
  con evidencia y validar las jugadas contra `validate_features`.
- **Reglas de decisión:** aprueba sólo con tests en verde y todos los criterios
  cumplidos; hace como máximo 2 rondas de cambios.
- **Entrada:** plan + entrega. **Salida:** veredicto con hallazgos `archivo:línea`.
- **Éxito:** decidís en menos de 5 minutos leyendo sólo la revisión.

---

## 4. Flujo de trabajo

1. **Pedido (vos, 2 min):** en Claude Code escribís, por ejemplo,
   *"Usá el coordinador para: agregar un script que importe data/candidatas/*.jsonl"*.
2. **Plan (coordinador):** devuelve la tarea con criterios. Si dice
   "Necesita aprobación: sí", respondés sí o no.
3. **Ejecución:** *"Pasale el plan al desarrollador"* (o al scout-datos).
4. **Revisión:** *"Que el revisor revise la entrega"*.
   - **CAMBIOS** → vuelve al que entregó con los hallazgos (máx. 2 rondas).
   - **ESCALAR** → decidís vos.
5. **Aprobación (vos, 5 min):** leés la revisión y, si estás de acuerdo:
   *"Aprobado: hacé push de la rama y abrí el PR"* o *"Aprobado: cargá el lote"*.
6. **Cierre:** anotás una línea en la planilla de métricas (sección 6).

**Atajo:** con práctica podés pedir todo junto: *"Coordinador → desarrollador →
revisor para: <pedido>. Parar antes del push y mostrarme la revisión."*

**Ritmo semanal (≈ 2–2,5 h):**

| Cuándo | Qué | Tiempo |
| --- | --- | --- |
| Lunes | 1 pedido de código + 1 lote de datos | 15 min |
| Miércoles | Leer revisiones, aprobar o pedir cambios | 30–45 min |
| Viernes | Push/merge o carga de lo aprobado; anotar métricas | 30 min |
| Sobra | Probar la app a mano 10 min | 10 min |

---

## 5. Herramientas (lo mínimo)

| Herramienta | Para qué | Por qué alcanza |
| --- | --- | --- |
| **Claude Code + Opus 5.5** | Corre los 4 agentes (`.claude/agents/`) | Ya lo usás; los subagentes vienen incluidos y no hace falta un framework |
| **GitHub** | Ramas, PRs, historial; es el "gestor de tareas" (Issues) | Cada tarea queda trazable y se puede revertir |
| **Repo como base de conocimiento** | `README.md`, `CLAUDE.md`, `docs/` | Los agentes lo leen solos; no hay que sincronizar nada |
| **`data/candidatas/` (JSONL)** | Planilla de jugadas antes de cargarlas | Se revisa con diff, se valida con código |
| **Planilla de métricas** (Google Sheets o un `docs/equipo-ia/metricas.md`) | Medir si el equipo ayuda | 6 columnas, 1 línea por tarea |
| Comunicación | No hace falta en v1 | Sos un solo operador: el chat de Claude Code alcanza |
| Automatización | No en v1. En el mes 2: GitHub Action que corra `pytest` en cada PR | Primero hay que probar el flujo a mano |

**Sin costo adicional** más allá de tu plan de Claude. Cualquier herramienta paga
se agrega sólo con tu aprobación.

---

## 6. Plan de calidad

### Puntos de control
1. **Plan:** ¿los criterios son comprobables? (lo mirás 1 minuto)
2. **Tests:** el desarrollador y el revisor los corren por separado.
3. **Revisión:** veredicto con evidencia.
4. **Aprobación humana:** nada llega a `main` ni a la base sin tu sí.

### Fallas comunes y cómo se frenan
| Falla | Señal | Defensa |
| --- | --- | --- |
| El agente agranda la tarea | Diff de más de 200 líneas, archivos fuera del plan | "Fuera de alcance" en el plan; el revisor lo marca |
| Tests que "pasan" sin probar nada | Test nuevo sin `assert` relevante | El revisor lo busca explícitamente |
| Datos inventados o mal leídos | Jugada sin URL o con `called` distinto a la fuente | Regla "sin URL no hay jugada"; abrís 3 fuentes al azar por lote |
| El modelo empeora con datos reales | Baja el acierto o sube el log-loss | Métricas antes/después en todo cambio al modelo o carga grande |
| Revisor complaciente | Siempre APROBADO | Semana 1: introducís un bug a propósito y verificás que lo detecte |

### Antes de usarlo en serio (semana 1)
- [ ] Una tarea trivial recorre todo el circuito sin intervención.
- [ ] El revisor detecta un bug sembrado a propósito.
- [ ] El scout trae 5 jugadas y las 5 fuentes se pueden abrir y verificar.
- [ ] Ningún agente intentó hacer push o borrar algo sin preguntar.

### ¿El equipo ayuda de verdad? (planilla, 1 línea por tarea)
`fecha | tarea | min. de tu tiempo | rondas de revisión | aprobada (s/n) | bugs después del merge`

Metas al mes: ≥ 80% de tareas aprobadas en ≤ 1 ronda, ≤ 20 min tuyos por tarea,
0 bugs en `main`, ≥ 90% de jugadas válidas por lote. Si no se cumplen, se
ajustan los prompts y no se suman agentes.

---

## 7. Plan de lanzamiento en 7 días (≈ 20–30 min por día)

| Día | Objetivo | Qué hacés | Listo cuando |
| --- | --- | --- | --- |
| **1** | Instalar | Mergear estos archivos; `pip install -r requirements-dev.txt`; `python -m pytest -q`; en Claude Code, `/agents` para ver los 4 | Ves los 4 agentes y los tests pasan |
| **2** | Primer circuito | Pedido trivial: *"agregar a `GET /api/health` la cantidad de árbitros"* → coordinador → desarrollador → revisor | Revisión APROBADO; mirás el diff |
| **3** | Probar al revisor | En una rama, rompé algo a propósito (ej.: el umbral 60% → 70% sin actualizar el test) y pedí revisión | El revisor lo detecta con archivo:línea |
| **4** | Tarea real 1 | *"Script `python -m vargento.importar data/candidatas/x.jsonl`: valida, crea árbitros por nombre, y por defecto sólo simula (`--aplicar` para cargar)"* | APROBADO; push y PR con tu aprobación |
| **5** | Primer lote de datos | *"scout-datos: 10 penales revisados por VAR en la Liga Profesional Argentina 2025"* → revisor | ≥ 9 de 10 válidas; verificaste 3 fuentes |
| **6** | Cargar y medir | Importás el lote con el script (simulación primero, después `--aplicar` en una base de prueba) y pedís métricas del modelo | Tenés los números antes/después |
| **7** | Retro | Llenás la planilla; ajustás 1 regla en el prompt que más falló; elegís las 2 tareas de la semana 2 | Una mejora concreta en un prompt |

**Semanas 2 a 4:** cada semana, 1 tarea del backlog (registro rápido de la
decisión final, recalibrar el modelo con datos reales, autenticación básica) y
1 lote de 25 a 30 jugadas. **Mes 2:** GitHub Action con `pytest` y, si las metas
se cumplen, un 5º agente de contenido (con aprobación obligatoria antes de
publicar).

---

## START HERE FIRST

1. Aprobá estos archivos (rama con `.claude/agents/`, `CLAUDE.md` y este documento).
2. Abrí Claude Code en el repo y escribí:

   > Usá el **coordinador** para planificar: *agregar a `GET /api/health` la cantidad de árbitros*. Después pasáselo al **desarrollador** y al final al **revisor**. Pará antes de cualquier push y mostrame la revisión.

3. Leé la revisión (5 min). Si dice APROBADO y el diff tiene sentido, ya
   tenés tu primer equipo de IA funcionando. Seguí con el día 3.
