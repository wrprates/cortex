from __future__ import annotations

import logging

from ..agents import (
    run_analyst,
    run_analyst_r,
    run_modeler,
    run_modeler_r,
    run_orchestrator,
    run_reviewer,
)
from .state import ProjectState

logger = logging.getLogger(__name__)

MAX_REVIEW_LOOPS = 2


def _build_run_readme(state, report, eda, model) -> str:
    run_id = state.get("run_id", "unknown")
    plan = state.get("plan") or {}
    title = report.get("title") or "Análise"
    subtitle = report.get("subtitle") or ""
    exec_sum = report.get("executive_summary") or ""
    findings = report.get("key_findings") or []
    conclusions = report.get("conclusions") or ""
    recommendations = report.get("recommendations") or []
    caveats = report.get("caveats") or []
    push = report.get("_github_push", "?")
    attempts = eda.get("attempts") or []
    workflow = state.get("workflow_type", "?")
    lang = state.get("primary_language", "?")

    # lista de HTMLs em outputs/
    lines = [f"# {title}"]
    if subtitle:
        lines.append(f"_{subtitle}_")
    lines += [
        "",
        f"**Run:** `{run_id}` · **Workflow:** `{workflow}` · **Linguagem:** `{lang}`",
        f"**Tentativas de execução:** {len(attempts)} · **Push:** `{push}`",
        "",
        "## 📊 Entregáveis",
        "- `outputs/report.html` — relatório interativo (Quarto + echarts4r) — **abre no browser após clone**",
        "- `outputs/*.html` — widgets interativos individuais (se houver)",
        "- `outputs/*.csv` / `outputs/*.parquet` — tabelas de resultados",
        "- `R/analyst_code.R` — código R fonte da análise",
        "- `plan.json` — plano estruturado das fases",
        "- `final_report.json` — relatório completo em JSON",
        "",
        "## 📝 Resumo Executivo",
        exec_sum or "_(vazio)_",
        "",
    ]
    if findings:
        lines.append("## 🔑 Principais Achados")
        for f in findings[:10]:
            lines.append(f"- {f}")
        lines.append("")
    if conclusions:
        lines += ["## ✅ Conclusões", str(conclusions), ""]
    if recommendations:
        lines.append("## 🎯 Recomendações")
        for r in recommendations[:10]:
            lines.append(f"- {r}")
        lines.append("")
    if caveats:
        lines.append("## ⚠️ Ressalvas")
        for c in caveats[:10]:
            lines.append(f"- {c}")
        lines.append("")
    lines += [
        "## 🗺️ Fases Planejadas",
    ]
    for i, ph in enumerate(plan.get("phases") or [], 1):
        nm = ph.get("name", "?")
        ap = "🔐" if ph.get("requires_human_approval") else "•"
        lines.append(f"{i}. {ap} **{nm}**")
    lines += [
        "",
        "---",
        "Gerado pelo **Cortex** — agente multi-LLM de ciência de dados.",
    ]
    return "\n".join(lines)


def _loop_run(coro):
    """Roda uma coroutine num loop novo, isolado — safe em thread."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def node_plan(state: ProjectState) -> dict:
    workflow_type = state.get("workflow_type", "full_ml")
    plan = run_orchestrator(
        "plan",
        context={
            "description": state.get("description"),
            "datasets": state.get("datasets", []),
            "workflow_type": workflow_type,
        },
    )
    return {"plan": plan, "current_phase": "planning", "status": "waiting_human"}


def node_eda(state: ProjectState) -> dict:
    from ..storage import minio_client
    import os

    language = state.get("primary_language", "r")
    workflow_type = state.get("workflow_type", "full_ml")
    datasets = state.get("datasets", [])

    # Baixa datasets s3:// do MinIO pra passar como inputs ao sandbox
    inputs: dict[str, bytes] = {}
    for uri in datasets:
        if uri.startswith("s3://"):
            # s3://bucket/key...  → pega só o key (remove prefixo bucket)
            path = uri[5:]
            parts = path.split("/", 1)
            if len(parts) == 2:
                key = parts[1]
                data = minio_client.get_bytes(key)
                inputs[os.path.basename(key)] = data
                logger.info("Dataset downloaded: %s (%d bytes)", key, len(data))
            else:
                raise ValueError(f"URI de dataset inválida (esperado s3://bucket/key): {uri}")
        else:
            raise ValueError(f"URI de dataset não suportada (use s3://): {uri}")

    context = {
        "plan": state.get("plan"),
        "datasets": datasets,
        "available_inputs": list(inputs.keys()),
    }

    if language == "r":
        result = run_analyst_r(
            task="Execute EDA conforme o plano aprovado.",
            context=context,
            inputs=inputs,
            workflow_type=workflow_type,
        )
    else:
        result = run_analyst(
            task="Execute EDA conforme o plano aprovado.",
            context=context,
            inputs=inputs,
        )

    return {"eda_results": result, "current_phase": "eda"}


def node_decide_next(state: ProjectState) -> dict:
    decision = run_orchestrator(
        "decide_next",
        context={
            "plan": state.get("plan"),
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
            "review_results": state.get("review_results"),
        },
    )
    return {"plan": {**state.get("plan", {}), "_next_decision": decision}}


def node_modeling(state: ProjectState) -> dict:
    language = state.get("primary_language", "r")

    context = {
        "plan": state.get("plan"),
        "eda_summary": (state.get("eda_results") or {}).get("summary"),
    }

    if language == "r":
        result = run_modeler_r(
            task="Treine e compare modelos baseline conforme o plano.",
            context=context,
            final_training=False,
        )
    else:
        result = run_modeler(
            task="Treine e compare modelos baseline conforme o plano.",
            context=context,
            final_training=False,
        )

    return {
        "model_results": result,
        "current_phase": "modeling",
        "status": "waiting_human",
    }


def node_review(state: ProjectState) -> dict:
    result = run_reviewer(
        artifacts_context={
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
        }
    )
    loops = state.get("review_loop_count", 0) + 1
    return {
        "review_results": result,
        "current_phase": "review",
        "review_loop_count": loops,
    }


def node_report(state: ProjectState) -> dict:
    import asyncio
    import json as _json
    from ..storage import github_manager, postgres as _db

    report = run_orchestrator(
        "compile_report",
        context={
            "plan": state.get("plan"),
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
            "review_results": state.get("review_results"),
        },
    )

    # Push pro GitHub se o cliente tem repo (já resolvido no start, via state)
    push_status = "skipped"
    repo_url = state.get("github_repo")
    if repo_url:
        try:
            from pathlib import Path
            eda = state.get("eda_results") or {}
            model = state.get("model_results") or {}
            plan = state.get("plan") or {}

            files: dict[str, bytes] = {
                "plan.json": _json.dumps(plan, ensure_ascii=False, indent=2).encode(),
                "final_report.json": _json.dumps(report, ensure_ascii=False, indent=2).encode(),
                "eda_summary.json": _json.dumps(eda.get("summary") or {}, ensure_ascii=False, indent=2).encode(),
                "R/analyst_code.R": (eda.get("code") or "").encode(),
            }
            if model.get("code"):
                files["R/modeler_code.R"] = model["code"].encode()

            # Coleta artefatos do sandbox (outputs/): HTMLs do Quarto, CSVs, PNGs, etc.
            outputs_dir_str = eda.get("outputs_dir")
            if outputs_dir_str:
                outputs_dir = Path(outputs_dir_str)
                if outputs_dir.exists():
                    for p in outputs_dir.rglob("*"):
                        if p.is_file():
                            rel = p.relative_to(outputs_dir)
                            files[f"outputs/{rel}"] = p.read_bytes()

            # README.md do run (visibilidade pro usuário clonar e entender)
            files["README.md"] = _build_run_readme(state, report, eda, model).encode()

            project_name = f"run-{state.get('run_id','unknown')[:8]}"
            ok = _loop_run(github_manager.push_analysis(repo_url, project_name, files))
            push_status = "pushed" if ok else "failed"
        except Exception as e:
            logger.exception("github push error: %s", e)
            push_status = f"error:{type(e).__name__}"

    report["_github_push"] = push_status
    return {
        "final_report": report,
        "current_phase": "done",
        "status": "completed",
    }


def route_after_eda(state: ProjectState) -> str:
    """
    Roteia após EDA baseado no tipo de workflow.

    - data_quality: vai direto para report
    - eda_hypothesis: vai direto para report
    - full_ml: continua para decide_next → modeling
    """
    workflow_type = state.get("workflow_type", "full_ml")
    if workflow_type in ("data_quality", "eda_hypothesis"):
        logger.info("Workflow %s: skipping modeling, going to report.", workflow_type)
        return "report"
    return "decide_next"


def route_after_review(state: ProjectState) -> str:
    review = state.get("review_results") or {}
    decision = review.get("decision")
    loops = state.get("review_loop_count", 0)

    if decision == "approved":
        return "report"
    if loops >= MAX_REVIEW_LOOPS:
        logger.warning("Review rejected %d times, forcing report.", loops)
        return "report"
    # Reprovado e ainda dentro do limite → volta para modelagem
    return "modeling"
