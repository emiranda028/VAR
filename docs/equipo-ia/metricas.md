# Métricas del equipo de IA

Una línea por tarea. Metas del mes: ≥ 80% de tareas aprobadas en ≤ 1 ronda,
≤ 20 min del dueño por tarea, 0 bugs en `main`, ≥ 90% de jugadas válidas por lote.

| Fecha | Tarea | Min. del dueño | Rondas de revisión | Aprobada | Bugs después del merge |
| --- | --- | --- | --- | --- | --- |
| 2026-10-31 | Blueprint y agentes (PR #2) | 5 | 0 | sí | 0 |
| 2026-10-31 | `/api/health` con cantidad de árbitros | 0 | 1 | sí | — |
| 2026-10-31 | Importador de jugadas reales | 0 | 2 (8 hallazgos corregidos) | sí | — |
| 2026-10-31 | Prueba del revisor con bug sembrado (día 3) | 0 | 1 | detectado ✅ | — |
| 2026-10-31 | Lote 1: 10 penales LPF 2024-2025 | 0 | — | 10/10 válidas, 10/10 confianza baja | — |
| 2026-10-31 | Columna `origen` + carga automática de `data/aprobadas/` (PR #4) | 2 | 1 | sí | — |
| 2026-10-31 | Lote 2: 23 jugadas → 15 cargadas (8 offside sin medida, apartados) | 3 | 1 | sí, 2 fechas corregidas | — |

## Lote 1: verificación y modelo (día 6)

- Fuentes verificadas a mano: 3 de 10 (River-Gimnasia, River-Banfield ×2, Riestra-River). Las tres coinciden en árbitro, decisión final y lo ocurrido en el VAR.
- Base de prueba: 1600 jugadas sintéticas + 10 reales (no se tocó ninguna base de producción).

| Modelo evaluado sobre las 10 jugadas reales | Acierto | Log-loss |
| --- | --- | --- |
| Antes: sólo datos sintéticos | 7/10 | 0,448 |
| Después: sintéticos + las otras 9 reales (leave-one-out) | 7/10 | 0,436 |

Lectura: con 10 jugadas el cambio no es significativo. Los 3 errores son penales
que se cobraron por intervención del VAR sin haberse cobrado en el campo
(Racing-Tigre, Central Córdoba-San Lorenzo, River-Banfield): en los datos
sintéticos la decisión de campo pesa mucho y el modelo subestima esas
correcciones. Hace falta más volumen real antes de recalibrar.

## Lote 2 (cargado)

- 15 jugadas: 7 manos, 3 penales, 5 rojas; 9/15 confianza media (meta ≥ 50% ✅).
- El revisor verificó 8 contra sus fuentes: `called` y `decision_campo` coinciden en todas.
- Corregido: Barracas-Huracán 2025-11-17 → 2025-11-16 (fecha de publicación tomada como fecha del partido).
- Apartados: 8 offside con `distancia_cm` estimada → `data/candidatas/2026-10-31-lote2-offside-sin-medida.jsonl`.
- Pendiente: abrir a mano la nota de El Gráfico de Central Córdoba-Boca (Cloudflare la bloquea).

| Modelo evaluado sobre las 25 jugadas reales | Acierto | Log-loss |
| --- | --- | --- |
| Sólo datos demo | 15/25 | 0,589 |
| Demo + las otras reales (leave-one-out) | 17/25 | 0,565 |

Lectura: con jugadas reales el modelo acierta bastante menos que con las de demo (~80%).
Los datos sintéticos no representan bien al VAR real; es la señal más clara hasta ahora
de que hace falta recalibrar con datos reales cuando haya ~100.

## Lote 3 (cargado)

- 16 jugadas: 12 penales, 1 mano, 3 rojas; 9/16 confianza media.
- Revisor: APROBADO, 10 de 16 verificadas contra sus fuentes, sin contradicciones en `called` ni `decision_campo`.
- Sin offside: en la LPF 2024-2025 no se publican medidas de la línea. Hasta que llegue el offside semiautomático, los próximos lotes se enfocan en penales, manos y rojas.
- Medido con `python -m vargento.evaluar` (41 jugadas reales):

| Variante | Total | Penal | Mano | Roja | Log-loss |
| --- | --- | --- | --- | --- | --- |
| Sólo demo | 24/41 (58,5%) | 15/25 | 7/8 | 2/8 | 0,646 |
| Leave-one-out | 28/41 (68,3%) | 16/25 | 7/8 | 5/8 | 0,607 |

Lectura: los penales reales son el punto débil (60-64%) y las rojas mejoran mucho al sumar datos reales
(2/8 → 5/8). Las manos ya funcionan bien. Con ~60 jugadas reales conviene recalibrar los parámetros del modelo.

## Retro (día 7)

- Funcionó: el revisor detectó el bug sembrado y encontró 8 problemas reales en el importador (2 graves: fuentes vacías o con la URL rota).
- Falló: el scout marcó las 10 jugadas como confianza baja (las crónicas no describen el contacto). → Ajuste en `.claude/agents/scout-datos.md`: priorizar análisis arbitrales y audios oficiales, buscar una segunda fuente antes de dar por inferido un atributo, y marcar `decision_campo` como inferido si ninguna fuente lo dice.
- Próximas tareas: (1) lote 2 de 25 jugadas, empezando por las 3 manos ya identificadas; (2) separar jugadas reales de sintéticas en la base (columna de origen; requiere aprobar el cambio de esquema).
