---
title: BravoBot
emoji: 🎓
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# BravoBot

**Asistente oficial de la Institución Universitaria Pascual Bravo**, disponible 24/7 para aspirantes. Responde sobre oferta académica, admisiones, costos, perfiles y beneficios usando únicamente información pública del sitio `pascualbravo.edu.co`.

Construido con **RAG (Retrieval-Augmented Generation)**: combina búsqueda sobre el sitio oficial con un modelo de lenguaje para dar respuestas precisas, citadas y sin alucinaciones.

> Este repo contiene solo el **backend de IA**. El scraping y el frontend los entrega un tercero.

---

## ¿Qué hace?

- Responde preguntas frecuentes de aspirantes en lenguaje natural:
  - *"¿Qué tecnologías ofrece Pascual Bravo?"*
  - *"¿Cuánto cuesta Ingeniería Mecánica?"*
  - *"¿Qué requisitos necesito para inscribirme?"*
  - *"¿Qué diferencia hay entre Ingeniería Industrial y Mecánica?"*
- **Siempre cita la fuente**: cada respuesta incluye las URLs de `pascualbravo.edu.co` de donde salió la información.
- **Nunca inventa**: si la información no está en el corpus oficial, lo dice claramente y redirige al sitio/contacto institucional.
- Mantiene **memoria de conversación** para entender preguntas de seguimiento ("¿y el costo?", "¿esa misma carrera tiene nocturno?").

---

## Arranque rápido (5 minutos)

### Requisitos

- **Docker** + **Docker Compose**
- **Python 3.11 o 3.12** (recomendado — en 3.14 algunas dependencias de ML pueden no tener wheels aún)
- Al menos una API key de LLM. **Groq** es la más sencilla: [https://console.groq.com](https://console.groq.com) ofrece un tier gratuito generoso.

### Paso a paso

```bash
# 1. Clonar
cd datahack

# 2. Crear entorno Python e instalar dependencias
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[dev]"

# 3. Configurar variables
cp .env.example .env
# Abre .env y pega tu GROQ_API_KEY

# 4. Levantar PostgreSQL con pgvector (schema se crea automáticamente)
# El contenedor usa el puerto 5433 para evitar conflicto con Postgres local
docker compose up -d

# 5. Cargar datos de ejemplo (o los del equipo de scraping)
python scripts/ingest.py scripts/sample_data/

# 6. Arrancar el API
uvicorn app.main:app --reload --port 8000
```

Abre [http://localhost:8000/docs](http://localhost:8000/docs) y prueba los endpoints desde el navegador.

---

## Probarlo

**Pregunta típica de aspirante:**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": null, "question": "¿Qué tecnologías ofrece Pascual Bravo?"}'
```

Respuesta (abreviada):

```json
{
  "session_id": "6d1a...",
  "turn_id": 1234,
  "answer": "Pascual Bravo ofrece la Tecnología en Sistematización de Datos [1] y la Tecnología en Electricidad, entre otras. Ambas duran 6 semestres [1][2].",
  "citations": [
    { "id": 1, "url": "https://pascualbravo.edu.co/pregrados/tecnologia-en-sistematizacion-de-datos/", "title": "Tecnología en Sistematización de Datos", "snippet": "..." }
  ],
  "confident": true
}
```

> `turn_id` identifica esta respuesta del bot. El frontend lo reenvía al endpoint `/feedback` cuando el usuario marca pulgar arriba/abajo o "esto no respondió mi pregunta" (ver sección *Feedback loop* más abajo).

**Pregunta fuera de dominio:** el bot no inventa.

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": null, "question": "¿Cómo cocino un arroz con pollo?"}'
```

Respuesta:

```json
{
  "answer": "No tengo esa información en los datos oficiales del Pascual Bravo. Te recomiendo consultar https://pascualbravo.edu.co o contactar a la institución...",
  "citations": [],
  "confident": false
}
```

**Conversación con memoria:** reutiliza el `session_id` para preguntas de seguimiento.

```bash
# Primera pregunta
curl -sX POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id": null, "question": "Háblame de Ingeniería Mecánica"}'

# Seguimiento (usando el session_id de la respuesta anterior)
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id": "PEGA-AQUI-EL-UUID", "question": "¿Y cuánto cuesta?"}'
```

---

## Contrato con el equipo de scraping

El scraper entrega **JSON**. El normalizador acepta tanto el **formato simple** (un objeto básico) como el **formato real** con metadata rica; en ambos casos el normalizador produce un `NormalizedDocument` canónico.

### Aliases aceptados

| Campo canónico | Aliases aceptados |
|---|---|
| `url` | `url`, `link`, `href`, `pageUrl`, `page_url`, `source`, `source_url` |
| `title` | `title`, `titulo`, `heading`, `name`, `nombre`, `program_title` |
| `content` | `content`, `body`, `text`, `contenido`, `html`, `markdown`, `raw_text`, `presentation`, `presentacion` |
| `category` | `category`, `categoria`, `section`, `seccion`, `tipo`, `type`, `item_type` |

Las categorías se **canonicalizan** automáticamente: `pregrados → pregrado`, `especialización → posgrado`, `becas → beneficios`, `matrícula → costos`, etc.

### Formato real del scraper (recomendado)

Además del cuerpo narrativo (`presentation`), el normalizador aprovecha estos campos estructurados cuando vienen:

| Campo | Uso |
|---|---|
| `faculty` | Facultad — se inserta en `## Información general` |
| `modalidad` | "Presencial", "Presencial y virtual", etc. |
| `program_title` | Título otorgado ("Ingeniero(a) de Materiales") |
| `summary` | SNIES + registro calificado |
| `inscriptions` | Período de inscripciones → `## Inscripciones` |
| `class_start` | Inicio de clases → `## Inscripciones` |
| `price_table` | Array `[{Estrato, Valor}, ...]` → `## Costos de matrícula por estrato` |

Ejemplo (recortado):

```json
{
  "title": "Ingeniería de Materiales",
  "link": "https://pascualbravo.edu.co/pregrados/ingenieria-de-materiales/",
  "category": "pregrados",
  "faculty": "Facultad de Ingeniería",
  "modalidad": "Presencial - Alta Calidad",
  "program_title": "Ingeniero (a) de Materiales",
  "summary": "SNIES 102345 — Registro calificado vigente.",
  "inscriptions": "Del 2 de marzo al 15 de junio de 2026",
  "class_start": "Agosto de 2026",
  "price_table": [
    { "Estrato": "1", "Valor": "$1.800.000" },
    { "Estrato": "3", "Valor": "$2.500.000" }
  ],
  "presentation": "El programa forma profesionales capaces de..."
}
```

Salida (el normalizador compone automáticamente):

```markdown
## Información general
- Título otorgado: Ingeniero (a) de Materiales
- Facultad: Facultad de Ingeniería
- Modalidad: Presencial - Alta Calidad
- Información oficial: SNIES 102345 — Registro calificado vigente.

## Presentación
El programa forma profesionales capaces de...

## Inscripciones
- Período de inscripciones: Del 2 de marzo al 15 de junio de 2026
- Inicio de clases: Agosto de 2026

## Costos de matrícula por estrato
- Estrato 1: $1.800.000
- Estrato 3: $2.500.000
```

### Formato simple (retrocompatible)

```json
{
  "url": "https://pascualbravo.edu.co/pregrados/ingenieria-mecanica/",
  "title": "Ingeniería Mecánica",
  "category": "pregrado",
  "content": "## Presentación\n...\n## Requisitos\n..."
}
```

### Garantías del ingestor

- Acepta un archivo con un objeto o una lista, o un directorio con varios `.json`.
- **Rechaza** URLs fuera de `pascualbravo.edu.co` (defensa en profundidad).
- **Limpia** HTML crudo automáticamente.
- **Canonicaliza** la categoría (`pregrados` → `pregrado`) o la infiere si falta.
- **Tolerante** con programas que no tienen `presentation`: construye el documento solo desde la metadata (inscripciones, costos, facultad…), para que sigan siendo buscables.
- **Descarta** contenido vacío, muy corto o duplicado (por `source_hash`).

---

## Contrato con el equipo de frontend

### `POST /chat`

```json
// Request
{ "session_id": "uuid-o-null", "question": "..." }

// Response
{
  "session_id": "uuid",
  "turn_id": 1234,
  "answer": "...[1][2]",
  "citations": [
    { "id": 1, "url": "...", "title": "...", "snippet": "..." }
  ],
  "confident": true
}
```

- Pasa `session_id: null` en el primer mensaje; guarda el UUID devuelto y reúsalo en los siguientes.
- `confident: false` → el backend no encontró información suficientemente clara; muestra la `answer` tal cual (ya trae redirección institucional).
- Los `[N]` en `answer` corresponden a los `id` de `citations` — úsalos para renderizar enlaces inline.
- `turn_id` identifica esta respuesta del bot; guárdalo para enviarlo con `/feedback` cuando el usuario califique.

### `POST /feedback`

Cierra el loop de calidad. El usuario marca si la respuesta le sirvió; el backend persiste el juicio junto con la metadata de retrieval/gate de ese turno para triage offline.

```json
// Request
{
  "session_id": "uuid",
  "turn_id": 1234,
  "rating": "not_helpful",
  "reason": "No mencionó el costo nocturno"
}

// Response
{ "feedback_id": 42 }
```

Ratings aceptados:

| `rating` | Cuándo mostrarlo | A quién ayuda |
|---|---|---|
| `helpful` | Pulgar arriba | Señal positiva (gate calibration) |
| `not_helpful` | "Esto no respondió mi pregunta" | Retrieval / cobertura |
| `wrong` | "Esta información es incorrecta" | Prompts (alucinación candidata) |
| `incomplete` | "Me faltó información" | Retrieval / prompt verboso |
| `missing_info` | El bot dijo "no tengo esa info" pero el usuario sabe que debería | Cobertura del corpus |

Devuelve `404` si `turn_id` no existe, no pertenece a la sesión, o no es un turno del asistente.

### `POST /sessions`

Crea una sesión explícitamente (útil si quieres mostrar el chat vacío con el `session_id` ya fijado).

```json
// Response
{ "session_id": "uuid" }
```

### `GET /health`

Verifica que la DB y al menos un proveedor de LLM estén disponibles.

```json
{ "status": "ok", "database": true, "providers": ["groq", "cerebras"] }
```

---

## ¿Cómo evita alucinar?

Dos mecanismos combinados:

1. **Gate de confianza multi-señal** (antes de llamar al LLM):
   - Similitud del mejor fragmento
   - Similitud promedio del top-3
   - Consistencia del top-K (categorías, URLs)
   - Cobertura de palabras clave de la pregunta
   - Si la pregunta pide números/fechas, exige que el contexto los tenga
   - Si fallan suficientes señales → respuesta institucional de fallback (sin LLM).

2. **System prompt estricto** al LLM:
   - Prohibido usar conocimiento previo.
   - Obligatorio citar `[N]` cada afirmación.
   - Si el contexto no tiene la respuesta, decir el mensaje de fallback tal cual.

---

## Feedback loop — cómo mejora el bot con cada conversación

Cada respuesta del `/chat` graba en Postgres:

- El turno (`session_turns`) — para la memoria conversacional.
- La metadata del turno (`turn_metadata`): query reformulado, IDs/URLs de los chunks recuperados, decisión del gate, señales de confianza, score.

Cuando el usuario envía feedback a `/feedback`, se cruza con esa metadata y `scripts/analyze_feedback.py` produce tres CSVs de triage:

```bash
python scripts/analyze_feedback.py
python scripts/analyze_feedback.py --since 2026-04-01
```

| Archivo generado | Criterio | Destinatario | Acción |
|---|---|---|---|
| `data/feedback/corpus_gaps.csv` | `missing_info` ∪ (`not_helpful` ∧ `confident=false`) | Equipo de scraping | Páginas/temas que deberían estar indexadas y no lo están → agregarlas a `mappings.json` y re-ingestar |
| `data/feedback/hallucination_candidates.csv` | `wrong` ∧ `confident=true` | Owner del prompt | El gate pasó pero el LLM alucinó → revisar `SYSTEM_PROMPT`, subir umbrales de confianza específicos |
| `data/feedback/retrieval_misses.csv` | (`not_helpful` ∨ `incomplete`) ∧ `confident=true` | Owner del retrieval | El LLM intentó pero los chunks eran insuficientes → usar como negativos para re-ranker o para tunear RRF / chunking |

El script también imprime en consola una **calibración del gate**: qué fracción de los `confident=true` el usuario marcó como útiles vs. malos, y viceversa. Si `confident=false` produce muchos `missing_info`, el gate está siendo demasiado estricto y hay que bajar `CONFIDENCE_SIGNALS_REQUIRED` o los umbrales individuales en `.env`.

> **Migración**: las tablas `turn_metadata` y `feedback` están en `scripts/init_db.sql`. Si tu contenedor Postgres ya está creado, aplica el schema con:
> ```bash
> docker exec -i bravobot-postgres psql -U bravobot -d bravobot < scripts/init_db.sql
> ```

---

## Tests

```bash
pytest -q
```

51 tests cubren:
- Normalizador (aliases, dominio, HTML, dedupe, inferencia de categoría, canonicalización, formato real del scraper con `price_table`/`presentation`/metadata rica, construcción desde metadata sin `presentation`)
- Chunker (secciones separadas, heading path, no fusión entre secciones)
- Gate de confianza (preguntas on/off-domain, numéricas, comparativas)
- Extracción de citaciones
- Validación de modelos de feedback (ratings válidos, turn_id positivo, límite de `reason`)

---

## Troubleshooting

**"No LLM providers configured"**
→ Falta la API key en `.env`. Al menos `GROQ_API_KEY`.

**El modelo de embeddings tarda mucho la primera vez**
→ `sentence-transformers` descarga `multilingual-e5-large` (~2GB) en el primer `ingest.py`. Después queda en caché.

**Docker no arranca Postgres / "password authentication failed for user bravobot"**
→ Es probable que tengas PostgreSQL instalado localmente en el puerto 5432 y esté interceptando las conexiones antes de llegar al contenedor Docker. El proyecto ya viene configurado para usar el puerto **5433** en el host (`docker-compose.yml: "5433:5432"`). Verifica que tu `.env` tenga `DATABASE_URL=postgresql+psycopg://bravobot:bravobot@localhost:5433/bravobot`. Si necesitas otro puerto, edita ambos archivos (`docker-compose.yml` y `.env`).

**`pip install` falla con torch en Python 3.14**
→ Usa Python 3.11 o 3.12. Torch aún no publica wheels estables para 3.14.

---

## Desplegar en Hugging Face Spaces + Supabase (nube)

El repo ya incluye todo lo necesario para desplegarse como **Space tipo Docker**:
`Dockerfile`, `.dockerignore`, `scripts/hf_spaces_entrypoint.sh` y la metadata
YAML al inicio de este README (`sdk: docker`, `app_port: 7860`).

> HF Spaces **no ofrece Postgres**, así que la persistencia (embeddings y
> sesiones) vive en **Supabase** — Postgres gestionado con `pgvector`
> preinstalado, tier gratuito de 500 MB, y sin tarjeta de crédito.
> Otras alternativas compatibles: Neon o Render, pero esta guía usa Supabase.

### 1. Crear y preparar el proyecto en Supabase

1. Entra a [supabase.com](https://supabase.com) → *New project*. Apunta la
   **database password** que generes (no se puede recuperar después, solo
   resetear).
2. Espera a que termine el aprovisionamiento (~2 min).
3. Activa `pgvector`: **Database → Extensions**, busca `vector` y dale
   *Enable*. (`pgcrypto` ya viene activo; nuestro `init_db.sql` es idempotente
   así que no hace daño si ya existen.)
4. Copia la **Connection string** desde *Project Settings → Database →
   Connection string → URI*. Supabase te ofrece tres modos; para HF Spaces
   usa el **Session pooler** (contenedor de larga vida, soporta prepared
   statements de SQLAlchemy, IPv4 compatible):

   ```
   postgresql://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres
   ```

   Conviértelo al formato de SQLAlchemy añadiendo `+psycopg` y `sslmode=require`:

   ```
   postgresql+psycopg://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres?sslmode=require
   ```

   > ⚠️ **No uses el Transaction pooler (puerto 6543)** con SQLAlchemy: rompe
   > las prepared statements. El *Session pooler* (5432) o la *Direct
   > connection* (`db.<PROJECT_REF>.supabase.co:5432`) funcionan bien.

### 2. Crear el Space en Hugging Face

En [huggingface.co/new-space](https://huggingface.co/new-space):

- *Space SDK*: **Docker** → *Blank*
- *Hardware*: **CPU basic** sirve para probar (el modelo de embeddings
  `multilingual-e5-large` ocupa ~2 GB en RAM). Si lo sientes corto sube a
  *CPU upgrade*.

### 3. Subir el código

```bash
git remote add space https://huggingface.co/spaces/<tu-usuario>/<nombre-space>
git push space main
```

### 4. Configurar los Secrets del Space

En *Settings → Variables and secrets* añade como **Secrets** (no Variables,
para no exponer tokens en logs):

| Clave | Valor | Obligatorio |
|---|---|---|
| `DATABASE_URL` | URL del Session pooler de Supabase (ver paso 1.4) | ✅ |
| `GROQ_API_KEY` | API key de Groq | ✅ uno |
| `CEREBRAS_API_KEY` / `ANTHROPIC_API_KEY` | Proveedores alternativos | opcional |
| `INGEST_ON_START` | `true` para ingerir `scripts/sample_data/` en el primer arranque | opcional |
| `INGEST_PATH` | Ruta dentro del contenedor con tus JSON | opcional |
| `CORS_ORIGINS` | Dominios del frontend autorizados (p.ej. `https://midominio.co`) | opcional |

### 5. Esperar el build y probar

- El primer arranque descarga `multilingual-e5-large` (~2 GB) y puede tardar
  5–10 minutos. Los siguientes arranques reutilizan el caché.
- Cuando el Space quede en *Running*, el API está en:

  ```
  https://<tu-usuario>-<nombre-space>.hf.space/docs
  ```

- Verifica: `GET /health` debe responder `{"status": "ok", "database": true, ...}`.

### Cargar datos en un Space ya desplegado

Como HF Spaces no te da shell interactiva, tienes tres opciones:

- **Más rápido — ingestar localmente contra Supabase.** Exporta la misma
  `DATABASE_URL` del Session pooler en tu máquina y corre:

  ```bash
  export DATABASE_URL="postgresql+psycopg://postgres.<REF>:<PASS>@aws-0-<REGION>.pooler.supabase.com:5432/postgres?sslmode=require"
  python scripts/ingest.py scripts/sample_data/
  ```

  El ingest es idempotente (dedupe por `source_hash`), así que re-ejecutarlo
  es seguro.

- **Ingest automático al arrancar.** Sube los JSON al repo (p.ej. en
  `scripts/sample_data/`) y configura en los Secrets
  `INGEST_ON_START=true` e `INGEST_PATH=scripts/sample_data`. Tras el primer
  arranque exitoso, cambia `INGEST_ON_START=false` para evitar re-ingerir
  en cada redeploy.

- **Ingest manual desde el SQL Editor de Supabase.** No recomendado para
  cargas grandes, pero útil para inspección: *Supabase → SQL Editor* permite
  consultar `SELECT count(*) FROM documents;` para verificar la ingesta.

### Notas y límites

- El filesystem del Space es **efímero** en el tier gratuito; por eso toda la
  persistencia real vive en Supabase. El caché de modelos de HF se recrea
  en cada redeploy (lento la primera vez).
- **Supabase free tier** da 500 MB de almacenamiento y pausa el proyecto
  tras 7 días sin actividad. Para producción usa tier Pro.
- Si tu red de desarrollo no tiene IPv6, el **Session pooler** es obligatorio
  (la Direct connection de Supabase ahora solo resuelve por IPv6).
- El `docker-compose.yml` sigue sirviendo para desarrollo local — **no se usa**
  en HF Spaces (Spaces solo lee el `Dockerfile`).

---

## Licencia y contacto

Proyecto académico para el datahack Pascual Bravo. Información oficial siempre en:
- Sitio: [https://pascualbravo.edu.co](https://pascualbravo.edu.co)
- Correo: admisiones@pascualbravo.edu.co
