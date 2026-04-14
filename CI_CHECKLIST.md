# CI Checklist

Dùng checklist này trước khi merge hoặc release.

## Quick local CI

```bash
make ci
```

Strict quality gate:

```bash
make ci-full
```

## Chi tiết

### 1. Cài dependencies

```bash
pip install -e ".[dev]"
```

### 2. Chạy test suite

```bash
pytest -q
```

### 3. Chạy smoke test router

```bash
pytest -q tests/test_router_smoke.py
```

### 4. Lint

```bash
ruff check src tests
```

### 5. Format check

```bash
ruff format --check src tests
```

### 6. Type check

```bash
mypy src
```

Hoặc chạy gộp:

```bash
make quality
```

### 7. Security scan

```bash
bandit -r src
```

### 8. Dependency audit

```bash
pip_audit
```

## Recommended merge gate

Tối thiểu nên pass:

- `make ci`

Gate chặt hơn:

- `make ci-full`

## Notes

- CI hiện chạy trên Python 3.11.
- Nếu test local bằng Python 3.10, cần đảm bảo dependency đồng bộ với `requirements.txt` và `pyproject.toml`.
- Khi refactor router hoặc wiring FastAPI, luôn chạy lại `tests/test_router_smoke.py`.
