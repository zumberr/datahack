from app.ingest.normalizer import normalize_many, normalize_one


def test_rejects_non_pascualbravo_domain():
    doc, warns = normalize_one({
        "url": "https://other-site.com/page",
        "title": "X",
        "content": "A" * 200,
    })
    assert doc is None
    assert any("not in pascualbravo.edu.co" in w for w in warns)


def test_accepts_subdomain():
    doc, _ = normalize_one({
        "url": "https://admisiones.pascualbravo.edu.co/info",
        "title": "Admisiones",
        "content": "La institución ofrece varios programas académicos. " * 5,
    })
    assert doc is not None
    assert doc.url.endswith("pascualbravo.edu.co/info")


def test_field_aliases_url_title_content():
    doc, _ = normalize_one({
        "link": "https://pascualbravo.edu.co/programas/x",
        "titulo": "Programa X",
        "body": "Contenido suficientemente largo para pasar la validación mínima del normalizador y tener sentido.",
    })
    assert doc is not None
    assert doc.title == "Programa X"
    assert "Contenido" in doc.content


def test_rejects_too_short_content():
    doc, warns = normalize_one({
        "url": "https://pascualbravo.edu.co/x",
        "title": "Corto",
        "content": "Hola",
    })
    assert doc is None
    assert any("too short" in w for w in warns)


def test_rejects_missing_content():
    doc, warns = normalize_one({
        "url": "https://pascualbravo.edu.co/x",
        "title": "Sin cuerpo",
    })
    assert doc is None
    assert any("missing content" in w for w in warns)


def test_html_is_stripped():
    doc, _ = normalize_one({
        "url": "https://pascualbravo.edu.co/admisiones",
        "title": "Admisiones",
        "content": "<div><h1>Admisiones</h1><p>Inscripciones abiertas hasta el 30 de noviembre. Los aspirantes deben presentar su documento.</p><script>evil()</script></div>",
    })
    assert doc is not None
    assert "<p>" not in doc.content
    assert "<script>" not in doc.content
    assert "evil()" not in doc.content
    assert "Inscripciones" in doc.content


def test_category_inferred_from_url():
    doc, _ = normalize_one({
        "url": "https://pascualbravo.edu.co/pregrados/ingenieria-mecanica/",
        "title": "Ingeniería Mecánica",
        "content": "Programa de Ingeniería Mecánica con duración de 10 semestres formando profesionales integrales.",
    })
    assert doc is not None
    assert doc.category == "pregrado"


def test_category_inferred_from_keywords_when_url_generic():
    doc, _ = normalize_one({
        "url": "https://pascualbravo.edu.co/info/detalles",
        "title": "Matrícula",
        "content": "Los valores de matrícula y derechos pecuniarios para el periodo 2026 son los siguientes ...",
    })
    assert doc is not None
    assert doc.category == "costos"


def test_title_inferred_when_missing():
    doc, _ = normalize_one({
        "url": "https://pascualbravo.edu.co/bienestar/becas",
        "content": "Bienestar ofrece becas para estudiantes destacados con promedios altos y necesidad económica demostrada.",
    })
    assert doc is not None
    assert doc.title.strip() != ""
    assert any("title missing" in w for w in doc.warnings)


def test_many_dedupes_by_content_hash():
    items = [
        {
            "url": "https://pascualbravo.edu.co/x",
            "title": "A",
            "content": "Contenido exactamente igual de prueba. " * 5,
        },
        {
            "url": "https://pascualbravo.edu.co/x",
            "title": "A",
            "content": "Contenido exactamente igual de prueba. " * 5,
        },
    ]
    docs, log = normalize_many(items)
    assert len(docs) == 1
    assert any("duplicate" in entry for entry in log)


def test_many_rejects_non_dict():
    docs, log = normalize_many(["not a dict", {"url": "https://pascualbravo.edu.co/ok",
                                                "title": "Ok", "content": "x" * 200}])
    assert len(docs) == 1
    assert any("not a dict" in entry for entry in log)


# ---------- Real-scraper format: rich metadata, plural category, missing presentation ----------

_REAL_DOC_FULL = {
    "title": "Ingeniería de Materiales",
    "summary": "SNIES 102345 — Registro calificado vigente.",
    "link": "https://pascualbravo.edu.co/pregrados/ingenieria-de-materiales/",
    "item_type": "programa",
    "category": "pregrados",
    "source_url": "https://pascualbravo.edu.co/pregrados/ingenieria-de-materiales/",
    "faculty": "Facultad de Ingeniería",
    "modalidad": "Presencial - Alta Calidad",
    "program_title": "Ingeniero (a) de Materiales",
    "inscriptions": "Del 2 de marzo al 15 de junio de 2026",
    "class_start": "Agosto de 2026",
    "price_table": [
        {"Estrato": "1", "Valor": "$1.800.000"},
        {"Estrato": "2", "Valor": "$2.100.000"},
        {"Estrato": "3", "Valor": "$2.500.000"},
    ],
    "presentation": "El programa forma profesionales en materiales y tiene una duración de 10 semestres con enfoque en metalmecánica y polímeros.",
}


def test_real_scraper_plural_category_is_canonicalized():
    doc, _ = normalize_one(_REAL_DOC_FULL)
    assert doc is not None
    assert doc.category == "pregrado"
    assert any("canonicalized" in w for w in doc.warnings)


def test_real_scraper_link_alias_is_picked_over_source_url():
    doc, _ = normalize_one(_REAL_DOC_FULL)
    assert doc is not None
    assert doc.url.endswith("/ingenieria-de-materiales/")


def test_presentation_is_accepted_as_content():
    data = dict(_REAL_DOC_FULL)
    # Strip every content-like field except presentation, so the test proves
    # presentation alone is enough.
    doc, _ = normalize_one(data)
    assert doc is not None
    assert "10 semestres" in doc.content


def test_rich_metadata_is_folded_into_content():
    doc, _ = normalize_one(_REAL_DOC_FULL)
    assert doc is not None
    # General info section must carry faculty + modalidad + SNIES summary.
    assert "Facultad de Ingeniería" in doc.content
    assert "Presencial - Alta Calidad" in doc.content
    assert "SNIES 102345" in doc.content
    # Inscriptions section must carry the inscription window and class start.
    assert "2 de marzo" in doc.content
    assert "Agosto de 2026" in doc.content
    # Costs section must render every estrato row from the price_table.
    assert "Estrato 1" in doc.content
    assert "$1.800.000" in doc.content
    assert "$2.500.000" in doc.content


def test_missing_presentation_but_rich_metadata_builds_content():
    """Tecnología en Producción Industrial in the real dump has no presentation."""
    data = {
        "title": "Tecnología en Producción Industrial",
        "summary": "SNIES 54231 — Registro calificado vigente.",
        "link": "https://pascualbravo.edu.co/pregrados/tecnologia-en-produccion-industrial/",
        "category": "pregrados",
        "faculty": "Facultad de Producción y Diseño",
        "modalidad": "Presencial y virtual",
        "program_title": "Tecnólogo (a) en Producción Industrial",
        "inscriptions": "Del 2 de marzo al 15 de junio de 2026",
        "class_start": "Agosto de 2026",
        "price_table": [
            {"Estrato": "1", "Valor": "$1.200.000"},
            {"Estrato": "2", "Valor": "$1.400.000"},
        ],
    }
    doc, _ = normalize_one(data)
    assert doc is not None
    assert doc.category == "pregrado"
    # The program must be searchable by its distinctive metadata.
    assert "Facultad de Producción y Diseño" in doc.content
    assert "$1.200.000" in doc.content
    assert "Agosto de 2026" in doc.content
    assert any("built from metadata" in w for w in doc.warnings)


def test_missing_presentation_and_metadata_is_rejected():
    doc, warns = normalize_one({
        "url": "https://pascualbravo.edu.co/pregrados/fantasma/",
        "title": "Programa Fantasma",
    })
    assert doc is None
    assert any("missing content" in w for w in warns)


def test_price_table_tolerates_missing_or_malformed_entries():
    data = dict(_REAL_DOC_FULL)
    data["price_table"] = [
        {"Estrato": "1", "Valor": "$1.800.000"},
        "basura",                   # non-dict row
        {"otro_campo": "ignorar"},  # dict without Estrato/Valor
        {"Estrato": "3"},           # missing Valor
    ]
    doc, _ = normalize_one(data)
    assert doc is not None
    assert "Estrato 1" in doc.content
    assert "$1.800.000" in doc.content
    # Malformed rows must not leak boilerplate.
    assert "basura" not in doc.content
    assert "otro_campo" not in doc.content


def test_program_title_alias_used_when_title_missing():
    data = dict(_REAL_DOC_FULL)
    data.pop("title")
    doc, _ = normalize_one(data)
    assert doc is not None
    # program_title must win over the URL-segment fallback.
    assert "Ingeniero" in doc.title


# ---------- FAQ items (question/answer) ----------

def test_faq_question_answer_accepted():
    """FAQ items from the posgrado scraper use question/answer instead of title/content."""
    data = {
        "question": "¿Cuál es el precio del semestre en los programas de Posgrado?",
        "answer": (
            "El precio para los programas de Especialización es el equivalente en "
            "pesos colombianos a 5.5 salarios mínimos legales mensuales vigentes."
        ),
        "item_type": "faq",
        "category": "posgrados",
        "source_url": "https://pascualbravo.edu.co/posgrados/",
    }
    doc, _ = normalize_one(data)
    assert doc is not None
    assert "precio" in doc.title.lower()
    assert "5.5 salarios" in doc.content
    assert doc.category == "posgrado"


def test_faq_short_answer_rejected():
    data = {
        "question": "¿Pregunta?",
        "answer": "Sí.",
        "source_url": "https://pascualbravo.edu.co/posgrados/",
    }
    doc, warns = normalize_one(data)
    assert doc is None
    assert any("too short" in w for w in warns)


# ---------- Posgrado program metadata ----------

_POSGRADO_PROGRAMA = {
    "title": "Especialización en Big Data",
    "link": "https://pascualbravo.edu.co/programas/especializacion-en-big-data/",
    "category": "posgrados",
    "source_url": "https://pascualbravo.edu.co/posgrados/",
    "semesters": "2 semestres",
    "program_title": "Especialista en Big data",
    "schedule": "Jueves: 6:00 p.m. – 10:00 p.m. Viernes: 6:00 p.m. – 10:00 p.m.",
    "cost": "$9.630.000",
    "credits": "24",
    "snies": "110634",
    "registro_calificado": "014240 del 06 de agosto de 2021",
    "vigencia": "7 años",
    "program_overview": "Domina herramientas de análisis, visualización y modelado para liderar la era del dato.",
}


def test_posgrado_program_overview_is_accepted_as_content():
    doc, _ = normalize_one(_POSGRADO_PROGRAMA)
    assert doc is not None
    assert "análisis" in doc.content or "visualización" in doc.content


def test_posgrado_metadata_folded_into_content():
    doc, _ = normalize_one(_POSGRADO_PROGRAMA)
    assert doc is not None
    # Posgrado details section should carry these fields
    assert "$9.630.000" in doc.content
    assert "2 semestres" in doc.content
    assert "110634" in doc.content
    assert "014240" in doc.content
    assert "7 años" in doc.content


def test_posgrado_category_canonicalized():
    doc, _ = normalize_one(_POSGRADO_PROGRAMA)
    assert doc is not None
    assert doc.category == "posgrado"
