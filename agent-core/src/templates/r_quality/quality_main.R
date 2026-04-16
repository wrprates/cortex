# Cortex Quality Pipeline — script determinístico
# Não editar manualmente. O dispatcher Python compõe column_types via
# ./inputs/__column_types.json e invoca este script no sandbox.
#
# Saídas:
#   ./outputs/analysis.rds    — lista com df_clean e resultados por coluna
#   ./outputs/summary.json    — sumário estruturado por coluna + geral
#   ./outputs/dataset_clean.parquet — dataset tratado (se pacote arrow disponível)
#                                    ou dataset_clean.csv como fallback
#   ./outputs/report.qmd + report.html — relatório Quarto

suppressPackageStartupMessages({
  library(tidyverse)
  library(jsonlite)
  library(quarto)
})

dir.create("./outputs", showWarnings = FALSE, recursive = TRUE)

# -------------------------------------------------------------
# 1. Carrega configuração de tipos semânticos (gerada pelo Python)
# -------------------------------------------------------------
config <- fromJSON("./inputs/__column_types.json", simplifyVector = FALSE)
source_file <- config$source_file
col_types <- config$columns

message(sprintf("[quality] carregando %s (%d colunas classificadas)",
                source_file, length(col_types)))

# -------------------------------------------------------------
# 2. Carrega dados
# -------------------------------------------------------------
src_path <- file.path("./inputs", source_file)
ext <- tools::file_ext(source_file) |> tolower()
df <- switch(ext,
  csv = readr::read_csv(src_path, show_col_types = FALSE),
  tsv = readr::read_tsv(src_path, show_col_types = FALSE),
  stop(sprintf("extensão não suportada: %s", ext))
)

n_rows_orig <- nrow(df)
n_cols_orig <- ncol(df)

# -------------------------------------------------------------
# 3. Funções de tratamento por tipo semântico
#    Contrato: cada função recebe (df, col_name) e devolve list com:
#      - action: "kept"|"dropped"|"imputed"|"transformed"
#      - findings: character vector (observações)
#      - df_after: data.frame após tratamento (mesmo número de linhas)
# -------------------------------------------------------------

# Converte vazios disfarçados em NA e reporta
.null_empty <- function(x) {
  if (is.character(x)) {
    empty <- str_trim(x) == ""
    x[empty] <- NA_character_
  }
  x
}

treat_numeric <- function(df, col) {
  x <- df[[col]]
  x <- suppressWarnings(as.numeric(x))
  n_na <- sum(is.na(x))
  pct_na <- round(n_na / length(x) * 100, 2)

  findings <- c()
  if (pct_na > 50) findings <- c(findings, sprintf(
    "alta proporção de NA (%.1f%%) — avaliar se coluna é útil", pct_na))

  # Estatísticas robustas
  med <- median(x, na.rm = TRUE)
  q1 <- quantile(x, 0.25, na.rm = TRUE, names = FALSE)
  q3 <- quantile(x, 0.75, na.rm = TRUE, names = FALSE)
  iqr <- q3 - q1
  lo <- q1 - 1.5 * iqr
  hi <- q3 + 1.5 * iqr
  n_outliers <- sum(x < lo | x > hi, na.rm = TRUE)
  pct_outliers <- round(n_outliers / length(x) * 100, 2)
  if (pct_outliers > 5) findings <- c(findings, sprintf(
    "outliers IQR: %.1f%% fora de [%.2f, %.2f]", pct_outliers, lo, hi))

  # Imputação por mediana
  x[is.na(x)] <- med
  df[[col]] <- x

  list(
    semantic_type = "numeric",
    action = if (n_na > 0) "imputed_median" else "kept",
    n_missing = n_na,
    pct_missing = pct_na,
    median = med, q1 = q1, q3 = q3,
    n_outliers_iqr = n_outliers,
    pct_outliers_iqr = pct_outliers,
    findings = findings,
    df_after = df
  )
}

treat_integer <- function(df, col) {
  # Trata como numérico mas reporta como integer
  r <- treat_numeric(df, col)
  r$semantic_type <- "integer"
  r$df_after[[col]] <- as.integer(round(r$df_after[[col]]))
  r
}

treat_boolean <- function(df, col) {
  x <- df[[col]]
  x_char <- as.character(x) |> str_trim() |> str_to_lower()
  true_vals <- c("true", "1", "t", "yes", "y", "sim", "s")
  false_vals <- c("false", "0", "f", "no", "n", "nao", "não", "n")
  out <- rep(NA, length(x))
  out[x_char %in% true_vals] <- TRUE
  out[x_char %in% false_vals] <- FALSE
  n_na <- sum(is.na(out))
  pct_na <- round(n_na / length(x) * 100, 2)
  prop_true <- mean(out, na.rm = TRUE)

  findings <- c()
  if (pct_na > 5) findings <- c(findings, sprintf(
    "valores não interpretáveis como boolean: %.1f%%", pct_na))
  if (!is.nan(prop_true) && (prop_true < 0.01 || prop_true > 0.99)) {
    findings <- c(findings, sprintf(
      "quase-constante (prop TRUE = %.3f) — pouco sinal", prop_true))
  }

  # Imputação pela moda
  mode_val <- if (sum(out, na.rm = TRUE) >= sum(!out, na.rm = TRUE)) TRUE else FALSE
  out[is.na(out)] <- mode_val
  df[[col]] <- out

  list(
    semantic_type = "boolean",
    action = if (n_na > 0) "imputed_mode" else "kept",
    n_missing = n_na,
    pct_missing = pct_na,
    prop_true = prop_true,
    findings = findings,
    df_after = df
  )
}

treat_categorical <- function(df, col) {
  x <- .null_empty(as.character(df[[col]]))
  n_na <- sum(is.na(x))
  pct_na <- round(n_na / length(x) * 100, 2)
  freq <- sort(table(x, useNA = "no"), decreasing = TRUE)
  card <- length(freq)
  rare <- names(freq[freq / sum(freq) < 0.01])

  findings <- c()
  if (pct_na > 0) findings <- c(findings, sprintf(
    "NA/vazio disfarçado: %.1f%%", pct_na))
  if (card > 100) findings <- c(findings, sprintf(
    "cardinalidade alta (%d níveis) — possível id ou texto livre", card))
  if (length(rare) > 0) findings <- c(findings, sprintf(
    "%d níveis raros (<1%%) — candidatos a 'Outros'", length(rare)))

  # Imputa com a moda
  if (n_na > 0 && length(freq) > 0) {
    x[is.na(x)] <- names(freq)[1]
  }
  df[[col]] <- x

  list(
    semantic_type = "categorical",
    action = if (n_na > 0) "imputed_mode" else "kept",
    n_missing = n_na,
    pct_missing = pct_na,
    cardinality = card,
    top_levels = as.list(head(freq, 10)),
    n_rare_levels = length(rare),
    findings = findings,
    df_after = df
  )
}

treat_date <- function(df, col) {
  x <- df[[col]]
  parsed <- suppressWarnings(
    as.Date(x, tryFormats = c("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"))
  )
  n_na <- sum(is.na(parsed))
  pct_na <- round(n_na / length(parsed) * 100, 2)

  findings <- c()
  if (pct_na > 10) findings <- c(findings, sprintf(
    "%.1f%% falharam o parsing de data", pct_na))

  if (all(is.na(parsed))) {
    range_txt <- "n/a"
  } else {
    range_txt <- sprintf("[%s, %s]",
      min(parsed, na.rm = TRUE), max(parsed, na.rm = TRUE))
  }

  df[[col]] <- parsed
  list(
    semantic_type = "date",
    action = "parsed",
    n_missing = n_na,
    pct_missing = pct_na,
    range = range_txt,
    findings = findings,
    df_after = df
  )
}

treat_id <- function(df, col) {
  x <- df[[col]]
  n_dup <- sum(duplicated(x, incomparables = NA))
  n_na <- sum(is.na(x))
  findings <- c()
  if (n_dup > 0) findings <- c(findings, sprintf(
    "%d duplicatas em coluna de id", n_dup))
  if (n_na > 0) findings <- c(findings, sprintf(
    "%d NAs em coluna de id — linhas serão removidas", n_na))

  # Remove linhas com id NA
  df <- df[!is.na(x), , drop = FALSE]

  list(
    semantic_type = "id",
    action = if (n_na > 0 || n_dup > 0) "rows_removed" else "kept",
    n_missing = n_na,
    n_duplicates = n_dup,
    findings = findings,
    df_after = df
  )
}

treat_text <- function(df, col) {
  x <- as.character(df[[col]])
  lens <- nchar(x)
  findings <- c(sprintf("comprimento médio: %.1f | mediano: %.0f",
                        mean(lens, na.rm = TRUE), median(lens, na.rm = TRUE)))
  list(
    semantic_type = "text",
    action = "kept",
    mean_length = mean(lens, na.rm = TRUE),
    median_length = median(lens, na.rm = TRUE),
    findings = findings,
    df_after = df
  )
}

treat_constant <- function(df, col) {
  findings <- c("coluna constante — removida")
  df[[col]] <- NULL
  list(
    semantic_type = "constant",
    action = "dropped",
    findings = findings,
    df_after = df
  )
}

treat_all_missing <- function(df, col) {
  findings <- c("coluna 100% missing — removida")
  df[[col]] <- NULL
  list(
    semantic_type = "all_missing",
    action = "dropped",
    findings = findings,
    df_after = df
  )
}

# -------------------------------------------------------------
# 4. Loop de tratamento
# -------------------------------------------------------------
treatments <- list()
for (ct in col_types) {
  col <- ct$name
  st <- ct$semantic_type
  fn_name <- paste0("treat_", st)
  if (!exists(fn_name)) {
    message(sprintf("[quality] tipo semântico desconhecido: %s (coluna %s) — pulando",
                    st, col))
    next
  }
  if (!(col %in% names(df))) {
    message(sprintf("[quality] coluna %s não encontrada no df — pulando", col))
    next
  }
  message(sprintf("[quality] tratando %s como %s", col, st))
  fn <- get(fn_name)
  r <- fn(df, col)
  df <- r$df_after
  r$df_after <- NULL  # não salva df a cada coluna pra economizar memória
  treatments[[col]] <- r
}

# -------------------------------------------------------------
# 5. Resumo geral
# -------------------------------------------------------------
n_rows_final <- nrow(df)
n_cols_final <- ncol(df)

summary_json <- list(
  dataset = list(
    file = source_file,
    rows_original = n_rows_orig,
    cols_original = n_cols_orig,
    rows_final = n_rows_final,
    cols_final = n_cols_final,
    rows_removed = n_rows_orig - n_rows_final,
    cols_dropped = n_cols_orig - n_cols_final
  ),
  columns = treatments
)

write_json(summary_json, "./outputs/summary.json",
           auto_unbox = TRUE, pretty = TRUE, na = "null")

# -------------------------------------------------------------
# 6. Persistência: analysis.rds com df_clean, parquet/csv com dataset tratado
# -------------------------------------------------------------
saveRDS(list(df_clean = df, treatments = treatments, summary = summary_json),
        "./outputs/analysis.rds")

if (requireNamespace("arrow", quietly = TRUE)) {
  arrow::write_parquet(df, "./outputs/dataset_clean.parquet")
  message("[quality] dataset_clean.parquet salvo")
} else {
  readr::write_csv(df, "./outputs/dataset_clean.csv")
  message("[quality] arrow ausente — salvo como dataset_clean.csv")
}

# -------------------------------------------------------------
# 7. Report Quarto
# -------------------------------------------------------------
qmd_lines <- c(
  "---",
  "title: 'Diagnóstico de Qualidade dos Dados'",
  "format:",
  "  html:",
  "    self-contained: true",
  "    toc: true",
  "---",
  "",
  "```{r setup, include=FALSE}",
  "knitr::opts_chunk$set(echo = FALSE, warning = FALSE, message = FALSE)",
  "library(tidyverse)",
  "library(jsonlite)",
  "s <- fromJSON('summary.json', simplifyVector = FALSE)",
  "```",
  "",
  "## Dataset",
  "",
  "```{r}",
  "ds <- s$dataset",
  "cat(sprintf(",
  "  '**Arquivo:** `%s` · **Linhas:** %s (removidas %s) · **Colunas:** %s (removidas %s)\\n\\n',",
  "  ds$file, ds$rows_final, ds$rows_removed, ds$cols_final, ds$cols_dropped",
  "))",
  "```",
  "",
  "## Tratamento por coluna",
  "",
  "```{r}",
  "for (col in names(s$columns)) {",
  "  r <- s$columns[[col]]",
  "  cat(sprintf('### `%s` — tipo: %s · ação: %s\\n\\n', col, r$semantic_type, r$action))",
  "  if (length(r$findings) > 0) {",
  "    cat(paste0('- ', unlist(r$findings), collapse = '\\n'), '\\n\\n')",
  "  } else {",
  "    cat('- sem observações críticas\\n\\n')",
  "  }",
  "}",
  "```"
)

writeLines(qmd_lines, "./outputs/report.qmd")

quarto::quarto_render("./outputs/report.qmd", output_format = "html", quiet = TRUE)

message("[quality] DONE")
