from openai import OpenAI
import os

print("BASE_URL =", os.getenv("OPENAI_BASE_URL"))
print("API_KEY  =", os.getenv("OPENAI_API_KEY"))

client = OpenAI()  # utilise directement les variables d'env

resp = client.chat.completions.create(
    model="llama3",  # ou le nom exact que tu as pull dans ollama list
    messages=[
        {"role": "user", "content": "Dis-moi en une phrase ce que tu es."}
    ],
)

print("\nRéponse du modèle :\n")
print(resp.choices[0].message.content)
