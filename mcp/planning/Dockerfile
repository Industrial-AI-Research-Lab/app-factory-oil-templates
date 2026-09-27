# NOTE: python 3.10 is pinned because sampo==0.1.2.0.6 declares
# Requires-Python <3.11,>=3.10 and PyPI has no sampo release for 3.11
# (3.10 line) or 3.12+ (2.5.x line, different major). Do not bump to 3.11+
# without also replacing sampo. mcp[cli]==1.16.0 supports 3.10.
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app/sources/stairs-resource-model:/app/sources/stairs-storage/src \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sources ./sources

RUN useradd --uid 10001 --create-home appuser \
    && chown -R 10001:10001 /app
USER 10001

EXPOSE 8080

CMD ["python", "-m", "planning_mcp", "--transport", "streamable-http"]
