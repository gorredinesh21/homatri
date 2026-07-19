# Homaatri dev shortcuts (Windows: use `py` venv or Git Bash)
.PHONY: install dev test lint run up down logs migrate revision

VENV=.venv
PY=$(VENV)/Scripts/python.exe

install:
	python -m venv $(VENV)
	$(PY) -m pip install -U pip
	$(PY) -m pip install -r requirements-dev.txt

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check app

run:            ## run locally against DATABASE_URL (sqlite or postgres)
	$(PY) -m uvicorn app.main:app --reload --port 8000

up:             ## full stack in Docker (app + postgres/pgvector)
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f app

migrate:        ## apply migrations (inside container or with DATABASE_URL set)
	$(PY) -m alembic upgrade head

revision:       ## autogenerate a migration (needs a live Postgres)
	$(PY) -m alembic revision --autogenerate -m "$(m)"
