import os

os.environ.setdefault("GROQ_API_KEY", "")
os.environ.setdefault("CEREBRAS_API_KEY", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://bravobot:bravobot@localhost:5432/bravobot",
)
