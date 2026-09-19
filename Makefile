COMPOSE ?= docker compose
TEST_IMAGE ?= python:3.14-slim
IMAGE ?= ghcr.io/michaelmurfy/cleanarr:latest

.PHONY: help up build test pull update

help:
	@echo "make up      # pull GHCR image and start (creates .env from .env.example if missing)"
	@echo "make build  # build the image locally and start"
	@echo "make test   # backend suite in a throwaway container"
	@echo "make pull   # docker pull the published Cleanarr image"
	@echo "make update # pull the latest published image and restart"

.env:
	cp .env.example .env

# Pull the published image and start the stack.
up: .env
	$(COMPOSE) pull
	$(COMPOSE) up -d

# Build the image locally and start.
build: .env
	$(COMPOSE) up -d --build

# Run the backend suite in a throwaway container. Pip cache persists between runs.
test:
	docker run --rm \
		-v cleanarr-pip-cache:/root/.cache/pip \
		-v $(CURDIR)/backend:/src:ro \
		$(TEST_IMAGE) \
		sh -c 'cp -r /src /app && cd /app && pip install -q -r requirements-dev.txt && pytest'

# Refresh the published Cleanarr image from GHCR.
pull:
	docker pull $(IMAGE)

# Pull the latest published image and restart.
update: .env
	$(COMPOSE) pull
	$(COMPOSE) up -d
