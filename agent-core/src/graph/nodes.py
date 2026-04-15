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
    attempts = eda.get("attempts") or []
    workflow = state.get("workflow_type", "?")
    quality_verdict = report.get("quality_verdict", "n/a")

    lines = [f"# {title}"]
    if subtitle:
        lines.append(f"_{subtitle}_")
    lines += [
        "",
        f"**Run:** `{run_id}` · **Workflow:** `{workflow}` · "
        f"**Veredito de qualidade:** `{quality_verdict}` · "
        f"**Tentativas:** {len(attempts)}",
        "",
        "## 📊 Entregáveis neste branch",
        "- `outputs/report.html` — relatório Quarto interativo **(abra no browser para ver os insights)**",
        "- `outputs/*.html` — widgets complementares (se houver)",
        "- `outputs/*.csv` / `*.parquet` — tabelas de apoio",
        "- `R/01_analyst.R` — código R da análise",
    ]
    if model.get("code"):
        lines.append("- `R/02_modeler.R` — código R da modelagem")
    lines += [
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


def node_eda(state: ProjectState) -> dict:
    language = state.get("primary_language", "r")
    workflow_type = state.get("workflow_type", "full_ml")
    datasets = state.get("datasets", [])

    inputs = _download_inputs(datasets)

    context = {
        "plan": state.get("plan"),
        "datasets": datasets,
        "available_inputs": list(inputs.keys()),
        "dataset_profile": state.get("dataset_profile"),
    }

    def _run_analyst(extra_guidance: str = "") -> dict:
        task = "Execute EDA conforme o plano aprovado."
        if extra_guidance:
            task = f"{task}\n\nOrientação do decision_maker:\n{extra_guidance}"
        if language == "r":
            return run_analyst_r(
                task=task, context=context, inputs=inputs, workflow_type=workflow_type
            )
        return run_analyst(task=task, context=context, inputs=inputs)

    result = _run_analyst()

    # Se falhou após os retries internos do analyst, consulta decision_maker.
    if not result.get("success"):
        decision = run_decision_maker(
            failing_agent="analyst_r" if language == "r" else "analyst",
            objective="EDA conforme plano aprovado",
            attempts=result.get("attempts", []),
            stderr_tail=result.get("stderr_tail", ""),
            stdout_tail=result.get("stdout_tail", ""),
            profile=state.get("dataset_profile"),
        )
        logger.warning("decision_maker: %s — %s", decision.get("action"), decision.get("rationale"))

        action = decision.get("action")
        if action == "retry_with_guidance":
            result_retry = _run_analyst(extra_guidance=decision.get("guidance", ""))
            result_retry["_decision"] = decision
            result = result_retry
        elif action == "skip":
            result["_decision"] = decision
            result["skipped"] = True
            result["skip_reason"] = decision.get("rationale", "infeasible")
        elif action == "abort":
            raise RuntimeError(
                f"EDA abortado pelo decision_maker: {decision.get('rationale')}"
            )
        else:
            logger.error("decision_maker retornou action desconhecida: %s", action)
            result["_decision"] = decision

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
    import json as _json
    from pathlib import Path
    from ..storage import github_manager

    report = run_orchestrator(
        "compile_report",
        context={
            "plan": state.get("plan"),
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
            "review_results": state.get("review_results"),
        },
    )

    push_info: dict = {"status": "skipped"}
    repo_url = state.get("github_repo")
    if repo_url:
        try:
            eda = state.get("eda_results") or {}
            model = state.get("model_results") or {}
            plan = state.get("plan") or {}
            run_id = state.get("run_id", "unknown")
            run_short = run_id[:8] if isinstance(run_id, str) else "unknown"

            # Arquivos vão direto na raiz da branch — sem subpasta por run.
            files: dict[str, bytes] = {
                "plan.json": _json.dumps(plan, ensure_ascii=False, indent=2).encode(),
                "final_report.json": _json.dumps(report, ensure_ascii=False, indent=2).encode(),
                "eda_summary.json": _json.dumps(
                    eda.get("summary") or {}, ensure_ascii=False, indent=2
                ).encode(),
                "R/01_analyst.R": (eda.get("code") or "").encode(),
            }
            if model.get("code"):
                files["R/02_modeler.R"] = model["code"].encode()

            # Artefatos do sandbox vão para outputs/ na raiz
            outputs_dir_str = eda.get("outputs_dir")
            if outputs_dir_str:
                outputs_dir = Path(outputs_dir_str)
                if outputs_dir.exists():
                    for p in outputs_dir.rglob("*"):
                        if p.is_file():
                            rel = p.relative_to(outputs_dir)
                            files[f"outputs/{rel}"] = p.read_bytes()

            files["README.md"] = _build_run_readme(state, report, eda, model).encode()

            branch_name = f"run/{run_short}"
            commit_msg = (
                f"Cortex run {run_short}: {state.get('workflow_type','?')} "
                f"({len(eda.get('attempts') or [])} tentativas)"
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
