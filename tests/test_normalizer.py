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
