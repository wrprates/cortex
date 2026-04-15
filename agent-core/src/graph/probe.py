"""
Probe de dataset: roda um script R mínimo no sandbox antes de planejar.

Produz um `profile.json` com shape, tipos, missing%, cardinalidade e uma
amostra de cada coluna. O orquestrador usa isso para julgar viabilidade
ANTES de pedir um plano — evita plano cego baseado só no briefing.
"""
from __future__ import annotations

import json
import logging

from ..sandbox.runner import run_code

logger = logging.getLogger(__name__)

PROBE_R_SCRIPT = r"""
suppressPackageStartupMessages({
  library(jsonlite)
  library(tools)
})

files <- list.files("./inputs", full.names = TRUE, recursive = FALSE)
if (length(files) == 0) {
  writeLines(toJSON(list(datasets = list(), error = "no_inputs"), auto_unbox = TRUE),
             "./outputs/profile.json")
  quit(save = "no", status = 0)
}

profile_one <- function(path) {
  ext <- tolower(file_ext(path))
  name <- basename(path)
  size_mb <- round(file.info(path)$size / 1024 / 1024, 2)

  df <- NULL
  read_error <- NULL

  if (ext %in% c("csv", "tsv", "txt")) {
    sep <- if (ext == "tsv") "\t" else ","
    df <- tryCatch(
      read.csv(path, sep = sep, nrows = 50000, stringsAsFactors = FALSE),
      error = function(e) { read_error <<- conditionMessage(e); NULL }
    )
  } else if (ext == "parquet") {
    read_error <- "parquet_not_supported_in_probe"
  } else if (ext %in% c("json", "jsonl")) {
    df <- tryCatch(fromJSON(path), error = function(e) { read_error <<- conditionMessage(e); NULL })
  } else {
    read_error <- paste0("unsupported_ext:", ext)
  }

  if (is.null(df) || !is.data.frame(df)) {
    return(list(
      file = name, size_mb = size_mb, read_error = read_error,
      rows = NA, cols = NA, columns = list()
    ))
  }

  cols <- lapply(names(df), function(cn) {
    x <- df[[cn]]
    missing_pct <- round(mean(is.na(x) | (is.character(x) & x == "")) * 100, 2)
    card <- length(unique(x))
    dtype <- class(x)[1]
    sample <- head(x[!is.na(x)], 5)
    list(
      name = cn,
      dtype = dtype,
      missing_pct = missing_pct,
      cardinality = card,
      sample = as.character(sample)
    )
  })

  list(
    file = name, size_mb = size_mb, read_error = NA,
    rows = nrow(df), cols = ncol(df),
    columns = cols
  )
}

datasets <- lapply(files, profile_one)

writeLines(
  toJSON(list(datasets = datasets), auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
  "./outputs/profile.json"
)
"""


def run_probe(inputs: dict[str, bytes]) -> dict:
    """
    Executa o script de probe no sandbox com os inputs fornecidos.

    Retorna o dict decodificado de `profile.json` com chave `datasets`.
    Se o probe falhar (exit != 0 ou arquivo ausente), inclui `_probe_error`.
    """
    # keep_workspace=True porque precisamos ler profile.json depois do retorno
    # de run_code (caso contrário o finally do runner limpa o dir antes).
    import shutil

    result = run_code(
        code=PROBE_R_SCRIPT,
        language="r",
        inputs=inputs,
        timeout=120,
        keep_workspace=True,
    )

    try:
        profile_path = result.outputs_dir / "profile.json"
        if result.exit_code != 0 or not profile_path.exists():
            logger.error(
                "probe falhou: exit=%s stderr=%s",
                result.exit_code,
                result.stderr[-800:],
            )
            return {
                "datasets": [],
                "_probe_error": {
                    "exit_code": result.exit_code,
                    "stderr_tail": result.stderr[-800:],
                    "timed_out": result.timed_out,
                },
            }

        return json.loads(profile_path.read_text())
    finally:
        shutil.rmtree(result.outputs_dir.parent, ignore_errors=True)
