COMPOSE ?= docker compose
TEST_IMAGE ?= python:3.14-slim

.PHONY: help up test pull update

help:
	@echo "make up      # build and start (creates .env from .env.example if missing)"
	@echo "make test    # backend suite in a throwaway container"
	@echo "make pull    # docker pull the Dockerfile base images"
	@echo "make update  # pull + build + restart"

.env:
	cp .env.example .env

# Build and start the stack.
up: .env
	$(COMPOSE) up -d --build

# Run the backend suite in a throwaway container. Pip cache persists between runs.
test:
	docker run --rm \
		-v cleanarr-pip-cache:/root/.cache/pip \
		-v $(CURDIR)/backend:/src:ro \
		$(TEST_IMAGE) \
		sh -c 'cp -r /src /app && cd /app && pip install -q -r requirements-dev.txt && pytest'

# Refresh the base images the Dockerfile builds on.
pull:
	grep -oE '^FROM [^ ]+' Dockerfile | awk '{print $$2}' | sort -u | xargs -n1 docker pull

# Pull base images, rebuild, restart.
update: pull .env
	$(COMPOSE) build
	$(COMPOSE) up -d
