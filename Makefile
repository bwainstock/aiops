.PHONY: sync format format-check lint typecheck test compose-config observability-check \
	copilot-preflight copilot-smoke check \
	load trigger-error trigger-latency trigger-down clear-down clear-alert-events

sync:
	uv sync --all-packages

format:
	uv run --all-packages ruff format .

format-check:
	uv run --all-packages ruff format --check .

lint:
	uv run --all-packages ruff check .

typecheck:
	uv run --all-packages pyright

test:
	uv run --all-packages pytest

compose-config:
	docker compose config --quiet

observability-check:
	docker run --rm --entrypoint /bin/promtool \
		-v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" \
		prom/prometheus:v3.16.0 check config /etc/prometheus/prometheus.yml
	docker run --rm --entrypoint /bin/promtool \
		-v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" \
		prom/prometheus:v3.16.0 check rules /etc/prometheus/rules/demo-api.yml
	docker run --rm --entrypoint /bin/amtool \
		-v "$(CURDIR)/observability/alertmanager:/etc/alertmanager:ro" \
		prom/alertmanager:v0.35.0 check-config /etc/alertmanager/alertmanager.yml
	docker run --rm \
		-v "$(CURDIR)/observability/loki/loki.yml:/etc/loki/loki.yml:ro" \
		grafana/loki:3.7.9 -config.file=/etc/loki/loki.yml -verify-config=true
	docker run --rm \
		-v "$(CURDIR)/observability/alloy/config.alloy:/etc/alloy/config.alloy:ro" \
		grafana/alloy:v1.21.3 fmt --test /etc/alloy/config.alloy
	docker run --rm \
		-v "$(CURDIR)/observability/tempo/tempo.yml:/etc/tempo/tempo.yml:ro" \
		grafana/tempo:3.1.1 -config.file=/etc/tempo/tempo.yml -config.verify=true
	jq empty observability/grafana/dashboards/demo-service.json

check: format-check lint typecheck test compose-config

load:
	docker compose --profile load run --rm loadgen

trigger-error:
	docker compose --profile load run --rm loadgen \
		aiops-loadgen --base-url http://demo-api:8000 --requests 200 \
		--fault error --interval 0.1 --allow-failures

trigger-latency:
	docker compose --profile load run --rm loadgen \
		aiops-loadgen --base-url http://demo-api:8000 --requests 50 \
		--fault latency

trigger-down:
	docker compose stop demo-api

clear-down:
	docker compose up -d demo-api

clear-alert-events:
	curl --fail --silent --show-error -X DELETE \
		http://127.0.0.1:8090/api/v1/events >/dev/null

copilot-preflight:
	uv run aiops-copilot-smoke --preflight-only

copilot-smoke:
	uv run aiops-copilot-smoke
