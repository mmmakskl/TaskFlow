PYTHON ?= venv/bin/python

.PHONY: dev test test-business-unit test-business test-ux lint format typecheck check migrate migration docker run start
dev:
	python3 -m venv venv
	venv/bin/pip install -r requirements-dev.txt
test:
	$(PYTHON) -m pytest -q
test-business-unit:
	$(PYTHON) -m pytest -q tests/unit/test_business_*.py
test-business: test-business-unit
	@test -n "$$TEST_DATABASE_URL" || (echo 'TEST_DATABASE_URL is required (disposable _test database)'; exit 1)
	$(PYTHON) -m pytest -q tests/integration/test_business_*.py tests/integration/test_services.py
test-ux:
	@test -n "$$TEST_DATABASE_URL" || (echo 'TEST_DATABASE_URL is required (disposable _test database)'; exit 1)
	$(PYTHON) -m pytest -q tests/unit/test_interaction.py tests/integration/test_mobile_ux.py tests/integration/test_form_navigation.py tests/integration/test_web_and_bot.py
lint:
	$(PYTHON) -m ruff check app tests alembic tools
	$(PYTHON) -m ruff format --check app tests alembic tools
format:
	$(PYTHON) -m ruff check --fix app tests alembic tools
	$(PYTHON) -m ruff format app tests alembic tools
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
