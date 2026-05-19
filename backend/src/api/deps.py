from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from src.api.rate_limit import TokenBucketRateLimiter
from src.api.session import InMemorySessionStore
from src.config import Settings
from src.core.in_memory_store import InMemoryStore
from src.core.schema_graph import SchemaGraph
from src.core.schema_loader import load as load_schema
from src.guardrails.audit_log import AuditLog
from src.guardrails.dual_llm import DualLLMBoundary
from src.guardrails.injection_detector import InjectionDetector
from src.guardrails.input_validator import InputValidator
from src.guardrails.output_filter import OutputFilter, SessionContext
from src.knowledge.embeddings import EmbeddingClient, SentenceTransformerEmbedding
from src.knowledge.indexer import KBIndexer
from src.knowledge.retriever import KBRetriever
from src.llm.base import BaseLLMClient
from src.llm.factory import make_llm_client
from src.planner.planner import Planner
from src.planner.relation_scorer import RelationScorer
from src.response.responder import Responder
from src.write_path.approval_store import InMemoryApprovalStore


@dataclass
class AppContainer:
    """The DI container. Built once at startup; held on `app.state.container`."""

    settings: Settings
    graph: SchemaGraph
    store: InMemoryStore
    embedding: EmbeddingClient
    kb_retriever: KBRetriever
    input_validator: InputValidator
    injection_detector: InjectionDetector
    output_filter: OutputFilter
    planner: Planner
    responder: Responder
    relation_scorer: RelationScorer
    session_store: InMemorySessionStore
    approval_store: InMemoryApprovalStore
    audit_log: AuditLog
    rate_limiter: TokenBucketRateLimiter
    dual_llm: DualLLMBoundary
    is_ready: bool = False


def build_container(
    settings: Settings,
    *,
    embedding: EmbeddingClient | None = None,
    planner_llm: BaseLLMClient | None = None,
    response_llm: BaseLLMClient | None = None,
    # `preprocessor_llm` kept as a no-op kwarg for back-compat with the
    # existing test fixture in tests/conftest.py. The new architecture
    # has only two LLM clients (planner + responder); the third (used by
    # the deleted preprocessor) is silently ignored.
    preprocessor_llm: BaseLLMClient | None = None,  # noqa: ARG001
) -> AppContainer:
    """Construct the container. Parameters allow tests to inject fakes
    instead of real LLM clients / heavy ML models.
    """
    # Step-by-step prints so a hung deploy tells us exactly where it stopped.
    print("[deps] loading schema graph", flush=True)
    graph = load_schema(settings.DATA_DIR / "schema.yaml")
    print(f"[deps] schema ok ({graph.counts()})", flush=True)

    print("[deps] loading data store", flush=True)
    store = InMemoryStore(graph, settings.DATA_DIR)
    print("[deps] data store ok", flush=True)

    print("[deps] preparing embedding client (lazy)", flush=True)
    emb = embedding or SentenceTransformerEmbedding(settings.EMBEDDING_MODEL)
    print(f"[deps] embedding ready (dim={emb.dimension})", flush=True)

    print("[deps] preparing KB retriever (lazy)", flush=True)
    kb = KBRetriever(store, KBIndexer(emb))

    # The graph IS the executor: bind store + kb so execute_plan and friends
    # can find their collaborators.
    graph.bind_runtime(store=store, kb=kb)
    print("[deps] graph bound to runtime (store + kb)", flush=True)

    print("[deps] instantiating LLM clients", flush=True)
    pllm = planner_llm or make_llm_client("planner", settings)
    rllm = response_llm or make_llm_client("response", settings)
    print("[deps] LLM clients ok", flush=True)

    planner = Planner(pllm, graph, allow_pii=False)
    responder = Responder(rllm)
    # The relation scorer is a THIRD LLM ROLE but uses the same client
    # instance as the responder — text-only, no tools, no record access.
    # DualLLMBoundary stays two-CLIENT (planner != responder); the role
    # split is documented in DESIGN.md.
    relation_scorer = RelationScorer(rllm)

    session_store = InMemorySessionStore(ttl_seconds=settings.SESSION_TTL_SECONDS)
    approval_store = InMemoryApprovalStore()
    audit_log = AuditLog(settings.AUDIT_LOG_PATH)
    rate_limiter = TokenBucketRateLimiter(per_minute=settings.RATE_LIMIT_PER_MIN)
    dual_llm = DualLLMBoundary(privileged=pllm, quarantined=rllm)
    dual_llm.assert_distinct()
    print("[deps] container fully built", flush=True)

    return AppContainer(
        settings=settings,
        graph=graph,
        store=store,
        embedding=emb,
        kb_retriever=kb,
        input_validator=InputValidator(),
        injection_detector=InjectionDetector(),
        output_filter=OutputFilter(graph, SessionContext()),
        planner=planner,
        responder=responder,
        relation_scorer=relation_scorer,
        session_store=session_store,
        approval_store=approval_store,
        audit_log=audit_log,
        rate_limiter=rate_limiter,
        dual_llm=dual_llm,
        is_ready=True,
    )


# Path constant retained for the few callers that imported it.
FEW_SHOT_PATH = Path(__file__).resolve().parents[1] / "planner" / "prompts"


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError(
            "AppContainer not initialised on app.state. "
            "Did the startup hook run?"
        )
    return container  # type: ignore[no-any-return]
