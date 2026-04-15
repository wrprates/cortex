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
from ..agents.decision_maker import run_decision_maker
from .probe import run_probe
from .state import ProjectState

logger = logging.getLogger(__name__)

MAX_REVIEW_LOOPS = 2


def _download_inputs(datasets: list[str]) -> dict[str, bytes]:
    """Baixa datasets s3:// do MinIO e retorna {filename: bytes}. Raise em URI inválida ou erro."""
    from ..storage import minio_client
    import os

    inputs: dict[str, bytes] = {}
    for uri in datasets:
        if not uri.startswith("s3://"):
            raise ValueError(f"URI de dataset não suportada (use s3://): {uri}")
        path = uri[5:]
        parts = path.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"URI de dataset inválida (esperado s3://bucket/key): {uri}")
        key = parts[1]
        data = minio_client.get_bytes(key)
        inputs[os.path.basename(key)] = data
        logger.info("Dataset downloaded: %s (%d bytes)", key, len(data))
    return inputs


def _fmt_finding(f) -> str:
    if isinstance(f, dict):
        obs = f.get("finding") or ""
        sig = f.get("significance") or ""
        act = f.get("recommended_action") or ""
        return f"**{obs}** — {sig} _Ação:_ {act}".strip()
    return str(f)


def _fmt_recommendation(r) -> str:
    if isinstance(r, dict):
        action = r.get("action") or ""
        just = r.get("justification") or ""
        impact = r.get("expected_impact") or ""
        return f"**{action}** — {just} (_Impacto esperado:_ {impact})".strip()
    return str(r)


def _stage_line(stage_key: str, stage_result: dict, code_path: str) -> str | None:
    """Descreve uma stage que rodou, para o README."""
    if not stage_result:
        return None
    attempts = stage_result.get("attempts") or []
    status = "✅"
    if stage_result.get("skipped"):
        reason = stage_result.get("skip_reason") or "decisão do decision_maker"
        status = f"⏭️ pulado — {reason}"
    elif not stage_result.get("success"):
        status = "⚠️ terminou com falhas (ver stderr)"
    return (
        f"- **{stage_key}** — {status} · {len(attempts)} tentativas · "
        f"código: `{code_path}` · relatório: `outputs/{stage_key}.html`"
    )


def _build_run_readme(state, report) -> str:
    run_id = state.get("run_id", "unknown")
    plan = state.get("plan") or {}
    quality = state.get("quality_results") or {}
    hypothesis = state.get("hypothesis_results") or {}
    model = state.get("model_results") or {}

    title = report.get("title") or "Análise"
    subtitle = report.get("subtitle") or ""
    exec_sum = report.get("executive_summary") or ""
    findings = report.get("key_findings") or []
    conclusions = report.get("conclusions") or ""
    recommendations = report.get("recommendations") or []
    caveats = report.get("caveats") or []
    workflow = state.get("workflow_type", "?")
    quality_verdict = report.get("quality_verdict", "n/a")

    lines = [f"# {title}"]
    if subtitle:
        lines.append(f"_{subtitle}_")
    lines += [
        "",
        f"**Run:** `{run_id}` · **Workflow:** `{workflow}` · "
        f"**Veredito de qualidade:** `{quality_verdict}`",
        "",
        "## 🧭 Etapas executadas",
    ]
    for line in filter(
        None,
        [
            _stage_line("quality", quality, "R/01_quality.R"),
            _stage_line("hypothesis", hypothesis, "R/02_hypothesis.R"),
            _stage_line("ml", model, "R/03_ml.R") if model else None,
        ],
    ):
        lines.append(line)
    lines += [
        "",
        "## 📊 Entregáveis neste branch",
        "- `outputs/quality.html` — relatório de qualidade dos dados",
        "- `outputs/hypothesis.html` — EDA por hipóteses (se workflow != data_quality)",
    ]
    if model:
        lines.append("- `outputs/ml.html` — modelagem (se workflow = full_ml)")
    lines += [
        "- `outputs/*_summary.json` — sumário estruturado por etapa",
        "- `R/0N_<etapa>.R` — código R de cada etapa",
        "- `plan.json` / `final_report.json` — metadados do run",
        "",
        "## 📝 Resumo Executivo",
        exec_sum or "_(vazio)_",
        "",
    ]
    if findings:
        lines.append("## 🔑 Principais Achados")
        for f in findings[:10]:
            lines.append(f"- {_fmt_finding(f)}")
        lines.append("")
    if conclusions:
        lines += ["## ✅ Conclusões", str(conclusions), ""]
    if recommendations:
        lines.append("## 🎯 Recomendações")
        for r in recommendations[:10]:
            lines.append(f"- {_fmt_recommendation(r)}")
        lines.append("")
    if caveats:
        lines.append("## ⚠️ Ressalvas")
        for c in caveats[:10]:
            lines.append(f"- {c}")
        lines.append("")
    lines += ["## 🗺️ Fases Planejadas"]
    for i, ph in enumerate(plan.get("phases") or [], 1):
        nm = ph.get("name", "?")
        rat = ph.get("rationale") or ""
        ap = "🔐" if ph.get("requires_human_approval") else "•"
        line = f"{i}. {ap} **{nm}**"
        if rat:
            line += f" — {rat}"
        lines.append(line)
    lines += [
        "",
        "---",
        f"Gerado pelo **Cortex** — run `{run_id}`. Para abrir um PR contra `main`, "
        f"navegue até o branch `run/{run_id[:8] if isinstance(run_id,str) else ''}` e use o botão **Compare & pull request** do GitHub.",
    ]
    return "\n".join(lines)


def node_probe(state: ProjectState) -> dict:
    """
    Inspeciona os datasets antes de planejar. Produz dataset_profile usado pelo
    orchestrator para julgar viabilidade com informação real, não só do briefing.
    """
    datasets = state.get("datasets", [])
    if not datasets:
        logger.warning("node_probe: sem datasets no state, pulando probe.")
        return {"dataset_profile": {"datasets": [], "_skipped": "no_datasets"}, "current_phase": "probing"}

    inputs = _download_inputs(datasets)
    profile = run_probe(inputs)
    logger.info("probe ok: %d datasets perfilados", len(profile.get("datasets", [])))
    return {"dataset_profile": profile, "current_phase": "probing"}


def node_plan(state: ProjectState) -> dict:
    workflow_type = state.get("workflow_type", "full_ml")
    plan = run_orchestrator(
        "plan",
        context={
            "description": state.get("description"),
            "datasets": state.get("datasets", []),
            "workflow_type": workflow_type,
            "dataset_profile": state.get("dataset_profile"),
        },
    )
    return {"plan": plan, "current_phase": "planning", "status": "waiting_human"}


_STAGE_TASK = {
    "quality": "Execute a fase de QUALIDADE DE DADOS conforme o plano aprovado.",
    "hypothesis": "Execute a fase de EDA POR HIPÓTESES conforme o plano aprovado.",
}


def _run_analyst_stage(state: ProjectState, stage: str) -> dict:
    """Roda o analyst_r para a stage dada; consulta decision_maker em caso de falha."""
    datasets = state.get("datasets", [])
    inputs = _download_inputs(datasets)

    context = {
        "plan": state.get("plan"),
        "stage": stage,
        "datasets": datasets,
        "available_inputs": list(inputs.keys()),
        "dataset_profile": state.get("dataset_profile"),
        "quality_summary": (state.get("quality_results") or {}).get("summary"),
    }

    def _call(extra_guidance: str = "") -> dict:
        task = _STAGE_TASK[stage]
        if extra_guidance:
            task = f"{task}\n\nOrientação do decision_maker:\n{extra_guidance}"
        return run_analyst_r(task=task, context=context, inputs=inputs, stage=stage)

    result = _call()

    if not result.get("success"):
        decision = run_decision_maker(
            failing_agent=f"analyst_r[{stage}]",
            objective=_STAGE_TASK[stage],
            attempts=result.get("attempts", []),
            stderr_tail=result.get("stderr_tail", ""),
            stdout_tail=result.get("stdout_tail", ""),
            profile=state.get("dataset_profile"),
        )
        logger.warning(
            "decision_maker[%s]: %s — %s",
            stage, decision.get("action"), decision.get("rationale"),
        )

        action = decision.get("action")
        if action == "retry_with_guidance":
            retry = _call(extra_guidance=decision.get("guidance", ""))
            retry["_decision"] = decision
            result = retry
        elif action == "skip":
            result["_decision"] = decision
            result["skipped"] = True
            result["skip_reason"] = decision.get("rationale", "infeasible")
        elif action == "abort":
            raise RuntimeError(
                f"Stage {stage!r} abortada pelo decision_maker: {decision.get('rationale')}"
            )
        else:
            logger.error("decision_maker retornou action desconhecida: %s", action)
            result["_decision"] = decision

    return result


def node_quality(state: ProjectState) -> dict:
    result = _run_analyst_stage(state, "quality")
    return {"quality_results": result, "current_phase": "quality"}


def node_hypothesis(state: ProjectState) -> dict:
    result = _run_analyst_stage(state, "hypothesis")
    return {"hypothesis_results": result, "current_phase": "hypothesis"}


def node_decide_next(state: ProjectState) -> dict:
    decision = run_orchestrator(
        "decide_next",
        context={
            "plan": state.get("plan"),
            "quality_results": state.get("quality_results"),
            "hypothesis_results": state.get("hypothesis_results"),
            "model_results": state.get("model_results"),
            "review_results": state.get("review_results"),
        },
    )
    return {"plan": {**state.get("plan", {}), "_next_decision": decision}}


def node_modeling(state: ProjectState) -> dict:
    language = state.get("primary_language", "r")

    context = {
        "plan": state.get("plan"),
        "quality_summary": (state.get("quality_results") or {}).get("summary"),
        "hypothesis_summary": (state.get("hypothesis_results") or {}).get("summary"),
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
            "quality_results": state.get("quality_results"),
            "hypothesis_results": state.get("hypothesis_results"),
            "model_results": state.get("model_results"),
        }
    )
    loops = state.get("review_loop_count", 0) + 1
    return {
        "review_results": result,
        "current_phase": "review",
        "review_loop_count": loops,
    }


def _collect_stage_artifacts(
    files: dict[str, bytes],
    stage_result: dict,
    stage_key: str,
    code_path: str,
) -> None:
    """
    Coleta artefatos de uma stage para o dict `files`:

    - Copia o código R para `code_path` (ex: "R/01_quality.R").
    - Renomeia `outputs/report.html` da stage para `outputs/{stage_key}.html`.
    - Renomeia `outputs/summary.json` para `outputs/{stage_key}_summary.json`.
    - Copia qualquer outro artefato mantendo o nome em `outputs/`.
    """
    from pathlib import Path

    if not stage_result:
        return

    code = stage_result.get("code") or ""
    if code:
        files[code_path] = code.encode()

    outputs_dir_str = stage_result.get("outputs_dir")
    if not outputs_dir_str:
        return
    outputs_dir = Path(outputs_dir_str)
    if not outputs_dir.exists():
        return

    for p in outputs_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(outputs_dir)
        name = rel.name
        if name == "report.html":
            dest = f"outputs/{stage_key}.html"
        elif name == "summary.json":
            dest = f"outputs/{stage_key}_summary.json"
        elif name == "analysis.rds":
            dest = f"outputs/{stage_key}_analysis.rds"
        else:
            dest = f"outputs/{rel}"
        files[dest] = p.read_bytes()


def node_report(state: ProjectState) -> dict:
    import json as _json
    from ..storage import github_manager

    quality = state.get("quality_results") or {}
    hypothesis = state.get("hypothesis_results") or {}
    model = state.get("model_results") or {}
    plan = state.get("plan") or {}

    report = run_orchestrator(
        "compile_report",
        context={
            "plan": plan,
            "quality_results": quality,
            "hypothesis_results": hypothesis,
            "model_results": model,
            "review_results": state.get("review_results"),
        },
    )

    push_info: dict = {"status": "skipped"}
    repo_url = state.get("github_repo")
    if repo_url:
        try:
            run_id = state.get("run_id", "unknown")
            run_short = run_id[:8] if isinstance(run_id, str) else "unknown"

            files: dict[str, bytes] = {
                "plan.json": _json.dumps(plan, ensure_ascii=False, indent=2).encode(),
                "final_report.json": _json.dumps(report, ensure_ascii=False, indent=2).encode(),
            }
            _collect_stage_artifacts(files, quality, "quality", "R/01_quality.R")
            _collect_stage_artifacts(files, hypothesis, "hypothesis", "R/02_hypothesis.R")
            if model:
                _collect_stage_artifacts(files, model, "ml", "R/03_ml.R")

            files["README.md"] = _build_run_readme(state, report).encode()

            branch_name = f"run/{run_short}"
            n_quality_attempts = len((quality.get("attempts") or []))
            n_hypothesis_attempts = len((hypothesis.get("attempts") or []))
            commit_msg = (
                f"Cortex run {run_short}: {state.get('workflow_type','?')} "
                f"(quality={n_quality_attempts} tent., hypothesis={n_hypothesis_attempts} tent.)"
            )
            push_info = github_manager.push_to_branch(
                repo_url, branch_name, files, commit_message=commit_msg
            )
            push_info["status"] = "pushed" if push_info.get("ok") else "failed"
        except Exception as e:
            logger.exception("github push error: %s", e)
            push_info = {"status": f"error:{type(e).__name__}", "error": str(e)}

    report["_github_push"] = push_info
    return {
        "final_report": report,
        "current_phase": "done",
        "status": "completed",
    }


def route_after_quality(state: ProjectState) -> str:
    """Após qualidade, vai pra hipótese (eda_hypothesis|full_ml) ou direto pro report (data_quality)."""
    workflow_type = state.get("workflow_type", "full_ml")
    if workflow_type == "data_quality":
        return "report"
    return "hypothesis"


def route_after_hypothesis(state: ProjectState) -> str:
    """Após hipóteses, vai pra modelagem (full_ml) ou report (eda_hypothesis)."""
    workflow_type = state.get("workflow_type", "full_ml")
    if workflow_type == "full_ml":
        return "decide_next"
    return "report"


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
