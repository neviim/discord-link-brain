# syntax=docker/dockerfile:1
#
# Duas metas (targets):
#   dev   — código vem por bind mount, ferramentas de teste, roda sob demanda
#   prod  — código assado na imagem, agendador roda sozinho e reinicia sozinho
#
# O UID/GID são argumentos de build porque o container escreve num vault que
# vive no host: rodar como root deixaria arquivos root no seu Obsidian.

FROM python:3.12-slim AS base

ARG UID=1000
ARG GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/dlb \
    CLAUDE_CONFIG_DIR=/home/dlb/.claude

# ca-certificates: o enriquecimento abre HTTPS. tzdata: TIMEZONE precisa existir
# dentro do container, senão a virada do dia sai errada no nome dos arquivos.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    if ! getent group "${GID}" >/dev/null; then groupadd -g "${GID}" dlb; fi; \
    if ! getent passwd "${UID}" >/dev/null; then \
        useradd -u "${UID}" -g "${GID}" -M -d /home/dlb -s /bin/bash dlb; \
    fi; \
    mkdir -p /home/dlb/.claude /app/.state; \
    chown -R "${UID}:${GID}" /home/dlb /app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY docker/entrypoint.sh docker/scheduler.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/scheduler.sh

ENTRYPOINT ["entrypoint.sh"]


# ------------------------------------------------------------------- dev ---
# O compose sobrepõe /app com o diretório do host, então o COPY aqui só serve
# para a imagem funcionar solta (docker run sem compose).
FROM base AS dev

ENV DLB_MODE=dev

COPY --chown=${UID}:${GID} . /app
USER ${UID}:${GID}
CMD ["doctor"]


# ------------------------------------------------------------------ prod ---
FROM base AS prod

ENV DLB_MODE=prod

COPY --chown=${UID}:${GID} dlb/ /app/dlb/
COPY --chown=${UID}:${GID} requirements.txt README.md /app/
USER ${UID}:${GID}

# Sem argumento a imagem de produção sobe o agendador, não uma sync avulsa.
CMD ["scheduler"]

HEALTHCHECK --interval=5m --timeout=10s --start-period=1m --retries=2 \
    CMD ["entrypoint.sh", "healthcheck"]
