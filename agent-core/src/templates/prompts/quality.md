# Fase: Qualidade de Dados

Esta é a **primeira etapa** de qualquer projeto de ciência de dados e define se as análises seguintes são confiáveis.

## Objetivos

Avaliar rigorosamente, em pt-BR, quatro dimensões — e traduzir cada achado em **impacto de negócio**:

1. **Completude** — % de valores ausentes por coluna; padrões de ausência (MCAR, MAR, MNAR quando possível inferir); colunas inutilizáveis.
2. **Consistência** — tipos de dados coerentes (datas como datas, numéricos como numéricos); formatos uniformes (moeda, telefone, CNPJ); violação de relações esperadas entre colunas.
3. **Unicidade** — duplicatas exatas e parciais; chaves candidatas; violação de unicidade onde esperada.
4. **Validade** — valores fora de domínio (idades negativas, datas futuras impossíveis); outliers extremos segundo regras de negócio ou estatísticas robustas (IQR × 3, MAD).

## O que NÃO é aceitável

- "15% de missing em `age`" → **não é insight**, é descrição. Converta em: *"15% dos registros estão sem idade — essa coluna não pode ser usada como feature sem imputação informada. Recomenda-se verificar no sistema de origem se existe regra que gera esse gap, pois concentra-se em registros de 2023 (padrão MAR)."*
- Tabelas soltas com contagens de NA sem interpretação.
- Pular a fase se o dataset "parecer limpo" — sempre documentar o que foi checado e o resultado.

## Entregáveis desta fase

1. **`./outputs/summary.json`** com chaves obrigatórias:
   ```json
   {
     "dataset": {"rows": int, "cols": int, "memory_mb": float},
     "completeness": {"<col>": {"missing_pct": float, "pattern": str}},
     "consistency": {"<col>": {"dtype_issue": str|null, "format_issue": str|null}},
     "uniqueness": {"duplicates_full": int, "candidate_keys": [str]},
     "validity": {"<col>": {"out_of_domain": int, "extreme_outliers": int}},
     "blocking_issues": [str],
     "recommended_actions": [str]
   }
   ```
2. **`./outputs/report.html`** com as 4 seções, cada uma com **pelo menos 2 parágrafos narrativos** seguindo a regra de 3 camadas (ver `report_narrative.md`). Gráficos `echarts4r` como suporte — nunca sozinhos.
3. **`./outputs/analysis.rds`** com os objetos R usados no relatório.

## Decisão final obrigatória

Ao fim da fase, o agente precisa responder explicitamente no report:

> **"Este dataset está apto para análise exploratória?"** — Sim / Sim, com ressalvas / Não.
>
> Justificar em 3-5 frases, citando o que precisa ser resolvido antes da próxima fase.
