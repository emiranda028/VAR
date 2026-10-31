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

## Retro (día 7)

- Funcionó: el revisor detectó el bug sembrado y encontró 8 problemas reales en el importador (2 graves: fuentes vacías o con la URL rota).
- Falló: el scout marcó las 10 jugadas como confianza baja (las crónicas no describen el contacto). → Ajuste en `.claude/agents/scout-datos.md`: priorizar análisis arbitrales y audios oficiales, buscar una segunda fuente antes de dar por inferido un atributo, y marcar `decision_campo` como inferido si ninguna fuente lo dice.
- Próximas tareas: (1) lote 2 de 25 jugadas, empezando por las 3 manos ya identificadas; (2) separar jugadas reales de sintéticas en la base (columna de origen; requiere aprobar el cambio de esquema).
