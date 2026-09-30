# VARgento

Herramienta para periodistas deportivos: ante una jugada polémica, VARgento da la
**probabilidad de que se cobre (SÍ) o no (NO)**, basada en jugadas similares del
archivo y en cómo **el árbitro del partido** resolvió jugadas comparables. Muestra
las jugadas parecidas con sus videos para compararlas lado a lado y genera una
**placa** lista para redes o notas.

> Es una estimación estadística: no reemplaza el criterio arbitral. Los datos que
> trae la app son **sintéticos** (árbitros y partidos ficticios), sólo para demo.

## Cómo arrancar

```bash
pip install -r requirements.txt
python -m vargento            # http://127.0.0.1:8000
```

La primera vez crea `vargento.db` (SQLite), la carga con 1600 jugadas de demo y
crea el usuario `admin`. **La contraseña del admin se muestra una sola vez en la
consola** al arrancar (o se fija con `VARGENTO_ADMIN_PASSWORD`). Cambiala desde
"Cambiar clave" al entrar.

Variables de entorno:

- `VARGENTO_DB`: ruta de la base (por defecto `vargento.db`).
- `VARGENTO_SEED_DEMO=0`: arrancar con la base vacía, sin datos de demo (para usar datos reales).
- `VARGENTO_ADMIN_USER` / `VARGENTO_ADMIN_PASSWORD`: usuario y clave del primer admin.
- `VARGENTO_MEDIA_DIR`: carpeta de videos y fotos subidos (por defecto `media`).
- `VARGENTO_SECURE_COOKIES=1`: cookies sólo por HTTPS (activar al publicarla en internet).

## Usuarios y roles

| Rol | Puede |
| --- | --- |
| Periodista | Analizar jugadas, subir videos/fotos, descargar la placa y registrar lo que cobró el árbitro. |
| Editor | Todo lo anterior, más importar datos, gestionar árbitros, agregar videos a jugadas del archivo y recalibrar el modelo. |
| Administrador | Todo lo anterior y gestionar usuarios. |

Los usuarios creados con roles de versiones anteriores (árbitro, operador VAR,
analista) se convierten solos: los dos primeros en periodista y analista en editor.

Las contraseñas se guardan con scrypt, las sesiones duran 12 horas y un usuario
se bloquea 5 minutos tras 5 intentos fallidos. Si se pierde la clave del admin:

```bash
python -m vargento.users clave admin      # también: crear USUARIO periodista|editor|admin, listar
```

## Flujo de uso

1. **Análisis**: se cargan los atributos de la jugada (y su video o foto) y se ve
   la probabilidad SÍ/NO: **SE COBRA**, **NO SE COBRA** o **DIVIDIDA**.
2. **Placa**: "Descargar placa" genera una imagen 1200×675 con el resultado, para
   redes, TV o la nota.
3. **¿Qué cobró el árbitro?**: cuando se conoce la decisión real, se registra con
   "Lo cobró" / "No lo cobró". La jugada pasa al archivo y el modelo aprende.
4. **Historial**: muestra los análisis y qué tan seguido acertó VARgento.

## Videos y fotos

- **Jugada nueva**: en el formulario, "Video o foto de la jugada" permite subir un
  archivo (JPG, PNG, WEBP, GIF, MP4, WEBM o MOV, hasta 300 MB) o pegar un enlace
  (YouTube, Vimeo, o un `.mp4`/`.jpg` público) con el segundo donde empieza la jugada.
  También se puede agregar después, desde el resultado.
- **Comparar**: tocando cualquier jugada parecida se abre una vista lado a lado
  con los dos videos y la tabla de atributos, resaltando lo que cambia.
- **Histórico**: al registrar la decisión final, el video pasa con la jugada al
  histórico. Los editores pueden agregar videos a jugadas viejas desde la
  comparación, o en el CSV con las columnas `video` y `video_inicio`.
- Los archivos se guardan en la carpeta `media/` (o `VARGENTO_MEDIA_DIR`) y sólo
  los ven usuarios con sesión iniciada.

> **Derechos de imagen**: las imágenes de TV de los partidos tienen dueño. Para uso
> comercial conviene enlazar a videos publicados oficialmente (YouTube de la liga,
> de los clubes, etc.) en vez de subir copias.

## Cargar datos reales

Desde la pestaña **Datos** (o por consola) se importa un CSV con una fila por
jugada revisada. La plantilla se descarga desde la app o con:

```bash
python -m vargento.importer --plantilla > plantilla.csv
python -m vargento.importer jugadas.csv --probar   # sólo valida
python -m vargento.importer jugadas.csv            # importa
```

Columnas: `tipo`, `arbitro`, `cobrado` (sí/no), opcionales `pais_arbitro`,
`fecha` (AAAA-MM-DD), `partido`, `descripcion`, y una columna por atributo del
tipo de jugada (ver la tabla de abajo). Acepta `,` o `;` y archivos de Excel con
tildes. Si alguna fila tiene errores no se importa nada y se listan todos.

Después de importar, **Recalibrar** (en Datos, o `python -m vargento.evaluate
--calibrar`) busca los parámetros del modelo que mejor predicen esos datos y
muestra el acierto y la calibración ("cuando dice 70%, ¿se cobra 70%?").

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

El resultado es **SE COBRA** (≥ 60%), **NO SE COBRA** (≤ 40%) o **DIVIDIDA**, con un
nivel de confianza según cuánta evidencia hay. La pantalla muestra las jugadas
parecidas que respaldan el número, para que el árbitro pueda verlas.

En los datos de demo (validación con 600 jugadas separadas) acierta ~80%, y el
ajuste por árbitro mejora el log-loss de 0,445 a 0,432. Los parámetros están al
principio de `model.py` (`ModelParams`) y se recalibran con datos reales desde la app.

## API

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `/api/play-types` | Tipos de jugada y sus atributos |
| GET/POST | `/api/referees` | Listar / crear árbitros |
| GET | `/api/referees/{id}/stats` | Tasa de cobro del árbitro por tipo vs. promedio |
| POST | `/api/auth/login`, `/api/auth/logout`, `/api/auth/password` | Sesión (cookie o `Authorization: Bearer`) |
| GET/POST | `/api/plays` | Listar / registrar jugadas con su decisión final |
| GET | `/api/plays/template` | Plantilla CSV |
| POST | `/api/plays/import` | Importar CSV (`{"csv": "...", "dry_run": true}`) |
| POST | `/api/predict` | Probabilidad de cobro; crea una revisión (`review_id`) |
| POST | `/api/reviews/{id}/decision` | Registrar la decisión final |
| GET | `/api/reviews`, `/api/reviews/stats` | Historial y acierto en revisiones reales |
| POST | `/api/model/evaluate`, `/api/model/calibrate` | Evaluar / recalibrar el modelo |
| GET | `/api/plays/{id}`, `/api/reviews/{id}` | Detalle de una jugada / revisión, con sus videos |
| POST | `/api/media/upload?review_id=…` o `?play_id=…` | Subir video/foto (el cuerpo es el archivo, con su `Content-Type`) |
| POST | `/api/media/link` | Adjuntar un enlace (`url`, `start_seconds`, `review_id` o `play_id`) |
| GET/DELETE | `/api/media/{id}/file`, `/api/media/{id}` | Ver / borrar un archivo |
| GET/POST/PATCH | `/api/users` | Gestión de usuarios (admin) |

Ejemplo:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'content-type: application/json' \
  -d '{"username":"admin","password":"TU_CLAVE"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -X POST localhost:8000/api/predict -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "play_type": "offside",
  "referee_id": 1,
  "features": {"decision_campo": false, "distancia_cm": 12,
               "interfiere_juego": true, "jugada_deliberada_rival": false}
}'
```

Documentación interactiva en `/docs`.

## Próximos pasos

- Conseguir y cargar datos reales de revisiones VAR (informes arbitrales, revisiones publicadas).
- Publicarla detrás de HTTPS (con `VARGENTO_SECURE_COOKIES=1`) si se va a usar fuera de una red local.
- Si el histórico crece mucho (decenas de miles de jugadas), indexar la búsqueda de similares.
