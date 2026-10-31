# VARgento — reglas para Claude y el equipo de agentes

App FastAPI + SQLite que da la probabilidad de cobrar una jugada revisada por el
VAR. Ver `README.md`. Plan del equipo de IA: `docs/equipo-ia/BLUEPRINT.md`.

## Comandos
- Instalar: `pip install -r requirements-dev.txt`
- Tests: `python -m pytest -q` (tienen que pasar antes de cualquier entrega)
- Correr la app: `python -m vargento` (http://127.0.0.1:8000)

## Equipo (`.claude/agents/`)
Flujo: `coordinador` → `desarrollador` o `scout-datos` → `revisor` → dueño aprueba.
Todo pedido nuevo pasa primero por `coordinador`.

## Reglas que no se negocian
- Pedir aprobación al dueño antes de: `git push`, merge a `main`, publicar,
  enviar mensajes, borrar archivos o datos, cambiar el esquema de la base,
  agregar dependencias, gastar dinero o hacer cualquier cosa irreversible.
- Nunca pedir ni escribir contraseñas, tokens o claves en el chat ni en el código.
- Nunca saltear, borrar ni marcar como `skip` un test para que pase.
- Trabajo en ramas `equipo/<tarea>`; `main` sólo se toca con aprobación.
- Alcance chico: una tarea por vez, con criterios de aceptación comprobables.
- Textos, comentarios y mensajes de commit en español.
