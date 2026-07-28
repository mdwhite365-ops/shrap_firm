.PHONY: install test lint fmt typecheck all deploy-drift

install:
	pip install -e '.[dev]'

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
