FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

RUN apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY infra ./infra
COPY scripts ./scripts
RUN chmod +x ./scripts/start.sh

RUN useradd --create-home --uid 10001 liquiguard \
    && chown -R liquiguard:liquiguard /app
USER liquiguard

EXPOSE 8000

CMD ["./scripts/start.sh"]
