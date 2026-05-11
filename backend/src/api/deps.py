from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request

from src.api.rate_limit import TokenBucketRateLimiter
from src.api.session import InMemorySessionStore
from src.config import Settings
from src.core.in_memory_store import InMemoryStore
from src.core.schema_loader import load as load_schema
from src.executor.engine import ExecutionEngine
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
from src.planner.few_shot import FewShotRetriever, load_examples
from src.planner.name_resolver import NameResolver
from src.planner.plan_generator import PlanGenerator
from src.planner.plan_validator import PlanValidator
from src.planner.preprocessor import Preprocessor
from src.response.generator import ResponseGenerator
from src.write_path.approval_store import InMemoryApprovalStore

FEW_SHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "planner"
    / "prompts"
    / "few_shot.yaml"
)


@dataclass
class AppContainer:
    """The DI container. Built once at startup; held on `app.state.container`."""

    settings: Settings
    graph: Any  # SchemaGraph
    store: InMemoryStore
    embedding: EmbeddingClient
    kb_retriever: KBRetriever
    input_validator: InputValidator
    injection_detector: InjectionDetector
    output_filter: OutputFilter
    name_resolver: NameResolver
    few_shot: FewShotRetriever
    preprocessor: Preprocessor
    plan_generator: PlanGenerator
    plan_validator: PlanValidator
    executor: ExecutionEngine
    response_generator: ResponseGenerator
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
    preprocessor_llm: BaseLLMClient | None = None,
    response_llm: BaseLLMClient | None = None,
) -> AppContainer:
    """Construct the container. Parameters allow tests to inject fakes
    instead of real LLM clients / heavy ML models.
    """
    # Step-by-step prints so a hung deploy tells us exactly where it stopped.
    # `print(..., flush=True)` so Docker logs see them in real time even if
    # PYTHONUNBUFFERED isn't honoured in some sub-call.
    print("[deps] loading schema graph", flush=True)
    graph = load_schema(settings.DATA_DIR / "schema.yaml")
    print(f"[deps] schema ok ({graph.counts()})", flush=True)

    print("[deps] loading data store", flush=True)
    store = InMemoryStore(graph, settings.DATA_DIR)
    print("[deps] data store ok", flush=True)

    print("[deps] preparing embedding client (lazy: model loads on first use)", flush=True)
    emb = embedding or SentenceTransformerEmbedding(settings.EMBEDDING_MODEL)
    print(f"[deps] embedding ready (dim={emb.dimension})", flush=True)

    print("[deps] preparing KB retriever (lazy: index builds on first KB query)", flush=True)
    kb = KBRetriever(store, KBIndexer(emb))

    print("[deps] instantiating LLM clients", flush=True)
    pllm = planner_llm or make_llm_client("planner", settings)
    pre_llm = preprocessor_llm or make_llm_client("preprocessor", settings)
    rllm = response_llm or make_llm_client("response", settings)
    print("[deps] LLM clients ok", flush=True)

    name_resolver = NameResolver(store)
    few_shot = FewShotRetriever(load_examples(FEW_SHOT_PATH), emb)
    preprocessor = Preprocessor(pre_llm, graph, name_resolver)
    plan_generator = PlanGenerator(pllm, graph, few_shot)
    plan_validator = PlanValidator(graph, allow_pii=False)
    executor = ExecutionEngine(graph, store, kb=kb)
    response_generator = ResponseGenerator(rllm)

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
        name_resolver=name_resolver,
        few_shot=few_shot,
        preprocessor=preprocessor,
        plan_generator=plan_generator,
        plan_validator=plan_validator,
        executor=executor,
        response_generator=response_generator,
        session_store=session_store,
        approval_store=approval_store,
        audit_log=audit_log,
        rate_limiter=rate_limiter,
        dual_llm=dual_llm,
        is_ready=True,
    )


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError(
            "AppContainer not initialised on app.state. "
            "Did the startup hook run?"
        )
    return container  # type: ignore[no-any-return]
