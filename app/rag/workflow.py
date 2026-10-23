"""
Agentic RAG Workflow — LangGraph
────────────────────────────────
Upgraded with:
  • LiteLLM Gateway (Gemini 3.8 Flash primary, OpenAI fallback)
  • NeMo Guardrails (input/output safety rails)
  • LangSmith Observability (automatic tracing)
  • LangMem Long-Term Agent Memory
"""

import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langgraph.graph import StateGraph, START, END

from app.core.config import get_settings
from app.core.guardrails import check_input_sync, check_output_sync
from app.core.observability import get_callbacks, observe_request
from app.rag.state import AgentState, RouteDecision, EvidenceGrade
from app.rag.vectorstore import get_retriever

logger = logging.getLogger(__name__)
settings = get_settings()

_llm = None
_web_search = None


def llm():
    """
    Return the LLM instance.
    When LiteLLM is enabled → uses Gemini 3.8 Flash via LiteLLM gateway.
    Otherwise → falls back to direct ChatOpenAI.
    """
    global _llm
    if _llm is None:
        if settings.litellm_enabled:
            from app.core.litellm_gateway import get_chat_model
            _llm = get_chat_model()
            logger.info("LLM: LiteLLM Gateway (model=%s)", settings.litellm_model)
        else:
            if not settings.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY is missing")
            _llm = ChatOpenAI(
                model=settings.openai_model,
                temperature=0,
                api_key=settings.openai_api_key,
            )
            logger.info("LLM: Direct OpenAI (model=%s)", settings.openai_model)
    return _llm


def web_search_tool():
    global _web_search
    if _web_search is None:
        if not settings.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY is missing")
        _web_search = TavilySearch(
            tavily_api_key=settings.tavily_api_key,
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )
    return _web_search


def add_trace(state: AgentState, message: str):
    return [*state.get("trace", []), message]


# ───────────────────────────── Guardrails node ───────────────────

def guardrail_check(state: AgentState):
    """Input guardrail: check the user question through NeMo Guardrails."""
    result = check_input_sync(state["question"])
    if not result["allowed"]:
        return {
            "answer": result.get("guardrail_message", "Your request was blocked by the safety guardrails. Please rephrase your question to focus on IT support topics."),
            "source_used": "guardrail_blocked",
            "trace": add_trace(state, f"Guardrail → BLOCKED ({result.get('reason', 'policy_violation')})"),
            "guardrail_blocked": True,
        }
    return {
        "trace": add_trace(state, "Guardrail → PASSED"),
        "guardrail_blocked": False,
    }


def after_guardrail(state: AgentState) -> Literal["route_question", "guardrail_end"]:
    """Route based on guardrail check result."""
    if state.get("guardrail_blocked", False):
        return "guardrail_end"
    return "route_question"


def guardrail_end(state: AgentState):
    """Terminal node when guardrail blocks the request."""
    return {}


# ───────────────────────────── Router ────────────────────────────

def route_question(state: AgentState):
    callbacks = get_callbacks()
    router = llm().with_structured_output(RouteDecision, method="json_mode")
    decision = router.invoke(f"""
You route messages for an enterprise IT support assistant.
Use kb for questions about company IT policies, VPN, password reset, MFA, laptop setup,
software access, security, email, devices, troubleshooting, or technology support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
Question: {state['question']}
Return valid JSON like {{"route":"kb"}}.
""", config={"callbacks": callbacks} if callbacks else None)
    return {"source_used": decision.route, "trace": add_trace(state, f"Router → {decision.route.upper()}")}


def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


# ───────────────────────────── Retrieve ──────────────────────────

def retrieve_kb(state: AgentState):
    docs = get_retriever().invoke(state["current_query"])
    return {"kb_docs": docs, "trace": add_trace(state, f"Private KB retrieval → {len(docs)} chunks")}


# ───────────────────────────── Grade KB ──────────────────────────

def grade_kb(state: AgentState):
    callbacks = get_callbacks()
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    context = "\n\n".join(f"Source: {d.metadata.get('source','unknown')}\n{d.page_content}" for d in state["kb_docs"])
    grade = grader.invoke(f"""
You grade evidence for an enterprise IT support assistant.
Question: {state['question']}
Private company KB evidence:\n{context}
Return good only if the evidence is sufficient to answer confidently and specifically.
Otherwise return weak. JSON: {{"grade":"good"}} or {{"grade":"weak"}}.
""", config={"callbacks": callbacks} if callbacks else None)
    return {"kb_grade": grade.grade, "trace": add_trace(state, f"KB evidence grade → {grade.grade.upper()}")}


def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


# ───────────────────────────── Web Search ────────────────────────

def search_web(state: AgentState):
    result = web_search_tool().invoke({"query": state["current_query"]})
    lines, citations = [], []
    if isinstance(result, dict):
        if result.get("answer"):
            lines.append("Search answer: " + result["answer"])
        for item in result.get("results", []):
            title, url, content = item.get("title", ""), item.get("url", ""), item.get("content", "")
            lines.append(f"Title: {title}\nURL: {url}\nContent: {content}")
            citations.append({"title": title or url, "url": url, "type": "web"})
    else:
        lines.append(str(result))
    return {
        "web_results": "\n\n".join(lines),
        "citations": citations,
        "source_used": "web",
        "trace": add_trace(state, "Web fallback → Tavily search"),
    }


def grade_web(state: AgentState):
    callbacks = get_callbacks()
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    grade = grader.invoke(f"""
Question: {state['question']}
Web evidence:\n{state['web_results']}
Return good if the evidence is sufficient and directly relevant; otherwise weak.
Return valid JSON like {{"grade":"good"}}.
""", config={"callbacks": callbacks} if callbacks else None)
    return {"web_grade": grade.grade, "trace": add_trace(state, f"Web evidence grade → {grade.grade.upper()}")}


def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < settings.max_retries:
        return "rewrite_query"
    return "insufficient"


# ───────────────────────────── Query Rewrite ─────────────────────

def rewrite_query(state: AgentState):
    callbacks = get_callbacks()
    rewritten = llm().invoke(f"""
Rewrite this IT support question for better private knowledge retrieval and vendor web search.
Preserve intent, add useful technical keywords, do not answer, return only the query.
Question: {state['question']}
""", config={"callbacks": callbacks} if callbacks else None).content.strip()
    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "trace": add_trace(state, f"Query rewrite → {rewritten}"),
    }


# ───────────────────────────── Answer Generation ─────────────────

def generate_from_kb(state: AgentState):
    callbacks = get_callbacks()
    context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
    user_context = state.get("user_context", "")
    answer = llm().invoke(f"""
You are an enterprise IT support copilot. Answer ONLY from the private company KB below.
Be concise, actionable, and safe. If steps are present, present them clearly.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
{user_context}
Question: {state['question']}\n\nPrivate KB:\n{context}
""", config={"callbacks": callbacks} if callbacks else None).content

    # Output guardrail
    guard_result = check_output_sync(state["question"], answer)
    if guard_result.get("filtered_response"):
        answer = guard_result["filtered_response"]

    citations = []
    seen = set()
    for d in state["kb_docs"]:
        src = d.metadata.get("source", "Private KB")
        if src not in seen:
            seen.add(src)
            citations.append({"title": src.split("/")[-1], "url": "", "type": "private_kb"})
    return {"answer": answer, "source_used": "private_kb", "citations": citations, "trace": add_trace(state, "Answer generation → PRIVATE KB")}


def generate_from_web(state: AgentState):
    callbacks = get_callbacks()
    user_context = state.get("user_context", "")
    answer = llm().invoke(f"""
You are an enterprise IT support copilot. The private company KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external web information and may need IT validation before changing company-managed systems.
{user_context}
Question: {state['question']}\n\nWeb evidence:\n{state['web_results']}
""", config={"callbacks": callbacks} if callbacks else None).content

    # Output guardrail
    guard_result = check_output_sync(state["question"], answer)
    if guard_result.get("filtered_response"):
        answer = guard_result["filtered_response"]

    return {"answer": answer, "source_used": "web_search", "trace": add_trace(state, "Answer generation → WEB SEARCH")}


def direct_answer(state: AgentState):
    callbacks = get_callbacks()
    answer = llm().invoke(
        f"Respond briefly and naturally to: {state['question']}",
        config={"callbacks": callbacks} if callbacks else None,
    ).content
    return {"answer": answer, "source_used": "direct", "trace": add_trace(state, "Direct response → no retrieval")}


def insufficient(state: AgentState):
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "trace": add_trace(state, "Stopped → insufficient reliable evidence"),
    }


# ───────────────────────────── Graph Builder ─────────────────────

def build_graph():
    graph = StateGraph(AgentState)

    # Add all nodes including new guardrail nodes
    for name, fn in {
        "guardrail_check": guardrail_check,
        "guardrail_end": guardrail_end,
        "route_question": route_question,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb,
        "search_web": search_web,
        "grade_web": grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": direct_answer,
        "insufficient": insufficient,
    }.items():
        graph.add_node(name, fn)

    # Guardrail check is the first node
    graph.add_edge(START, "guardrail_check")
    graph.add_conditional_edges("guardrail_check", after_guardrail, {
        "route_question": "route_question",
        "guardrail_end": "guardrail_end",
    })
    graph.add_edge("guardrail_end", END)

    # Normal routing
    graph.add_conditional_edges("route_question", route_after_router, {
        "retrieve_kb": "retrieve_kb", "direct_answer": "direct_answer"
    })
    graph.add_edge("retrieve_kb", "grade_kb")
    graph.add_conditional_edges("grade_kb", after_kb, {
        "generate_from_kb": "generate_from_kb", "search_web": "search_web"
    })
    graph.add_edge("search_web", "grade_web")
    graph.add_conditional_edges("grade_web", after_web, {
        "generate_from_web": "generate_from_web", "rewrite_query": "rewrite_query", "insufficient": "insufficient"
    })
    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("insufficient", END)
    return graph.compile()


agent_graph = build_graph()


def ask(question: str, username: str = "user"):
    """Execute the full agentic RAG workflow with LangSmith observability and LangMem long-term memory."""
    from app.services.memory import format_memories_for_prompt, extract_and_save_memory_async

    user_context = format_memories_for_prompt(username)

    with observe_request(question) as metrics:
        metrics.start_step("full_pipeline")

        initial: AgentState = {
            "question": question,
            "current_query": question,
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "retry_count": 0,
            "trace": [],
            "citations": [],
            "guardrail_blocked": False,
            "username": username,
            "user_context": user_context,
        }
        result = agent_graph.invoke(initial, config={"callbacks": get_callbacks()})

        metrics.end_step("full_pipeline", {
            "source_used": result.get("source_used"),
            "guardrail_blocked": result.get("guardrail_blocked", False),
        })

    # Asynchronously / passively extract and update long-term user memory
    if not result.get("guardrail_blocked", False) and result.get("answer"):
        extract_and_save_memory_async(username, question, result["answer"])

    return result

