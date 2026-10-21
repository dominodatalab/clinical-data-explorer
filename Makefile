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
	$(MAKE) test
	$(MAKE) test-unit
	$(MAKE) test-frontend
	$(MAKE) test-mocked-integration
	$(MAKE) test-e2e

test-external:
	@effective_gateway_url="$${DOMINO_LLM_GATEWAY_URL:-https://apps.cloud-dogfood.domino.tech/apps/llm_gateway}"; \
	case "$${effective_gateway_url%/}" in \
		https://apps.cloud-dogfood.domino.tech/apps/llm_gateway|https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1) \
			echo "NOTICE: The Cloud Dogfood Gateway is selected; if API_KEY_OVERRIDE is set, it must be a Domino PAT issued by cloud-dogfood." >&2 ;; \
	esac; \
	if [ -z "$${API_KEY_OVERRIDE:-}" ]; then \
		echo "WARNING: API_KEY_OVERRIDE is not set; Domino API calls may fail unless a Domino access-token sidecar is available." >&2; \
	fi; \
	DOMINO_LLM_GATEWAY_URL="$$effective_gateway_url" \
	DOMINO_LLM_GATEWAY_MODEL="$${DOMINO_LLM_GATEWAY_MODEL:-sonnet5-5}" \
	uv run --locked pytest -m external; \
	code=$$?; \
	if [ $$code -eq 5 ]; then echo "(no external tests registered yet)"; exit 0; else exit $$code; fi
