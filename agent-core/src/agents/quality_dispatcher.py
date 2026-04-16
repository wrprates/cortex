"""
Quality Dispatcher — pipeline determinístico de qualidade de dados.

Em vez de pedir ao LLM que gere código R do zero a cada run, este módulo:

1. Lê o `dataset_profile` (produzido pelo node_probe) ou re-profila os inputs.
2. Classifica cada coluna semanticamente (numeric | integer | boolean | date |
   categorical | text | id | constant | all_missing) usando heurísticas
   determinísticas — **zero chamada LLM**.
3. Escreve `__column_types.json` no workspace dos inputs.
4. Executa o script R fixo em `templates/r_quality/quality_main.R` no sandbox,
   que aplica a função de tratamento apropriada por coluna.
5. Retorna um resultado no mesmo formato do `run_analyst_r` para que
   `node_quality` consiga consumir sem mudar contrato.

**Princípio**: operações conhecidas (ex.: imputar mediana, tratar datas)
não precisam ser reinventadas pelo LLM. Só a narrativa do report passa
pelo LLM — e isso é tarefa do orchestrator, não deste módulo.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..sandbox.runner import run_code

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "templates" / "r_quality" / "quality_main.R"
)


def _classify_column(col_info: dict[str, Any], total_rows: int) -> str:
    """
    Heurística determinística de tipo semântico a partir do profile de uma coluna.

    `col_info` vem do `run_probe()` — tem: name, dtype (R class), missing_pct,
    cardinality, sample (lista de strings).
    """
    name = col_info.get("name", "")
    dtype = str(col_info.get("dtype", "")).lower()
    missing_pct = float(col_info.get("missing_pct") or 0)
    cardinality = int(col_info.get("cardinality") or 0)
    sample = [str(s) for s in (col_info.get("sample") or [])]

    # Casos extremos primeiro
    if missing_pct >= 99.5:
        return "all_missing"
    if cardinality <= 1:
        return "constant"

    # Nome sugere id
    name_lower = name.lower()
    looks_like_id = (
        name_lower == "id"
        or name_lower.endswith("_id")
        or name_lower.startswith("id_")
        or "uuid" in name_lower
    )
    if looks_like_id and total_rows > 0:
        # id de verdade: cardinalidade alta proporcional ao nrows
        if cardinality >= 0.95 * total_rows:
            return "id"

    # Date: R classes ou samples parseáveis
    if dtype in ("date", "posixct", "posixlt"):
        return "date"
    if sample and _looks_like_date(sample):
        return "date"

    # Boolean: logical ou cardinality 2 com valores típicos
    if dtype in ("logical", "boolean"):
        return "boolean"
    if cardinality == 2:
        norm = {s.strip().lower() for s in sample}
        if norm & {"true", "false", "t", "f", "yes", "no", "sim", "nao"}:
            return "boolean"
        # Binário 0/1 numérico é mais útil como boolean
        if norm <= {"0", "1"}:
            return "boolean"

    # Numeric types
    if dtype in ("integer", "int", "int32", "int64"):
        # integer com cardinalidade baixa pode ser codificação categórica,
        # mas para a fase de quality tratar como integer é suficiente.
        return "integer"
    if dtype in ("numeric", "double", "float", "float32", "float64"):
        return "numeric"

    # Character: categorical vs text vs id
    if dtype in ("character", "string", "factor"):
        if cardinality > 0.5 * max(total_rows, 1):
            # Muito única — ou id textual, ou texto livre
            if sample and _looks_like_text(sample):
                return "text"
            return "id"
        return "categorical"

    # Fallback conservador
    return "categorical"


def _looks_like_date(sample: list[str]) -> bool:
    """Heurística rápida: amostras ≥80% batem com padrões de data comum."""
    import re

    patterns = [
        r"^\d{4}-\d{2}-\d{2}",
        r"^\d{2}/\d{2}/\d{4}",
        r"^\d{2}-\d{2}-\d{4}",
        r"^\d{4}/\d{2}/\d{2}",
    ]
    hits = 0
    for s in sample:
        if any(re.match(p, s.strip()) for p in patterns):
            hits += 1
    return len(sample) > 0 and hits / len(sample) >= 0.8


def _looks_like_text(sample: list[str]) -> bool:
    """Amostras com ≥20 chars em média → texto livre."""
    if not sample:
        return False
    avg = sum(len(s) for s in sample) / len(sample)
    return avg >= 20


def classify_profile(dataset_profile: dict) -> dict:
    """
    Converte `dataset_profile` (do node_probe) em config de tratamento.

    Retorna: {source_file: str, columns: [{name, semantic_type, dtype, ...}, ...]}

    Se houver múltiplos datasets no profile, pega o primeiro (multi-tabela fica
    para fase posterior).
    """
    datasets = dataset_profile.get("datasets") or []
    if not datasets:
        raise ValueError("dataset_profile vazio — probe não produziu datasets")

    ds = datasets[0]
    if ds.get("read_error"):
        raise ValueError(f"probe falhou ao ler dataset: {ds.get('read_error')}")

    total_rows = int(ds.get("rows") or 0)
    out_cols = []
    for c in ds.get("columns") or []:
        sem = _classify_column(c, total_rows)
        out_cols.append({
            "name": c.get("name"),
            "semantic_type": sem,
            "dtype_r": c.get("dtype"),
            "missing_pct": c.get("missing_pct"),
            "cardinality": c.get("cardinality"),
        })

    return {
        "source_file": ds.get("file"),
        "total_rows": total_rows,
        "columns": out_cols,
    }


def run_quality_dispatcher(
    dataset_profile: dict,
    inputs: dict[str, bytes],
) -> dict:
    """
    Executa a pipeline determinística de quality no sandbox.

    Retorna dict compatível com o que `node_quality` já consome:
      {success, exit_code, code, summary, outputs_dir, stderr_tail, stdout_tail,
       attempts: [{...}], artifact_paths, _usage: {tokens_in: 0, ...}}
    """
    config = classify_profile(dataset_profile)

    # Injeta o config JSON como input adicional; o R lê via ./inputs/__column_types.json
    config_bytes = json.dumps(config, ensure_ascii=False).encode()
    inputs_ext = dict(inputs)
    inputs_ext["__column_types.json"] = config_bytes

    r_script = _TEMPLATE_PATH.read_text(encoding="utf-8")

    sandbox_result = run_code(
        code=r_script,
        language="r",
        inputs=inputs_ext,
        timeout=600,
        keep_workspace=True,
    )

    summary: dict = {}
    summary_parse_error: str | None = None
    summary_path = sandbox_result.outputs_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
        except json.JSONDecodeError as e:
            summary_parse_error = f"summary.json malformado: {e}"

    report_path = sandbox_result.outputs_dir / "report.html"
    has_report = report_path.exists() and report_path.stat().st_size > 500
    success = (
        sandbox_result.exit_code == 0
        and bool(summary)
        and summary_parse_error is None
    )

    return {
        "code": r_script,
        "language": "r",
        "stage": "quality",
        "dispatcher": "deterministic_v1",
        "classification": config,
        "exit_code": sandbox_result.exit_code,
        "timed_out": sandbox_result.timed_out,
        "stdout_tail": sandbox_result.stdout[-2000:],
        "stderr_tail": sandbox_result.stderr[-2000:],
        "summary": summary,
        "summary_parse_error": summary_parse_error,
        "has_report": has_report,
        "artifact_paths": [str(p) for p in sandbox_result.artifacts],
        "outputs_dir": str(sandbox_result.outputs_dir),
        "attempts": [{
            "attempt": 1,
            "exit_code": sandbox_result.exit_code,
            "has_summary": bool(summary),
            "has_report": has_report,
            "tokens_in": 0,
            "tokens_out": 0,
        }],
        "success": success,
        "_usage": {"tokens_in": 0, "tokens_out": 0, "model": "deterministic"},
    }
