from app.db import db_session
from app.rag.retriever import retrieve
from app.rag.confidence import evaluate_confidence
from app.rag.generator import get_llm
from app.rag.prompts import SYSTEM_PROMPT, build_user_message

def test_query(question):
    with db_session() as sess:
        chunks = retrieve(sess, question)
        print(f"\n=================================")
        print(f"PREGUNTA: {question}")
        print(f"=================================")
        
        conf = evaluate_confidence(question, chunks)
        print(f"Confianza Passed: {conf.passed}")
        for k, v in conf.signals.items():
            print(f" - {k}: {v}")
            
        if not conf.passed:
            print("\n>> FALLBACK TRIGGERED")
            return
            
        system = SYSTEM_PROMPT
        user = build_user_message(question, chunks)
        
        llm = get_llm()
        ans = llm.complete(system, user, temperature=0.1, max_tokens=1024)
        print("\n>> RESPUESTA DEL LLM:")
        print(ans)

queries = [
    "¿Cuanto cuestan los programas de pregrado?",
    "¿Cuales son los posgrados?",
    "¿Cuanto cuesta el examen de suficiencia?"
]

for q in queries:
    test_query(q)
