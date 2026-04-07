# Configuration Guide

## Source of truth

The **runtime source of truth** for application settings is `src/config.py` via Pydantic `BaseSettings`.

## Role of `config/config.yaml`

`config/config.yaml` should be treated as:
- a deployment/example configuration reference,
- a human-readable baseline for operators,
- not the authoritative runtime loader unless the application is explicitly extended to load it.

## Recommended precedence

1. Environment variables
2. `.env` file
3. Defaults in `src/config.py`
4. `config/config.yaml` only as documentation/reference unless wired into runtime

## Current gap

There is still duplication between `src/config.py` and `config/config.yaml`.
To fully eliminate config drift, the next refactor should choose one of these paths:

- **Option A:** keep Pydantic settings as the only runtime config source and reduce YAML to documentation/examples.
- **Option B:** load YAML explicitly, then map values into `Settings`, with environment variables overriding YAML.

## Recommended direction

For this project, **Option A** is the safer near-term choice because it is already aligned with the running code and test setup.