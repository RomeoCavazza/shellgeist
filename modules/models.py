# modules/models.py
import os
from openai import OpenAI


def get_client(model_type: str = "smart"):
    """
    Sélectionne le modèle IA utilisé :

    - model_type = "fast"  → par défaut aussi llama3 (tu peux changer après)
    - model_type = "smart" → llama3

    Tu peux overrider via variables d'environnement :
        AI_MODEL_FAST
        AI_MODEL_SMART
    """
    # Par défaut : tout sur llama3
    model_fast = os.getenv("AI_MODEL_FAST", "llama3")
    model_smart = os.getenv("AI_MODEL_SMART", "llama3")

    model = model_fast if model_type == "fast" else model_smart

    client = OpenAI()  # OPENAI_BASE_URL + OPENAI_API_KEY → Ollama
    return client, model
