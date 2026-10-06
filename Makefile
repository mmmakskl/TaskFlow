PYTHON ?= venv/bin/python

.PHONY: dev test lint format typecheck check migrate migration docker run start
dev:
	python3 -m venv venv
	venv/bin/pip install -r requirements-dev.txt
test:
	$(PYTHON) -m pytest -q
lint:
	$(PYTHON) -m ruff check app tests alembic
	$(PYTHON) -m ruff format --check app tests alembic
format:
	$(PYTHON) -m ruff check --fix app tests alembic
	$(PYTHON) -m ruff format app tests alembic
typecheck:
	$(PYTHON) -m mypy app
check: lint typecheck test
migrate:
	$(PYTHON) -m app.db.migrate
migration:
	@test -n "$(message)" || (echo 'Use make migration message="describe change"'; exit 1)
	$(PYTHON) -m alembic revision --autogenerate -m "$(message)"
docker:
	docker build --target production -t taskflow:latest .
run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000
start: migrate run
