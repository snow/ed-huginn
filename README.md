# Huginn

> *Huginn (HOO-gin) - One of Odin's ravens that flies across the worlds gathering information and returning with intelligence.*

**Repository:** `ed-huginn`
**CLI command:** `huginn`

## Project Overview

An Elite Dangerous intelligence-gathering tool for Powerplay 2.0 analysis. Currently focused on finding optimal AFK bounty hunting locations, designed to expand into broader analysis functions.

Uses PostGIS for local spatial queries, reducing dependence on external APIs and providing offline-capable analysis.

## Setup

### Prerequisites

- Python 3.10+
- Docker & Docker Compose (for PostGIS database)

### Quick Start

```bash
# Clone and setup (creates venv, installs deps - only if needed)
git clone https://github.com/yourusername/ed-huginn.git
cd ed-huginn
make prepare

# Activate the environment
source .venv/bin/activate
```

### Manual Setup

If you prefer not to use Make:

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS (.venv\Scripts\activate on Windows)
pip install -r requirements.txt
```

### Running the CLI

```bash
# With virtual environment activated
python -m huginn --help

# Available commands
python -m huginn seed        # Seed database with Spansh data
python -m huginn candidates  # List candidate systems (WIP)
```

## License

MIT
