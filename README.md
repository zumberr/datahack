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
  "answer": "Pascual Bravo ofrece la Tecnología en Sistematización de Datos [1] y la Tecnología en Electricidad, entre otras. Ambas duran 6 semestres [1][2].",
  "citations": [
    { "id": 1, "url": "https://pascualbravo.edu.co/pregrados/tecnologia-en-sistematizacion-de-datos/", "title": "Tecnología en Sistematización de Datos", "snippet": "..." }
  ],
  "confident": true
}
```

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

## Tests

```bash
pytest -q
```

41 tests cubren:
- Normalizador (aliases, dominio, HTML, dedupe, inferencia de categoría, canonicalización, formato real del scraper con `price_table`/`presentation`/metadata rica, construcción desde metadata sin `presentation`)
- Chunker (secciones separadas, heading path, no fusión entre secciones)
- Gate de confianza (preguntas on/off-domain, numéricas, comparativas)
- Extracción de citaciones

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

## Licencia y contacto

Proyecto académico para el datahack Pascual Bravo. Información oficial siempre en:
- Sitio: [https://pascualbravo.edu.co](https://pascualbravo.edu.co)
- Correo: admisiones@pascualbravo.edu.co
