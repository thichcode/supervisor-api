.PHONY: install dev test test-smoke lint format-check typecheck security audit ci ci-full quality run docker-build docker-up docker-down migrate

install:
	pip install -e ".[dev]"

dev:
	cp .env.example .env
	docker-compose -f docker-compose.dev.yml up

test:
	pytest -q

test-smoke:
	pytest -q tests/test_router_smoke.py

lint:
	ruff check src tests

format-check:
	ruff format --check src tests

typecheck:
	mypy src

security:
	bandit -r src

audit:
	pip_audit

quality: lint format-check typecheck

ci: test test-smoke

ci-full: ci quality

run:
	python -m src.api

docker-build:
	docker build -t supervisor:latest .

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f supervisor

migrate:
	docker-compose exec postgres psql -U postgres -d supervisor_db -f /docker-entrypoint-initdb.d/init.sql

logs:
	python -m uvicorn src.api:app --reload --log-level debug

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache

fmt:
	ruff format src/ tests/
	ruff check --fix src/ tests/
