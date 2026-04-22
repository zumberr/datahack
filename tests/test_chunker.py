from app.ingest.schemas import NormalizedDocument
from app.rag.chunker import chunk_document


def _doc(content: str, title: str = "Programa X",
         url: str = "https://pascualbravo.edu.co/programas/x",
         category: str = "pregrado") -> NormalizedDocument:
    return NormalizedDocument(
        url=url, title=title, category=category,
        content=content, source_hash="deadbeef",
    )


def test_markdown_sections_become_separate_chunks():
    content = (
        "## Presentación\n"
        "Programa de cuatro semestres con enfoque práctico en la industria del transporte. "
        "Los estudiantes aprenden a operar y mantener equipos pesados.\n\n"
        "## Perfil ocupacional\n"
        "El egresado puede desempeñarse en empresas de manufactura y logística del sector automotriz.\n\n"
        "## Requisitos de admisión\n"
        "Ser bachiller, presentar Saber 11, pagar derechos de inscripción."
    )
    chunks = chunk_document(_doc(content))
    section_titles = {c.section_title for c in chunks}
    assert "Presentación" in section_titles
    assert "Perfil ocupacional" in section_titles
    assert "Requisitos de admisión" in section_titles


def test_heading_path_includes_doc_title():
    content = "## Presentación\nPrograma breve con duración de 6 semestres y enfoque práctico."
    chunks = chunk_document(_doc(content, title="Tecnología en Electricidad"))
    assert chunks
    assert "Tecnología en Electricidad" in chunks[0].heading_path


def test_does_not_mix_sections_in_a_single_chunk():
    content = (
        "## Admisiones\n"
        "Proceso de inscripción y requisitos generales para aspirantes a pregrado.\n\n"
        "## Costos\n"
        "Valor de la matrícula para 2026 es de tres millones quinientos mil pesos."
    )
    chunks = chunk_document(_doc(content))
    for c in chunks:
        if "Admisiones" in c.section_title:
            assert "matrícula" not in c.content.lower() or "tres millones" not in c.content.lower()


def test_fallback_single_chunk_when_no_headings():
    content = (
        "Este es un documento plano sin encabezados explícitos. "
        "Habla sobre la institución y sus servicios generales para estudiantes actuales. " * 3
    )
    chunks = chunk_document(_doc(content))
    assert len(chunks) >= 1
    assert all(c.chunk_index >= 0 for c in chunks)


def test_long_section_is_split_with_overlap():
    body = "Este párrafo contiene contenido relevante sobre el programa académico. " * 120
    content = f"## Detalles del programa\n{body}"
    chunks = chunk_document(_doc(content))
    assert len(chunks) >= 2
    assert all("Detalles del programa" in c.section_title for c in chunks)


def test_chunk_index_is_monotonic():
    content = (
        "## A\n" + ("Contenido uno. " * 30) + "\n\n"
        "## B\n" + ("Contenido dos. " * 30) + "\n\n"
        "## C\n" + ("Contenido tres. " * 30)
    )
    chunks = chunk_document(_doc(content))
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))
