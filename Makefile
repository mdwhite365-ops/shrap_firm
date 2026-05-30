.PHONY: install test lint fmt typecheck all

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
