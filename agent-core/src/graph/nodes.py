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


_STAGE_TO_CODE_PATH = {
    "quality": "R/01_quality.R",
    "hypothesis": "R/02_hypothesis.R",
    "ml": "R/03_ml.R",
}


def _stage_progress_line(stage: str, result: dict, committed: bool = True) -> str:
    """Uma linha pro body do PR refletindo progresso da stage."""
    if not result or not committed:
        return f"- ⏳ **{stage}** — pendente"
    if not result.get("success"):
        return f"- ⚠️ **{stage}** — terminou com falhas"
    s = result.get("summary") or {}
    detail = ""
    if stage == "quality":
        ds = s.get("dataset") or {}
        detail = (
            f" — {ds.get('rows_original','?')}→{ds.get('rows_final','?')} linhas, "
            f"{ds.get('cols_dropped',0)} colunas descartadas"
        )
    elif stage == "hypothesis":
        hyps = s.get("hypotheses") or []
        detail = f" — {len(hyps)} hipóteses avaliadas"
    elif stage == "ml":
        metrics = (result.get("metrics") or {}) if stage == "ml" else {}
        detail = f" — {len(metrics)} métricas" if metrics else ""
    return f"- ✅ **{stage}**{detail}"


def _build_pr_body(state: ProjectState, committed: list[str]) -> str:
    """Body do PR refletindo progresso de cada stage comitada."""
    run_id = state.get("run_id", "?")
    workflow = state.get("workflow_type", "?")
    issue_number = state.get("issue_number")

    lines = [
        f"**Run:** `{run_id}` · **Workflow:** `{workflow}`",
    ]
    if issue_number:
        lines.append(f"**Issue:** #{issue_number}")
    lines += ["", "## Progresso"]
    stages = ["quality", "hypothesis", "ml"]
    results_by_stage = {
        "quality": state.get("quality_results") or {},
        "hypothesis": state.get("hypothesis_results") or {},
        "ml": state.get("model_results") or {},
    }
    for st in stages:
        is_relevant = (
            st == "quality"
            or (st == "hypothesis" and workflow in {"eda_hypothesis", "full_ml"})
            or (st == "ml" and workflow == "full_ml")
        )
        if not is_relevant:
            continue
        lines.append(_stage_progress_line(st, results_by_stage[st], st in committed))

    lines += [
        "",
        "_PR aberto ao fim do run com todas as fases completas. "
        "Merge = run aprovado; fechar sem merge = rejeitado (issue volta pro backlog)._",
    ]
    return "\n".join(lines)


def _commit_stage_to_repo(
    state: ProjectState, stage: str, result: dict
) -> dict | None:
    """
    Commita artefatos da stage no branch `run/<short>`. Sprint-lamina 4/4:
    NÃO abre nem atualiza PR — isso é responsabilidade exclusiva de node_report
    ao fim do run (princípio "PR = entrega completa, não progresso parcial").

    Idempotente: se o branch existir, adiciona commit em cima. Falhas de push
    são logadas e retornam None.

    Retorna dict com `stages_committed` atualizado, ou None se pushou falhou
    ou a stage não teve sucesso.
    """
    from ..storage import github_manager
    import json as _json

    repo_url = state.get("github_repo")
    if not repo_url:
        return None
    if not result or not result.get("success"):
        logger.info("commit_stage: stage %s não teve sucesso, pulando push", stage)
        return None

    run_id = state.get("run_id", "unknown")
    run_short = run_id[:8] if isinstance(run_id, str) else "unknown"
    branch_name = f"run/{run_short}"
    code_path = _STAGE_TO_CODE_PATH.get(stage, f"R/{stage}.R")

    # Coleta arquivos da stage (reusa helper existente)
    files: dict[str, bytes] = {}
    _collect_stage_artifacts(files, result, stage, code_path)

    if not files:
        logger.info("commit_stage: stage %s sem arquivos para commitar", stage)
        return None

    # plan.json (primeira vez que commitar no run)
    stages_committed = list(state.get("stages_committed") or [])
    if not stages_committed:
        files["plan.json"] = _json.dumps(
            state.get("plan") or {}, ensure_ascii=False, indent=2
        ).encode()

    msg_lines = [f"[{stage}] Cortex run {run_short}"]
    summary = result.get("summary") or {}
    if stage == "quality" and summary.get("dataset"):
        ds = summary["dataset"]
        msg_lines.append(
            f"Qualidade: {ds.get('rows_original','?')}→{ds.get('rows_final','?')} linhas, "
            f"{ds.get('cols_dropped',0)} colunas descartadas"
        )
    elif stage == "hypothesis" and summary.get("hypotheses"):
        msg_lines.append(f"EDA: {len(summary['hypotheses'])} hipóteses testadas")
    elif stage == "ml" and result.get("metrics"):
        msg_lines.append(f"Modeling: {len(result['metrics'])} métricas registradas")
    commit_message = "\n".join(msg_lines)

    push_info = github_manager.push_to_branch(
        repo_url, branch_name, files, commit_message=commit_message
    )
    if not push_info.get("ok"):
        logger.error("commit_stage: push falhou para %s — %s",
                     stage, push_info.get("error"))
        return None

    stages_committed.append(stage)
    logger.info("commit_stage: %s comitado em %s", stage, branch_name)
    return {"stages_committed": stages_committed}


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


def _post_plan_comment(state: ProjectState, plan: dict) -> None:
    """
    Teammate mode: posta o plano aprovado como comentário na issue original do tick.
    Sem sub-issues, sem milestone — 1 issue = 1 PR.
    Falhas são logadas; não quebram o run.
    """
    from ..storage import github_pm

    repo_url = state.get("github_repo")
    issue_number = state.get("issue_number")
    if not repo_url or not issue_number:
        logger.info("post_plan_comment: sem repo ou issue_number; pulando")
        return

    run_id = state.get("run_id", "?")
    kind = state.get("issue_kind", "?")
    lines = [
        "## 🧠 Plano do Cortex",
        "",
        f"_Run:_ `{run_id}`  ·  _Kind:_ `{kind}`",
        "",
    ]
    if plan.get("summary"):
        lines += ["### Resumo", str(plan["summary"]), ""]

    phases = plan.get("phases") or []
    if phases:
        lines.append("### Fases planejadas")
        for p in phases:
            nm = p.get("name", "?")
            obj = p.get("objective", "")
            rat = p.get("rationale", "")
            lines.append(f"- **{nm}** — {obj}")
            if rat:
                lines.append(f"  _{rat}_")
        lines.append("")

    risks = plan.get("risks")
    if risks:
        lines.append("### Riscos / atenção")
        if isinstance(risks, list):
            for r in risks:
                lines.append(f"- {r}")
        else:
            lines.append(str(risks))
        lines.append("")

    feas = plan.get("feasibility")
    if feas:
        lines += ["### Viabilidade", str(feas), ""]

    lines.append(
        "_Comentário automático do Cortex após nó `planning`. "
        "PR será aberto apenas ao fim do run._"
    )

    try:
        ok = github_pm.comment_issue(repo_url, int(issue_number), "\n".join(lines))
        if not ok:
            logger.warning("post_plan_comment falhou em #%s", issue_number)
    except Exception as e:
        logger.exception("post_plan_comment exception: %s", e)


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
    _post_plan_comment(state, plan)
    return {
        "plan": plan,
        "current_phase": "planning",
        "status": "active",
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
    updates = {
        "quality_results": result,
        "current_phase": "quality",
        "stage_artifacts": _merge_stage_artifacts(
            state.get("stage_artifacts"), "quality", published
        ),
    }
    # Commit incremental no repo do cliente + fecha issue + atualiza draft PR
    state_for_commit = {**state, **updates}
    repo_updates = _commit_stage_to_repo(state_for_commit, "quality", result)
    if repo_updates:
        updates.update(repo_updates)
    return updates


def node_hypothesis(state: ProjectState) -> dict:
    result = _run_analyst_stage(state, "hypothesis")
    published = _publish_stage_artifacts(
        state.get("run_id", "unknown"), "hypothesis", result.get("outputs_dir")
    )
    updates = {
        "hypothesis_results": result,
        "current_phase": "hypothesis",
        "stage_artifacts": _merge_stage_artifacts(
            state.get("stage_artifacts"), "hypothesis", published
        ),
    }
    state_for_commit = {**state, **updates}
    repo_updates = _commit_stage_to_repo(state_for_commit, "hypothesis", result)
    if repo_updates:
        updates.update(repo_updates)
    return updates


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

    updates = {
        "model_results": result,
        "current_phase": "modeling",
        "status": "active",
        "stage_artifacts": _merge_stage_artifacts(
            state.get("stage_artifacts"), "ml", published
        ),
    }
    # Wrap modeler result num shape que _commit_stage_to_repo espera (success flag)
    ml_result = {**result, "success": result.get("exit_code") == 0 and bool(result.get("metrics"))}
    state_for_commit = {**state, **updates}
    repo_updates = _commit_stage_to_repo(state_for_commit, "ml", ml_result)
    if repo_updates:
        updates.update(repo_updates)
    return updates


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
    """
    Fecha o ciclo (sprint-lamina 4/4):
    1. Compila relatório final via orchestrator.
    2. Commita README + final_report.json no branch do run.
    3. **Abre UM PR ready-for-review** com body completo (progresso + resumo
       executivo + findings + recomendações). Se o PR abrir com sucesso,
       fecha a issue-driver. Se falhar (push ou open), libera só o claim e
       deixa a issue aberta pra re-tick.
    """
    import json as _json
    from ..storage import github_manager, github_pm

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
    finalize_ok = False
    repo_url = state.get("github_repo")
    if repo_url:
        try:
            run_id = state.get("run_id", "unknown")
            run_short = run_id[:8] if isinstance(run_id, str) else "unknown"
            branch_name = f"run/{run_short}"

            files: dict[str, bytes] = {
                "README.md": _build_run_readme(state, report).encode(),
                "final_report.json": _json.dumps(
                    report, ensure_ascii=False, indent=2
                ).encode(),
            }
            push_info = github_manager.push_to_branch(
                repo_url, branch_name, files,
                commit_message=f"Cortex run {run_short}: relatório final",
            )
            push_info["status"] = "pushed" if push_info.get("ok") else "failed"

            if push_info.get("ok"):
                body = _build_pr_body(
                    state, state.get("stages_committed") or []
                )
                body += (
                    "\n\n---\n\n## Resumo Executivo\n\n"
                    + (report.get("executive_summary") or "_(vazio)_")
                )
                findings = report.get("key_findings") or []
                recs = report.get("recommendations") or []
                if findings:
                    body += "\n\n## 🔑 Principais Achados\n"
                    for f in findings[:10]:
                        body += f"- {_fmt_finding(f)}\n"
                if recs:
                    body += "\n\n## 🎯 Recomendações\n"
                    for r in recs[:10]:
                        body += f"- {_fmt_recommendation(r)}\n"

                title = f"Cortex run {run_short} ({state.get('workflow_type','?')})"
                pr_info = github_pm.create_pr(
                    repo_url,
                    head=branch_name,
                    base="main",
                    title=title,
                    body=body,
                    draft=False,
                )
                if pr_info:
                    finalize_ok = True
                else:
                    logger.error(
                        "node_report: push ok mas create_pr falhou — "
                        "issue ficará aberta pra re-tick."
                    )

        except Exception as e:
            logger.exception("github report-commit error: %s", e)
            push_info = {"status": f"error:{type(e).__name__}", "error": str(e)}

    # Libera claim sempre. Fecha issue apenas se TUDO deu certo (push + PR).
    if repo_url:
        _close_issue_driven_cycle(state, repo_url, push_ok=finalize_ok)

    report["_github_push"] = push_info
    if pr_info is not None:
        report["_github_pr"] = pr_info
    return {
        "final_report": report,
        "run_pr": pr_info,
        "current_phase": "done",
        "status": "completed",
    }


def _close_issue_driven_cycle(
    state: ProjectState, repo_url: str, *, push_ok: bool
) -> None:
    """
    Fecha o ciclo da issue-driver no GitHub ao final do run.

    - Libera o claim (`cortex:in-progress`) **sempre** que há `issue_number`
      no state, mesmo se o push falhou. Sem isso a issue fica travada e
      ninguém — nem humano, nem outro agente — consegue retomar o trabalho.
    - Fecha a issue (`state=closed`) só se o push deu certo. Se falhou,
      deixa aberta pra o próximo tick pegar de novo e tentar recuperar.

    No-op silencioso se o run não é issue-driven (runs legados via
    POST /v1/runs não populam `issue_number`).
    """
    from ..storage import github_pm

    issue_number = state.get("issue_number")
    if not issue_number:
        return
    try:
        github_pm.release_claim(repo_url, int(issue_number))
        if push_ok:
            github_pm.close_issue(repo_url, int(issue_number), reason="completed")
    except Exception as e:
        # Best-effort: se falhar aqui, o run já fez o trabalho útil; o humano
        # consegue limpar manualmente via botão de close/remover label.
        logger.warning(
            "close_issue_driven_cycle issue=#%s falhou: %s", issue_number, e
        )


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
