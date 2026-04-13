#!/usr/bin/env bash
# Executa um script Python ou R montado em /workspace.
# Convenção: o agent-core monta um volume em /workspace contendo:
#   /workspace/script.py  (ou script.R)
#   /workspace/inputs/     (dados de entrada, read-only por convenção)
#   /workspace/outputs/    (resultados — criados pelo script)
#   /workspace/meta.json   (metadados opcionais)
#
# O agent-core lê os arquivos de /workspace/outputs após execução.

set -euo pipefail

mkdir -p /workspace/outputs

cd /workspace

if [[ -f "script.py" ]]; then
    exec python script.py
elif [[ -f "script.R" ]]; then
    exec Rscript script.R
elif [[ -f "notebook.ipynb" ]]; then
    exec papermill notebook.ipynb outputs/executed.ipynb
else
    echo "ERROR: nenhum script.py, script.R ou notebook.ipynb encontrado em /workspace" >&2
    exit 2
fi
