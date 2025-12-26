VENV := .venv
BIN := $(VENV)/bin
PYTHON := $(BIN)/python
PIP := $(BIN)/pip

# Marker file to track when deps were last installed
DEPS_MARKER := $(VENV)/.deps_installed

.PHONY: prepare clean help run db-up db-down

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  prepare  - Create venv and install dependencies (only if needed)"
	@echo "  run      - Run Huginn interactive menu"
	@echo "  db-up    - Start PostGIS dev database"
	@echo "  db-down  - Stop PostGIS dev database"
	@echo "  clean    - Remove venv and cached files"

# Create venv if it doesn't exist
$(VENV):
	python3 -m venv $(VENV)

# Install deps only if requirements.txt is newer than marker
$(DEPS_MARKER): $(VENV) requirements.txt
	$(PIP) install -r requirements.txt
	@touch $(DEPS_MARKER)

prepare: $(DEPS_MARKER)
	@echo "Environment ready. Run: source $(VENV)/bin/activate"

clean:
	rm -rf $(VENV) __pycache__ huginn/__pycache__ huginn/**/__pycache__

run: $(DEPS_MARKER)
	$(PYTHON) -m huginn

db-up:
	docker compose -f docker-compose.dev.yml up -d

db-down:
	docker compose -f docker-compose.dev.yml down
