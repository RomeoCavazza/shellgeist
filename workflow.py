from typing import TypedDict, List

from openai import OpenAI
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langgraph.graph import StateGraph, END

client = OpenAI()  # utilise OPENAI_BASE_URL + OPENAI_API_KEY


class ChatState(TypedDict):
    messages: List[BaseMessage]


def call_llm(state: ChatState) -> ChatState:
    messages = state["messages"]

    # On transforme les messages LangChain en format OpenAI
    openai_messages = []
    for m in messages:
        if isinstance(m, HumanMessage):
            openai_messages.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            openai_messages.append({"role": "assistant", "content": m.content})

    resp = client.chat.completions.create(
        model="llama3",  # nom du modèle Ollama
        messages=openai_messages,
    )

    answer = resp.choices[0].message.content
    messages.append(AIMessage(content=answer))

    return {"messages": messages}


# Définition du graphe LangGraph
graph = StateGraph(ChatState)
graph.add_node("llm", call_llm)
graph.set_entry_point("llm")
graph.set_finish_point("llm")

app = graph.compile()


if __name__ == "__main__":
    import sys

    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Présente-toi en une phrase."
    initial_state: ChatState = {"messages": [HumanMessage(content=prompt)]}

    result = app.invoke(initial_state)
    last_msg = result["messages"][-1]
    print(last_msg.content)
