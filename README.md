# Huginn

> *Huginn (HOO-gin) - One of Odin's ravens that flies across the worlds gathering information and returning with intelligence.*

## Project Overview

An Elite Dangerous intelligence-gathering tool for Powerplay 2.0 analysis. Currently focused on finding optimal AFK bounty hunting locations.

## Usage

Download `galaxy_stations.json.gz` from <https://spansh.co.uk/dumps>.

```bash
git clone https://github.com/snow/ed-huginn.git
cd ed-huginn
docker compose up -d
mv /path/to/galaxy_stations.json.gz data/

# Access the interactive menu
docker exec -it huginn-scheduler python -m huginn
# Then:
# 1. Set pledged power
# 2. Seed database
# 3. List candidates
```

### Enable Incremental Updates

Set `enable_periodical_update` to `true` in `data/config.json`:

```json
{
  "pledged_power": "Jerome Archer",
  "enable_periodical_update": true
}
```

The scheduler runs `incremental-update` hourly as a subprocess.

### View Logs

```bash
docker logs -f huginn-scheduler
```

## TODO

- There are missing systems in galaxy_stations.json.gz and galaxy_populated.json.gz
- Probably drop edtools.cc support

## License

MIT
