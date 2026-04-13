FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        r-base r-base-dev \
        libpq-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python DS stack
RUN pip install --upgrade pip && pip install \
        pandas==2.2.3 \
        numpy==2.1.2 \
        scipy==1.14.1 \
        scikit-learn==1.5.2 \
        matplotlib==3.9.2 \
        seaborn==0.13.2 \
        plotly==5.24.1 \
        statsmodels==0.14.4 \
        xgboost==2.1.1 \
        lightgbm==4.5.0 \
        category_encoders==2.6.4 \
        papermill==2.6.0 \
        jupyter==1.1.1 \
        pyarrow==17.0.0 \
        openpyxl==3.1.5

# R packages (subset minimal; o resto instalável on-demand)
RUN R -e "install.packages(c('tidyverse','data.table','ggplot2','caret'), repos='https://cloud.r-project.org')" || true

# Usuário não-root para rodar código gerado
RUN useradd -m -u 1000 sandbox
USER sandbox
WORKDIR /workspace

COPY --chown=sandbox:sandbox entrypoint.sh /usr/local/bin/entrypoint.sh
USER root
RUN chmod +x /usr/local/bin/entrypoint.sh
USER sandbox

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
