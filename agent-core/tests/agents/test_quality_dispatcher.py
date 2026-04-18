"""
Testes do quality_dispatcher: garante que o template QMD fixo é injetado
no sandbox junto com o script R, e que `classify_profile` produz a config
esperada pra inputs conhecidos.

Não rodamos R de verdade — isso é integração. Aqui só os pontos puros.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agents import quality_dispatcher


# --------- paths dos templates ---------


class TestTemplatePaths:
    def test_r_script_exists_and_non_empty(self):
        path = quality_dispatcher._TEMPLATE_PATH
        assert path.exists(), f"script R esperado em {path}"
        text = path.read_text()
        # Sanity: tem o shebang esperado (comentário cabeçalho) e a chamada
        # final de render.
        assert "Cortex Quality Pipeline" in text
        assert "quarto::quarto_render" in text

    def test_qmd_template_exists_and_has_expected_sections(self):
        path = quality_dispatcher._QMD_TEMPLATE_PATH
        assert path.exists(), f"template QMD esperado em {path}"
        text = path.read_text()
        # Cabeçalho YAML
        assert text.startswith("---")
        # Seções obrigatórias do novo layout (o relatório velho não tinha
        # nenhuma delas).
        for section in [
            "Executive Overview",
            "Valores Ausentes",
            "Colunas Numéricas",
            "Colunas Categóricas",
            "Target Analysis",
            "Alertas de Qualidade",
            "Detalhe por Coluna",
        ]:
            assert section in text, f"seção '{section}' não encontrada no template"
        # Libraries esperadas no setup chunk
        for lib in ["tidyverse", "jsonlite", "gt", "echarts4r"]:
            assert f"library({lib})" in text, f"library({lib}) ausente do setup"


# --------- classify_profile ---------


class TestClassifyProfile:
    def test_classifies_typical_columns(self):
        profile = {
            "datasets": [{
                "file": "t.csv",
                "rows": 1000,
                "columns": [
                    {"name": "customer_id", "dtype": "character",
                     "cardinality": 1000, "missing_pct": 0, "sample": ["a", "b"]},
                    {"name": "age", "dtype": "integer",
                     "cardinality": 60, "missing_pct": 2.1, "sample": ["25", "47"]},
                    {"name": "gender", "dtype": "character",
                     "cardinality": 2, "missing_pct": 0, "sample": ["M", "F"]},
                    {"name": "Churn", "dtype": "character",
                     "cardinality": 2, "missing_pct": 0, "sample": ["Yes", "No"]},
                ],
            }]
        }
        config = quality_dispatcher.classify_profile(profile)
        assert config["source_file"] == "t.csv"
        assert config["total_rows"] == 1000
        by_name = {c["name"]: c["semantic_type"] for c in config["columns"]}
        assert by_name["customer_id"] == "id"  # nome + cardinalidade proporcional
        assert by_name["age"] == "integer"
        # Yes/No => boolean; outras cardinality=2 não-binary => categorical/boolean
        # O classify atual promove Yes/No a boolean porque "yes","no" batem no set.
        assert by_name["Churn"] == "boolean"

    def test_raises_on_empty_profile(self):
        with pytest.raises(ValueError):
            quality_dispatcher.classify_profile({"datasets": []})

    def test_raises_when_probe_failed(self):
        with pytest.raises(ValueError):
            quality_dispatcher.classify_profile({
                "datasets": [{"read_error": "arquivo corrompido"}]
            })


# --------- run_quality_dispatcher: inputs injetados ---------


class TestRunQualityDispatcher:
    """
    Testa que `run_code` é chamado com `inputs` contendo tanto
    `__column_types.json` (já existia) quanto `__report_template.qmd`
    (novo, crítico pro render não cair em erro no sandbox).
    """

    def _fake_run_code_capture(self):
        """Retorna um (fake, calls) — fake é a função mockada, calls registra args."""
        calls = {}

        def fake(*, code, language, inputs, timeout, keep_workspace):
            calls["code"] = code
            calls["language"] = language
            calls["inputs"] = inputs
            # Simula um run onde nada foi escrito no outputs_dir (success=False,
            # mas o dispatcher ainda popula os demais campos corretamente).
            tmp_dir = Path("/tmp/nonexistent_outputs_dir_for_test")
            return SimpleNamespace(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                outputs_dir=tmp_dir,
                artifacts=[],
            )

        return fake, calls

    def test_injects_report_template_and_column_types(self, monkeypatch):
        profile = {
            "datasets": [{
                "file": "x.csv",
                "rows": 10,
                "columns": [
                    {"name": "a", "dtype": "integer",
                     "cardinality": 10, "missing_pct": 0, "sample": ["1", "2"]},
                ],
            }]
        }
        fake, calls = self._fake_run_code_capture()
        monkeypatch.setattr(quality_dispatcher, "run_code", fake)

        result = quality_dispatcher.run_quality_dispatcher(
            dataset_profile=profile,
            inputs={"x.csv": b"a\n1\n2\n"},
        )

        # Contrato com o R: ambos inputs injetados existem e não são vazios
        injected = calls["inputs"]
        assert "__column_types.json" in injected
        assert "__report_template.qmd" in injected
        assert len(injected["__report_template.qmd"]) > 1000, (
            "template QMD injetado parece suspeito pequeno"
        )
        # Input original do usuário preservado
        assert injected["x.csv"] == b"a\n1\n2\n"

        # Contrato com node_quality: dispatcher retornou os campos esperados
        assert result["dispatcher"] == "deterministic_v1"
        assert result["stage"] == "quality"
        assert result["_usage"]["tokens_in"] == 0  # zero LLM
