.PHONY: test test-unit test-frontend test-mocked-integration test-e2e test-all test-external

test:
	uv run --locked pytest tests/contract

test-unit:
	uv run --locked pytest tests/unit tests/mcp_server

test-frontend:
	node --experimental-vm-modules --test tests/unit/js/*.test.mjs

test-mocked-integration:
	uv run --locked pytest -m mocked_integration

test-e2e:
	uv run --locked pytest tests/e2e -m "not mocked_integration"

test-all:
	uv run --locked pytest tests/contract tests/e2e tests/unit tests/mcp_server

test-external:
	@uv run --locked pytest -m external; code=$$?; if [ $$code -eq 5 ]; then echo "(no external tests registered yet)"; exit 0; else exit $$code; fi
