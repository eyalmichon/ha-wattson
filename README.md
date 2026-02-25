# Wattson

[![hacs][hacsbadge]][hacs]
[![release][releasebadge]][release]
[![lint][lintbadge]][lint]

Home Assistant integration that learns your appliances' power patterns to detect cycles and estimate time remaining.

## Installation

### HACS (Recommended)

1. Click the button below to open the repository in HACS:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eyalmichon&repository=ha-wattson&category=integration)
2. Click **Download**.
3. Restart Home Assistant.

### Manual

1. Download the latest release
2. Extract and copy `custom_components/wattson` to your `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration**
3. Search for **Wattson**

## Development

This project uses a devcontainer for development. Open in VS Code or Cursor and click "Reopen in Container".

```bash
# Start Home Assistant
scripts/develop

# Run tests
pytest
```

[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[release]: https://github.com/eyalmichon/ha-wattson/releases
[releasebadge]: https://img.shields.io/github/v/release/eyalmichon/ha-wattson
[lint]: https://github.com/eyalmichon/ha-wattson/actions/workflows/lint.yaml
[lintbadge]: https://github.com/eyalmichon/ha-wattson/actions/workflows/lint.yaml/badge.svg
