.PHONY: check test lint fmt typecheck audit run migrate parse docker-up

check: lint typecheck
	uv run pytest --cov --cov-report=term-missing:skip-covered
	uv run coverage report --include='src/napominator/domain/*,src/napominator/nlp/*' --fail-under=95 --skip-covered

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

fmt:
	uv run ruff check --fix .
	uv run ruff format .

run:
	uv run python -m napominator

audit:
	uv export --frozen --no-emit-project --no-hashes -o .audit-requirements.txt
	uv run pip-audit --strict -r .audit-requirements.txt

migrate:
	uv run alembic upgrade head

parse:
	uv run python -m napominator.nlp "$(TEXT)"

docker-up:
	docker compose up -d --build
