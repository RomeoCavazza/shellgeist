# modules/chat.py
from typing import TypedDict, List

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage, SystemMessage
from langgraph.graph import StateGraph
from openai import BadRequestError

from modules.models import get_client


class ChatState(TypedDict):
    messages: List[BaseMessage]


def call_chat_llm(state: ChatState) -> ChatState:
    messages = state["messages"]

    client, model = get_client("smart")   # → llama3 par défaut

    openai_messages = []
    for m in messages:
        if isinstance(m, HumanMessage):
            openai_messages.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            openai_messages.append({"role": "assistant", "content": m.content})
        elif isinstance(m, SystemMessage):
            openai_messages.append({"role": "system", "content": m.content})

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=openai_messages,
        )
        answer = resp.choices[0].message.content

    except BadRequestError as e:
        # Au cas où le modèle serait indisponible
        answer = f"[Erreur LLM] {e}\n(le modèle '{model}' semble indisponible.)"

    messages.append(AIMessage(content=answer))
    return {"messages": messages}


_graph = StateGraph(ChatState)
_graph.add_node("llm", call_chat_llm)
_graph.set_entry_point("llm")
_graph.set_finish_point("llm")
chat_app = _graph.compile()
