FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Dependências de sistema (R, tidyverse deps, Quarto deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        r-base r-base-dev \
        libpq-dev \
        ca-certificates \
        curl \
        # Dependências para tidyverse/R packages
        libxml2-dev \
        libcurl4-openssl-dev \
        libssl-dev \
        libfontconfig1-dev \
        libfreetype6-dev \
        libpng-dev \
        libtiff5-dev \
        libjpeg-dev \
        libharfbuzz-dev \
        libfribidi-dev \
        libgit2-dev \
        libuv1-dev \
        libsass-dev \
        libnode-dev \
        # Dependências para Quarto/Pandoc
        pandoc \
    && rm -rf /var/lib/apt/lists/*

# Instalar Quarto
RUN curl -LO https://quarto.org/download/latest/quarto-linux-amd64.deb \
    && dpkg -i quarto-linux-amd64.deb \
    && rm quarto-linux-amd64.deb

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

# R packages (tidyverse stack + visualização + quarto)
# pkgs listados; após install, verifica cada um com require() e aborta se faltar
RUN R -e "pkgs <- c('tidyverse','data.table','ggplot2','echarts4r','plotly','DT','jsonlite','rmarkdown','quarto','tidymodels','ranger','xgboost','lubridate','htmlwidgets','broom','skimr'); \
install.packages(pkgs, repos='https://cloud.r-project.org', Ncpus=4); \
missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly=TRUE)]; \
if (length(missing)) stop('R packages missing after install: ', paste(missing, collapse=', ')); \
cat('all R packages OK:', paste(pkgs, collapse=', '), '\n')"

# Usuário não-root para rodar código gerado
RUN useradd -m -u 1000 sandbox
USER sandbox
WORKDIR /workspace

COPY --chown=sandbox:sandbox entrypoint.sh /usr/local/bin/entrypoint.sh
USER root
RUN chmod +x /usr/local/bin/entrypoint.sh
USER sandbox

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
