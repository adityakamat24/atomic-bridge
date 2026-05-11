from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.deps import AppContainer, get_container

router = APIRouter(prefix="/v1/schema")


@router.get("")
async def get_schema(
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    g = container.graph
    return {
        "entities": [e.model_dump() for e in g.all_entities()],
        "relations": [r.model_dump() for r in g.all_relations()],
        "value_maps": [vm.model_dump() for vm in g.all_value_maps()],
        "counts": g.counts(),
    }


@router.get("/visjs")
async def get_schema_visjs(
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    out: dict[str, object] = container.graph.to_visjs()
    return out


@router.get("/llm-context")
async def get_llm_context(
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    return {"context": container.graph.to_llm_context()}
