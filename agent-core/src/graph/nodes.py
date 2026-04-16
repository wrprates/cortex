from __future__ import annotations

import logging
import os

from ..agents import (
    run_analyst_r,
    run_modeler_r,
    run_orchestrator,
    run_quality_dispatcher,
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


# Extensões que viajam no bus entre fases (dados estruturados; exclui HTML/QMD
# que são relatórios pesados e não servem de input para a próxima fase).
_HANDOFF_EXTS = {".parquet", ".rds", ".csv", ".json", ".feather", ".arrow"}


def _publish_stage_artifacts(
    run_id: str, stage: str, outputs_dir_str: str | None
) -> dict[str, str]:
    """
    Publica artefatos "de handoff" da stage no MinIO sob a chave
    `stage_outputs/<run_id>/<stage>/<filename>` e devolve {filename: s3_uri}.

    Só sobe arquivos com extensão em `_HANDOFF_EXTS` — o relatório HTML
    permanece apenas no artefato do branch do run (via `_collect_stage_artifacts`).
    """
    if not outputs_dir_str:
        return {}

    from pathlib import Path

    from ..storage import minio_client

    outputs_dir = Path(outputs_dir_str)
    if not outputs_dir.exists():
        return {}

    published: dict[str, str] = {}
    for p in outputs_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _HANDOFF_EXTS:
            continue
        # summary.json é renomeado para deixar claro de qual stage veio quando
        # múltiplos summaries coexistirem no ./inputs/ da próxima fase.
        if p.name == "summary.json":
            dest_name = f"{stage}_summary.json"
        elif p.name == "analysis.rds":
            dest_name = f"{stage}_analysis.rds"
        else:
            dest_name = p.name
        key = f"stage_outputs/{run_id}/{stage}/{dest_name}"
        try:
            uri = minio_client.put_bytes(key, p.read_bytes())
            published[dest_name] = uri
            logger.info("stage_artifact published: %s", uri)
        except Exception as e:
            logger.exception("falha publicando %s: %s", key, e)
    return published


def _collect_stage_inputs(state: ProjectState) -> dict[str, bytes]:
    """
    Baixa datasets originais + artefatos das fases anteriores publicados no bus.

    Retorna dict{filename: bytes} pronto pra passar como `inputs=` do sandbox.
    Se dois arquivos colidirem (ex.: dataset original e artefato chamado igual),
    o artefato da stage mais recente sobrescreve — é o comportamento desejado
    já que fases posteriores devem ver o estado mais trabalhado do dado.
    """
    from ..storage import minio_client

    inputs = _download_inputs(state.get("datasets", []))

    stage_artifacts = state.get("stage_artifacts") or {}
    for stage, files in stage_artifacts.items():
        for filename, uri in (files or {}).items():
            if not uri.startswith("s3://"):
                logger.warning("URI inválida no bus (%s/%s): %s", stage, filename, uri)
                continue
            path = uri[5:]
            parts = path.split("/", 1)
            if len(parts) != 2:
                continue
            key = parts[1]
            try:
                inputs[filename] = minio_client.get_bytes(key)
                logger.info("stage_artifact fetched: %s -> ./inputs/%s", uri, filename)
            except Exception as e:
                logger.exception("falha baixando artefato %s: %s", uri, e)
    return inputs


def _merge_stage_artifacts(
    existing: dict | None, stage: str, published: dict[str, str]
) -> dict:
    """Merge imutável: devolve novo dict com a stage atualizada."""
    merged = dict(existing or {})
    if published:
        merged[stage] = published
    return merged


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


_STAGES_BY_WORKFLOW = {
    "data_quality": ["quality"],
    "eda_hypothesis": ["quality", "hypothesis"],
    "full_ml": ["quality", "hypothesis", "ml"],
}


def _planned_stages(workflow_type: str) -> list[str]:
    return _STAGES_BY_WORKFLOW.get(workflow_type, ["quality", "hypothesis", "ml"])


def _issue_body_for_stage(stage: str, plan: dict, workflow_type: str) -> str:
    """Corpo da issue de uma stage — mostra objetivos e contexto do plano."""
    phases = plan.get("phases") or []
    relevant_names = {
        "quality": ("quality", "qualidade"),
        "hypothesis": ("hypothesis", "hipótese", "eda"),
        "ml": ("model", "ml", "machine learning"),
    }.get(stage, (stage,))
    matches = [
        p for p in phases
        if any(k in (p.get("name", "").lower()) for k in relevant_names)
    ]
    lines = [
        f"## Etapa: **{stage}**",
        "",
        f"_Workflow do projeto: `{workflow_type}`_",
        "",
        "### Objetivos desta etapa (derivados do plano)",
    ]
    if matches:
        for p in matches:
            nm = p.get("name", "?")
            obj = p.get("objective", "")
            rat = p.get("rationale", "")
            lines.append(f"- **{nm}** — {obj}")
            if rat:
                lines.append(f"  _{rat}_")
    else:
        lines.append("_Nenhuma fase do plano casou com esta etapa; "
                     "verifique viabilidade._")
    lines += [
        "",
        "### Entregáveis esperados no branch do run",
        f"- `R/0N_{stage}.R` — código fonte",
        f"- `outputs/{stage}.html` — relatório Quarto interativo",
        f"- `outputs/{stage}_summary.json` — sumário estruturado",
        "",
        "_Issue gerada automaticamente pelo Cortex após aprovação do plano. "
        "Será fechada quando o PR do run for mergeado._",
    ]
    return "\n".join(lines)


def _create_plan_issues(state: ProjectState, plan: dict) -> tuple[dict, int | None]:
    """
    Cria milestone (1 por projeto) e issues (1 por stage). Idempotente no milestone.

    Retorna (plan_issues, milestone_number). plan_issues = {stage: issue_number}.
    Falhas são logadas mas NÃO levantam — gestão no GitHub é valor agregado,
    não deve quebrar o run.
    """
    from ..storage import github_pm

    repo_url = state.get("github_repo")
    if not repo_url:
        return {}, None

    workflow_type = state.get("workflow_type", "full_ml")
    stages = _planned_stages(workflow_type)
    project_id = state.get("project_id", "?")
    run_id = state.get("run_id", "?")
    ms_title = f"Projeto {project_id[:8] if isinstance(project_id, str) else '?'}"
    ms_desc = (
        f"Milestone do projeto `{project_id}`. Reúne as etapas de ciência de "
        "dados executadas pelo Cortex."
    )

    try:
        milestone_number = github_pm.ensure_milestone(repo_url, ms_title, ms_desc)
    except Exception as e:
        logger.exception("ensure_milestone falhou: %s", e)
        milestone_number = None

    plan_issues: dict[str, int] = {}
    for stage in stages:
        title = f"[{stage}] run {run_id[:8] if isinstance(run_id, str) else '?'}"
        body = _issue_body_for_stage(stage, plan, workflow_type)
        try:
            num = github_pm.create_issue(
                repo_url,
                title=title,
                body=body,
                milestone=milestone_number,
                labels=[f"stage:{stage}", "cortex"],
            )
            if num is not None:
                plan_issues[stage] = num
        except Exception as e:
            logger.exception("create_issue(%s) falhou: %s", stage, e)

    return plan_issues, milestone_number


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
    plan_issues, milestone_number = _create_plan_issues(state, plan)
    return {
        "plan": plan,
        "plan_issues": plan_issues,
        "milestone_number": milestone_number,
        "current_phase": "planning",
        "status": "waiting_human",
    }


_STAGE_TASK = {
    "quality": "Execute a fase de QUALIDADE DE DADOS conforme o plano aprovado.",
    "hypothesis": "Execute a fase de EDA POR HIPÓTESES conforme o plano aprovado.",
}


def _run_analyst_stage(state: ProjectState, stage: str) -> dict:
    """Roda o analyst_r para a stage dada; consulta decision_maker em caso de falha."""
    datasets = state.get("datasets", [])
    inputs = _collect_stage_inputs(state)

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
    # Feature flag: QUALITY_DISPATCHER=1 usa pipeline determinístico
    # (classificação Python + R templatizado, sem LLM). Se desativada,
    # cai no fluxo antigo de geração LLM + retries (analyst_r).
    use_dispatcher = os.getenv("QUALITY_DISPATCHER", "0") == "1"
    if use_dispatcher and state.get("dataset_profile"):
        inputs = _collect_stage_inputs(state)
        try:
            result = run_quality_dispatcher(
                dataset_profile=state["dataset_profile"],
                inputs=inputs,
            )
            logger.info("node_quality: dispatcher determinístico — success=%s",
                        result.get("success"))
        except Exception as e:
            # Em caso de problema no dispatcher, voltamos ao fluxo LLM
            # para não travar o run. Loga o motivo.
            logger.exception("quality_dispatcher falhou, caindo no analyst_r: %s", e)
            result = _run_analyst_stage(state, "quality")
    else:
        result = _run_analyst_stage(state, "quality")

    published = _publish_stage_artifacts(
        state.get("run_id", "unknown"), "quality", result.get("outputs_dir")
    )
    return {
        "quality_results": result,
        "current_phase": "quality",
        "stage_artifacts": _merge_stage_artifacts(
            state.get("stage_artifacts"), "quality", published
        ),
    }


def node_hypothesis(state: ProjectState) -> dict:
    result = _run_analyst_stage(state, "hypothesis")
    published = _publish_stage_artifacts(
        state.get("run_id", "unknown"), "hypothesis", result.get("outputs_dir")
    )
    return {
        "hypothesis_results": result,
        "current_phase": "hypothesis",
        "stage_artifacts": _merge_stage_artifacts(
            state.get("stage_artifacts"), "hypothesis", published
        ),
    }


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
    inputs = _collect_stage_inputs(state)
    context = {
        "plan": state.get("plan"),
        "quality_summary": (state.get("quality_results") or {}).get("summary"),
        "hypothesis_summary": (state.get("hypothesis_results") or {}).get("summary"),
        "available_inputs": list(inputs.keys()),
    }

    result = run_modeler_r(
        task="Treine e compare modelos baseline conforme o plano.",
        context=context,
        inputs=inputs,
        final_training=False,
    )

    published = _publish_stage_artifacts(
        state.get("run_id", "unknown"), "ml", result.get("outputs_dir")
    )

    return {
        "model_results": result,
        "current_phase": "modeling",
        "status": "waiting_human",
        "stage_artifacts": _merge_stage_artifacts(
            state.get("stage_artifacts"), "ml", published
        ),
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


def _open_run_pr(
    state: ProjectState, report: dict, branch_name: str, run_short: str
) -> dict | None:
    """Abre PR run/xxx → main com 'Closes #N' para cada issue de stage, e comenta."""
    from ..storage import github_pm

    repo_url = state.get("github_repo")
    if not repo_url:
        return None

    plan_issues = state.get("plan_issues") or {}
    workflow = state.get("workflow_type", "?")
    title = f"Cortex run {run_short} ({workflow})"

    stages_that_ran: list[str] = []
    if state.get("quality_results"):
        stages_that_ran.append("quality")
    if state.get("hypothesis_results"):
        stages_that_ran.append("hypothesis")
    if state.get("model_results"):
        stages_that_ran.append("ml")

    closes_lines = [
        f"Closes #{plan_issues[s]}" for s in stages_that_ran if s in plan_issues
    ]
    exec_sum = (report.get("executive_summary") or "_(vazio)_").strip()
    quality_verdict = report.get("quality_verdict", "n/a")

    body_parts = [
        *closes_lines,
        "",
        f"**Workflow:** `{workflow}` · **Veredito de qualidade:** `{quality_verdict}`",
        "",
        "## Resumo Executivo",
        exec_sum,
        "",
        "## Entregáveis neste branch",
        "- `outputs/*.html` — relatórios Quarto interativos por etapa",
        "- `R/0N_<etapa>.R` — código fonte",
        "- `outputs/*_summary.json` — sumário estruturado",
        "",
        f"_Gerado automaticamente pelo Cortex. Run `{state.get('run_id')}`._",
    ]
    body = "\n".join(body_parts)

    try:
        pr = github_pm.create_pr(
            repo_url, head=branch_name, base="main", title=title, body=body
        )
    except Exception as e:
        logger.exception("create_pr falhou: %s", e)
        return {"status": "error", "error": str(e)}

    if pr is None:
        return {"status": "failed"}

    # Comentário adicional com findings (o corpo já tem exec summary).
    findings = report.get("key_findings") or []
    recommendations = report.get("recommendations") or []
    if findings or recommendations:
        parts = ["## 🔑 Principais Achados"]
        for f in findings[:10]:
            parts.append(f"- {_fmt_finding(f)}")
        if recommendations:
            parts += ["", "## 🎯 Recomendações"]
            for r in recommendations[:10]:
                parts.append(f"- {_fmt_recommendation(r)}")
        try:
            github_pm.comment_pr(repo_url, pr["number"], "\n".join(parts))
        except Exception as e:
            logger.warning("comment_pr falhou: %s", e)

    pr["status"] = "opened"
    return pr


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
    pr_info: dict | None = None
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

            # PR run/xxx → main + comentário com exec summary.
            if push_info.get("ok"):
                pr_info = _open_run_pr(state, report, branch_name, run_short)
        except Exception as e:
            logger.exception("github push error: %s", e)
            push_info = {"status": f"error:{type(e).__name__}", "error": str(e)}

    report["_github_push"] = push_info
    if pr_info is not None:
        report["_github_pr"] = pr_info
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
