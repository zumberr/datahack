from app.rag.confidence import evaluate_confidence
from app.rag.retriever import RetrievedChunk


def _chunk(sim: float, content: str, url: str = "https://pascualbravo.edu.co/a",
           category: str = "pregrado", title: str = "Programa") -> RetrievedChunk:
    return RetrievedChunk(
        id=1, url=url, title=title, category=category,
        section_title=None, heading_path=None,
        content=content, similarity=sim, rrf_score=0.1,
    )


def test_empty_chunks_fails_gate():
    res = evaluate_confidence("¿Qué tecnologías hay?", [])
    assert not res.passed
    assert res.score == 0.0


def test_strong_match_passes_gate():
    chunks = [
        _chunk(0.78, "La institución ofrece las tecnologías en Sistematización de Datos, Electricidad y Mecánica. Duración 6 semestres."),
        _chunk(0.73, "Tecnología en Sistematización de Datos con duración de 6 semestres.", url="https://pascualbravo.edu.co/b"),
        _chunk(0.68, "Tecnología en Electricidad, programa de 6 semestres.", url="https://pascualbravo.edu.co/c"),
    ]
    res = evaluate_confidence("¿Qué tecnologías ofrece la institución?", chunks)
    assert res.passed
    assert res.signals["top1"]
    assert res.signals["top3_mean"]


def test_off_domain_question_fails_gate():
    chunks = [
        _chunk(0.15, "Texto sobre becas y bienestar institucional.",
               category="beneficios"),
        _chunk(0.10, "Texto sobre inscripciones a posgrado.",
               url="https://pascualbravo.edu.co/b", category="admisiones"),
        _chunk(0.08, "Texto sobre costos de matrícula del semestre.",
               url="https://pascualbravo.edu.co/c", category="costos"),
    ]
    res = evaluate_confidence("¿Cómo preparo un arroz con pollo?", chunks)
    assert not res.passed


def test_numeric_question_requires_digits_in_context():
    chunks_without_numbers = [
        _chunk(0.55, "Ingeniería Mecánica forma profesionales integrales."),
        _chunk(0.50, "El programa tiene enfoque práctico en la industria metalmecánica.",
               url="https://pascualbravo.edu.co/b"),
        _chunk(0.48, "Los egresados trabajan en manufactura y mantenimiento.",
               url="https://pascualbravo.edu.co/c"),
    ]
    res_no_digits = evaluate_confidence("¿Cuánto cuesta Ingeniería Mecánica?", chunks_without_numbers)
    assert not res_no_digits.signals["format_match"]

    chunks_with_numbers = [
        _chunk(0.55, "Matrícula de Ingeniería Mecánica: $3.500.000 por semestre."),
        _chunk(0.50, "Duración del programa: 10 semestres.",
               url="https://pascualbravo.edu.co/b"),
        _chunk(0.48, "Se ofrecen planes de financiación.",
               url="https://pascualbravo.edu.co/c"),
    ]
    res_with_digits = evaluate_confidence("¿Cuánto cuesta Ingeniería Mecánica?", chunks_with_numbers)
    assert res_with_digits.signals["format_match"]


def test_comparative_question_tolerates_lower_consistency():
    chunks = [
        _chunk(0.62, "Ingeniería Industrial dura 10 semestres y forma profesionales en optimización de procesos.",
               url="https://pascualbravo.edu.co/industrial", category="pregrado"),
        _chunk(0.58, "Ingeniería Mecánica dura 10 semestres con enfoque en diseño y manufactura.",
               url="https://pascualbravo.edu.co/mecanica", category="pregrado"),
        _chunk(0.40, "Perfil ocupacional variado del sector manufacturero.",
               url="https://pascualbravo.edu.co/otros", category="pregrado"),
    ]
    res = evaluate_confidence(
        "¿Qué diferencia hay entre Ingeniería Industrial y Mecánica?",
        chunks,
    )
    # Top-1 and keyword coverage should carry the gate.
    assert res.signals["top1"]
    assert res.signals["keyword_coverage"]
    assert res.passed


def test_catastrophic_similarity_blocks_even_with_keyword_hits():
    chunks = [
        _chunk(0.05, "ingenieria mecanica industrial sistematizacion datos costos matricula",
               category="pregrado"),
        _chunk(0.04, "ingenieria mecanica industrial sistematizacion datos costos matricula",
               url="https://pascualbravo.edu.co/b", category="pregrado"),
        _chunk(0.03, "ingenieria mecanica industrial sistematizacion datos costos matricula",
               url="https://pascualbravo.edu.co/c", category="pregrado"),
    ]
    res = evaluate_confidence("ingenieria mecanica industrial sistematizacion datos costos", chunks)
    assert not res.passed
    assert res.details["catastrophic"] == "yes"
