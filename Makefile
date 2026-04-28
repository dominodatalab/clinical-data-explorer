.PHONY: test test-e2e test-all test-external

test:
	pytest tests/contract

test-e2e:
	pytest tests/e2e

test-all:
	pytest tests/contract tests/e2e

test-external:
	@pytest -m external; code=$$?; if [ $$code -eq 5 ]; then echo "(no external tests registered yet)"; exit 0; else exit $$code; fi
