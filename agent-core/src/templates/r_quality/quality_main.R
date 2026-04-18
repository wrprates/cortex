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

# -------------------------------------------------------------
# 5.1 Detecta target e calcula estatísticas de target
# -------------------------------------------------------------
# Heurística: procura coluna com nome canônico. Se não achar, procura boolean
# com cardinalidade 2 perto do fim do df (convenção comum em datasets públicos).
target_name_patterns <- c("^churn$", "^target$", "^label$", "^y$", "^class$", "^outcome$")
target_col <- NULL
for (pat in target_name_patterns) {
  hits <- names(df)[grepl(pat, names(df), ignore.case = TRUE)]
  if (length(hits) > 0) { target_col <- hits[1]; break }
}
# Fallback: último boolean/binary-cardinality 2
if (is.null(target_col)) {
  last_cols <- tail(names(df), 3)
  for (nm in rev(last_cols)) {
    if (nm %in% names(treatments)) {
      tt <- treatments[[nm]]$semantic_type
      if (!is.null(tt) && tt %in% c("boolean", "categorical")) {
        # Se categorical com cardinalidade 2, também vira target candidato
        vals <- unique(df[[nm]])
        if (length(vals[!is.na(vals)]) == 2) { target_col <- nm; break }
      }
    }
  }
}

target_info <- NULL
if (!is.null(target_col)) {
  y <- df[[target_col]]
  y_chr <- as.character(y)
  dist <- sort(table(y_chr, useNA = "no"), decreasing = TRUE)

  # Positive class: convenção "Yes"/"True"/"1"; fallback pro menos frequente
  positives <- c("Yes", "yes", "YES", "True", "TRUE", "1", "Sim", "sim")
  pos_class <- NULL
  for (p in positives) {
    if (p %in% names(dist)) { pos_class <- p; break }
  }
  if (is.null(pos_class) && length(dist) >= 2) {
    pos_class <- names(dist)[length(dist)]  # classe minoritária
  }

  # rate_by_categorical: pra cada categorical (top 10), P(target == pos | level)
  rates_by <- list()
  if (!is.null(pos_class)) {
    y_bin <- as.integer(y_chr == pos_class)
    cat_names <- names(treatments)[sapply(names(treatments), function(n)
      !is.null(treatments[[n]]$semantic_type) &&
        treatments[[n]]$semantic_type == "categorical" &&
        n != target_col)]
    for (nm in head(cat_names, 10)) {
      agg <- tapply(y_bin, as.character(df[[nm]]), mean, na.rm = TRUE)
      if (length(agg) > 0) rates_by[[nm]] <- as.list(round(as.numeric(agg) * 100, 2))
      if (length(rates_by[[nm]]) > 0) names(rates_by[[nm]]) <- names(agg)
    }
  }

  target_info <- list(
    column = target_col,
    type = treatments[[target_col]]$semantic_type %||% "unknown",
    positive_class = pos_class,
    distribution = as.list(dist),
    rate_by_categorical = rates_by
  )
  message(sprintf("[quality] target detectado: %s (pos=%s)",
                  target_col, pos_class %||% "?"))
}

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
  columns = treatments,
  target = target_info
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
# 7. Report Quarto — usa template fixo injetado pelo dispatcher.
#     O template lê summary.json + analysis.rds (ambos em ./outputs/) e
#     renderiza um HTML rico com tabelas gt, boxplots e echarts4r. Zero
#     geração de QMD em runtime — garante consistência entre runs.
# -------------------------------------------------------------
template_src <- "./inputs/__report_template.qmd"
if (!file.exists(template_src)) {
  stop(sprintf(
    "[quality] template QMD não encontrado em %s. O dispatcher Python deve ",
    "injetar `__report_template.qmd` em inputs — veja quality_dispatcher.py.",
    template_src
  ))
}
file.copy(template_src, "./outputs/report.qmd", overwrite = TRUE)

quarto::quarto_render("./outputs/report.qmd", output_format = "html", quiet = TRUE)

message("[quality] DONE")
