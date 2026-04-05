.PHONY: install dev test lint run docker-build docker-up docker-down migrate

install:
	pip install -e ".[dev]"

dev:
	cp .env.example .env
	docker-compose -f docker-compose.dev.yml up

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/
	mypy src/

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
