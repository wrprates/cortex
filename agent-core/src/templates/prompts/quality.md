# Fase: Qualidade de Dados

Esta é a **primeira etapa** de qualquer projeto de ciência de dados e define se as análises seguintes são confiáveis.

## Objetivos

Avaliar rigorosamente, em pt-BR, quatro dimensões — e traduzir cada achado em **impacto de negócio**:

1. **Completude** — % de valores ausentes por coluna; padrões de ausência (MCAR, MAR, MNAR quando possível inferir); colunas inutilizáveis.
2. **Consistência** — tipos de dados coerentes (datas como datas, numéricos como numéricos); formatos uniformes (moeda, telefone, CNPJ); violação de relações esperadas entre colunas.
3. **Unicidade** — duplicatas exatas e parciais; chaves candidatas; violação de unicidade onde esperada.
4. **Validade** — valores fora de domínio (idades negativas, datas futuras impossíveis); outliers extremos segundo regras de negócio ou estatísticas robustas (IQR × 3, MAD).

## Análise descritiva + visualização por tipo de variável

Trate cada coluna conforme o tipo — uma análise de qualidade que não olha os dados de perto é análise fraca. **Use o código R pra inspecionar e gerar os gráficos abaixo como suporte à narrativa**:

- **Numéricas contínuas** (preço, idade, receita, tempo): resumo com `skimr::skim()` ou `summary()` (mín/Q1/mediana/média/Q3/máx + NA), **histograma** (`echarts4r::e_histogram`) pra distribuição e **boxplot** (`echarts4r::e_boxplot`) pra outliers. Comente skew, multimodalidade, cauda longa e outliers.
- **Numéricas discretas / contagens**: histograma com bins ajustados + tabela de frequência se cardinalidade baixa. Verifique zero-inflação e cauda.
- **Categóricas** (nominal ou ordinal): **gráfico de barras** (`echarts4r::e_bar`) com contagem e %; destaque categorias raras (<1%) e cardinalidade alta (>50 níveis é suspeito de ID escondida).
- **Booleanas / binárias**: barras simples com proporção; sempre mostre N absoluto junto do %.
- **Datas / datetime**: série temporal de contagem por mês/dia (`echarts4r::e_line`); verifique gaps, datas impossíveis (futuras num histórico passado, antes do início do negócio), sazonalidade de coleta.
- **Texto livre**: distribuição de comprimento de string, top N termos, % vazio vs não-vazio. Raramente é feature utilizável sem processamento — documente isso.
- **Identificadores (IDs)**: confirme unicidade, padrão (UUID, incremental, alfanumérico), colisões. Não plote distribuição de ID.

**Regras sobre gráficos:**
- Cada gráfico precisa de **título descritivo que já conta a história** (ver `report_narrative.md`).
- Não gere 50 histogramas iguais. **Selecione as 10-15 variáveis mais relevantes** (ou as mais problemáticas) e comente cada uma; o resto vai resumido numa tabela final.
- Se não couber gráfico (ex: coluna constante), diga isso explicitamente e não gere o chunk.

## Análise por tabela (quando há múltiplas)

Se o dataset tem **mais de uma tabela** (ex: `customers.csv` + `orders.csv`):

1. Uma subseção do report **por tabela**, com as 4 dimensões + descritivas por tipo.
2. Uma seção final de **integridade relacional**: chaves estrangeiras batem entre tabelas? Há órfãos (orders sem customer)? Há cardinalidade inesperada (1-para-N que vira N-para-N)?
3. Se só há uma tabela, pule essa seção — mas **não finja que há múltiplas**.

## O que NÃO é aceitável

- "15% de missing em `age`" → **não é insight**, é descrição. Converta em: *"15% dos registros estão sem idade — essa coluna não pode ser usada como feature sem imputação informada. Recomenda-se verificar no sistema de origem se existe regra que gera esse gap, pois concentra-se em registros de 2023 (padrão MAR)."*
- Tabelas soltas com contagens de NA sem interpretação.
- Gráficos sem título descritivo ou sem parágrafo ao redor explicando o achado.
- Pular a fase se o dataset "parecer limpo" — sempre documentar o que foi checado e o resultado.
- Rodar o mesmo gráfico pra 50 colunas de forma mecânica (spam de output).

## Entregáveis desta fase

1. **`./outputs/summary.json`** com chaves obrigatórias:
   ```json
   {
     "dataset": {"rows": int, "cols": int, "memory_mb": float, "tables": [str]},
     "completeness": {"<col>": {"missing_pct": float, "pattern": str}},
     "consistency": {"<col>": {"dtype_issue": str|null, "format_issue": str|null}},
     "uniqueness": {"duplicates_full": int, "candidate_keys": [str]},
     "validity": {"<col>": {"out_of_domain": int, "extreme_outliers": int}},
     "by_variable_type": {"numeric": int, "categorical": int, "boolean": int, "date": int, "text": int, "id": int},
     "blocking_issues": [str],
     "recommended_actions": [str]
   }
   ```
2. **`./outputs/report.html`** (Quarto) com:
   - Resumo executivo (3-6 parágrafos, ranking dos achados + ação)
   - Contexto do dataset (shape, tabelas, tipos)
   - Seção por dimensão de qualidade (4) com **narrativa 3-camadas** + gráfico quando agrega
   - Seção "Análise descritiva por tipo de variável" com os gráficos acima
   - Seção "Por tabela" (se >1)
   - Recomendações priorizadas
   - Ressalvas / limitações
3. **`./outputs/analysis.rds`** com os objetos R usados no relatório.

## Decisão final obrigatória

Ao fim da fase, responda explicitamente no report:

> **"Este dataset está apto para análise exploratória?"** — Sim / Sim, com ressalvas / Não.
>
> Justificar em 3-5 frases, citando o que precisa ser resolvido antes da próxima fase.
