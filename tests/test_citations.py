from app.main import _extract_used_citations
from app.rag.retriever import RetrievedChunk


def _c(i: int, url: str = None, content: str = "contenido de prueba") -> RetrievedChunk:
    return RetrievedChunk(
        id=i, url=url or f"https://pascualbravo.edu.co/{i}",
        title=f"Documento {i}", category="pregrado",
        section_title=None, heading_path=None,
        content=content, similarity=0.7, rrf_score=0.1,
    )


def test_extracts_only_markers_present_in_answer():
    chunks = [_c(1), _c(2), _c(3)]
    answer = "La respuesta cita el primero [1] y el tercero [3]."
    citations = _extract_used_citations(answer, chunks)
    assert [c.id for c in citations] == [1, 3]


def test_deduplicates_repeated_markers():
    chunks = [_c(1), _c(2)]
    answer = "[1] afirma esto [1]. También [2]. Y otra vez [1]."
    citations = _extract_used_citations(answer, chunks)
    assert [c.id for c in citations] == [1, 2]


def test_ignores_markers_out_of_range():
    chunks = [_c(1)]
    answer = "[1] es válido, pero [5] no existe en el contexto."
    citations = _extract_used_citations(answer, chunks)
    assert [c.id for c in citations] == [1]


def test_snippet_is_truncated():
    long_content = "Texto " * 200
    chunks = [_c(1, content=long_content)]
    answer = "Según [1] el programa es extenso."
    citations = _extract_used_citations(answer, chunks)
    assert len(citations) == 1
    assert len(citations[0].snippet) <= 280


def test_no_markers_returns_empty():
    chunks = [_c(1), _c(2)]
    answer = "Respuesta sin citaciones."
    citations = _extract_used_citations(answer, chunks)
    assert citations == []
