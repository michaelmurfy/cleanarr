COMPOSE ?= docker compose
TEST_IMAGE ?= python:3.14-slim
IMAGE ?= ghcr.io/michaelmurfy/cleanarr:latest

.PHONY: help up build test pull update env

help:
	@echo "make up      # pull GHCR image and start"
	@echo "make env    # create .env from .env.example if missing"
	@echo "make build  # docker build from this repo and start"
	@echo "make test   # backend suite in a throwaway container"
	@echo "make pull   # docker pull the published Cleanarr image"
	@echo "make update # pull the latest published image and restart"

# Optional: copy the example env so you can lock credentials / services via file.
env:
	@test -f .env || cp .env.example .env

# Pull the published image and start the stack. No .env required — configure in Settings.
up:
	$(COMPOSE) pull
	$(COMPOSE) up -d

# Build the image locally (full git checkout) and start.
build:
	docker build -t $(IMAGE) .
	$(COMPOSE) up -d

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
update:
	$(COMPOSE) pull
	$(COMPOSE) up -d
