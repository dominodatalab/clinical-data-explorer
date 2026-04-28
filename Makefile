.PHONY: test test-e2e test-all test-external

test:
	uv run --locked pytest tests/contract

test-e2e:
	uv run --locked pytest tests/e2e

test-all:
	uv run --locked pytest tests/contract tests/e2e

test-external:
	@uv run --locked pytest -m external; code=$$?; if [ $$code -eq 5 ]; then echo "(no external tests registered yet)"; exit 0; else exit $$code; fi
