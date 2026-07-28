.PHONY: install test lint fmt typecheck all deploy-drift

# `.[dev]` is tooling only and cannot collect the test suite: tests import agent
# modules directly, so 13 files failed on a clean environment while a stale
# local venv reported green. The `test` extra pulls in every agent's deps.
install:
	pip install -e '.[dev,test]'

test:
	pytest

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

typecheck:
	mypy src/

all: install lint typecheck test

# Report compose services defined in infra/docker-compose.yml but not running.
# Run this on the Dell after any deploy — the per-service deploy pattern
# silently skips services that were never explicitly named (KI-014).
deploy-drift:
	sudo ./infra/check-deploy-drift.sh
