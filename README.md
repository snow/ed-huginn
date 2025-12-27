# Huginn

> *Huginn (HOO-gin) - One of Odin's ravens that flies across the worlds gathering information and returning with intelligence.*

## Project Overview

An Elite Dangerous intelligence-gathering tool for Powerplay 2.0 analysis. Currently focused on finding optimal AFK bounty hunting locations.

## Local Development

### Prerequisites

- Python 3.10+
- Docker & Docker Compose

### Quick Start

```bash
git clone https://github.com/yourusername/ed-huginn.git
cd ed-huginn
/usr/bin/make prepare
source .venv/bin/activate

# Start database
docker compose -f docker-compose.dev.yml up -d

# Seed database (requires galaxy_stations.json.gz in data/)
python -m huginn seed

# Set your power
python -m huginn power
```

### CLI Commands

```bash
python -m huginn                    # Interactive menu
python -m huginn candidates         # List candidate systems
python -m huginn incremental-update # Run all updates (history + candidates + RES)
python -m huginn scheduler          # Start hourly update daemon
```

## Docker Deployment

For NAS or server deployment:

```bash
# Start db + scheduler
docker compose up -d

# Initial seed (one-time)
docker exec huginn-scheduler python -m huginn seed

# Enable periodic updates in data/config.json:
# { "pledged_power": "...", "enable_periodical_update": true }

# View logs
docker logs -f huginn-scheduler
```

The scheduler runs `incremental-update` hourly as a subprocess.

## Configuration

`data/config.json`:

```json
{
  "pledged_power": "Jerome Archer",
  "enable_periodical_update": true
}
```

## License

MIT
