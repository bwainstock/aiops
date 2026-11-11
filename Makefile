.PHONY: sync format format-check lint typecheck test compose-config observability-check check

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
		-v "$(CURDIR)/observability/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
		prom/prometheus:v3.16.0 check config /etc/prometheus/prometheus.yml
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
