FROM python:3.12-slim-bookworm AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/taskflow
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM dependencies AS test
RUN apt-get update && apt-get install -y --no-install-recommends postgresql ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN pip-audit -r requirements.txt --disable-pip --no-deps
RUN ruff check app tests alembic tools && ruff format --check app tests alembic tools && mypy app
RUN install -d -o postgres /tmp/taskflow-pg && \
    runuser -u postgres -- /usr/lib/postgresql/15/bin/initdb -D /tmp/taskflow-pg -A trust --encoding=UTF8 && \
    runuser -u postgres -- /usr/lib/postgresql/15/bin/pg_ctl -D /tmp/taskflow-pg -l /tmp/taskflow-pg.log -o "-h 127.0.0.1" start && \
    runuser -u postgres -- /usr/lib/postgresql/15/bin/createdb taskflow_test && \
    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/taskflow_test pytest -q --cov=app --cov-report=term-missing

FROM dependencies AS production
RUN useradd --create-home --uid 10001 taskflow
COPY --chown=taskflow:taskflow app app
COPY --chown=taskflow:taskflow alembic alembic
COPY --chown=taskflow:taskflow alembic.ini .
USER taskflow
EXPOSE 10000
CMD ["sh", "-c", "python -m app.db.migrate && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --no-access-log"]
