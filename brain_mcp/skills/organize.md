---
name: organize
description: Ordena el vault y su grafo (carpetas, nombres, tags, relaciones, duplicados) preguntándole al usuario antes de tocar nada. Se activa con /organize en el chat.
source: Adaptado de "file-organizer" de claude-code-templates (https://github.com/davila7/claude-code-templates/blob/main/cli-tool/components/skills/productivity/file-organizer/SKILL.md)
license: MIT, Copyright (c) 2025 Daniel (San) Ávila. Adaptación para brain.
---
# Organizar el vault (/organize)

Estás ayudando al usuario a ordenar su vault de brain: notas en Markdown que se ven como un grafo
(carpetas, `[[links]]`, `tags`, `related` y entidades). El objetivo es que encuentre las cosas y que
el grafo muestre relaciones reales. Abajo tenés un **panorama del vault** calculado ahora.

## Cómo trabajar

1. **Entender el alcance (primer mensaje).** Contá en 3-5 líneas qué ves en el panorama (cuántas
   notas, qué carpetas, cuántas sueltas, tags repetidos, posibles duplicados). Después hacé **2-4
   preguntas concretas**, numeradas, y esperá la respuesta. Por ejemplo:
   - ¿Qué parte querés ordenar? (todo, `projects/`, las notas sueltas de la raíz…)
   - ¿Cuál es el problema principal? (no encontrás cosas, duplicados, tags desordenados, grafo sin conexiones)
   - ¿Hay algo que no toque? (proyectos en curso, notas sensibles)
   - ¿Qué tan a fondo? (conservador: solo tags y relaciones · completo: también mover y renombrar)
   No hagas ningún cambio en este paso.

2. **Analizar.** Con las respuestas, mirá lo que haga falta: `list_vault`, `read_file`, `vault_overview`
   (vuelve a calcular el panorama). Leé una nota antes de proponer moverla o unirla.

3. **Proponer un plan** en Markdown, antes de cambiar nada:
   - **Estado actual**: números y problemas.
   - **Estructura propuesta**: árbol de carpetas (usá las del vault: `projects/`, `skills/`,
     `knowledge/`, notas propias en carpetas con nombre claro).
   - **Cambios**, agrupados: mover/renombrar (`origen → destino`), tags a unificar (`#ia, #AI → #ia`),
     relaciones a agregar (`related` o `[[links]]`), descripciones que faltan, duplicados.
   - **Para decidir vos**: lo que no tengas claro.
   Terminá con: "¿Avanzo? (sí / no / cambiá algo)". Esperá la confirmación.

4. **Ejecutar** solo lo aprobado, de a un cambio por tool:
   - Mover o renombrar: `move_file(src, dst)`.
   - Tags, relaciones, descripción: `set_frontmatter(path, {...})` (no reescribas el archivo).
   - Links en el texto: `str_replace_file`.
   - Borrar un duplicado: **solo si el usuario lo confirmó para ese archivo**, con `delete_file`.
   Si una tool devuelve Error, frená, contá qué pasó y preguntá cómo seguir.

5. **Resumen final**: qué cambió (con cantidades), la estructura nueva y 2-3 hábitos para mantenerlo
   (por ejemplo: una descripción por nota, reusar tags existentes, `related` al crear algo nuevo).
   Recordá que todo se puede deshacer con `file_history` + `restore_file`.

## Reglas

- Nunca borres ni muevas sin confirmación explícita. Ante la duda, preguntá.
- No toques `BRAIN.md` (salvo para actualizar su índice si movés carpetas), `profile.md`,
  `memory/` ni `knowledge/sources/`: esos los maneja brain.
- Nombres: minúsculas con guiones (`notas-reunion-2026-09.md`), claros y específicos
  ("propuestas-clientes", no "docs"). Fechas como `AAAA-MM-DD`. Sin "final-v2 (1)".
- Tags: pocos y reusados; en minúscula; singular; sin sinónimos duplicados.
- Relaciones: `related: [nombre]` en el frontmatter o `[[nombre]]` en el texto conectan notas en el
  grafo. Una nota sin conexiones es candidata a relacionar o archivar.
- Archivar antes que borrar: lo viejo que el usuario no quiere perder va a `archivo/`.
- Si movés o renombrás una nota que otras linkean por path, actualizá esos links.
