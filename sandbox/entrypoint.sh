#!/usr/bin/env bash
# Executa script Python/R/notebook no working_dir atual.
# O agent-core monta a named volume compartilhada em /sandbox_root e
# seta working_dir=/sandbox_root/<run_id>. Cada execução tem seu próprio subdir.
#
# Convenção do subdir:
#   ./script.py | script.R | notebook.ipynb
#   ./inputs/     (dados de entrada)
#   ./outputs/    (resultados — criados pelo script)
#   ./meta.json   (metadados opcionais)

set -euo pipefail

mkdir -p ./outputs

if [[ -f "script.py" ]]; then
    exec python script.py
elif [[ -f "script.R" ]]; then
    exec Rscript script.R
elif [[ -f "notebook.ipynb" ]]; then
    exec papermill notebook.ipynb outputs/executed.ipynb
elif [[ -f "report.qmd" ]]; then
    exec quarto render report.qmd --output-dir outputs
elif [[ -f "report.Rmd" ]]; then
    exec Rscript -e "rmarkdown::render('report.Rmd', output_dir='outputs')"
else
    echo "ERROR: nenhum script.py, script.R, notebook.ipynb, report.qmd ou report.Rmd encontrado em $PWD" >&2
    exit 2
fi
