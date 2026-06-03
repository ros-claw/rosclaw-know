# PyPI publication guide

`rosclaw-know@1.5.0a1` is built and verified locally; this doc tells
operators what to do when publication policy is settled.

## Current state

- `pyproject.toml` declares `version = "1.5.0a1"`.
- `python -m build` produces:
  - `dist/rosclaw_know-1.5.0a1-py3-none-any.whl` (~180 KB)
  - `dist/rosclaw_know-1.5.0a1.tar.gz` (~220 KB)
- Both install cleanly into a fresh venv with `pip install rosclaw-know-1.5.0a1-py3-none-any.whl`.
- All runtime-facing imports succeed inside the rosclaw target venv.

## Open decision

The two repos (`ros-claw/rosclaw-know`, `ros-claw/rosclaw`) stay
**PRIVATE on GitHub**.  PyPI publication is a separate decision:

| Index | Pros | Cons |
|---|---|---|
| Public **pypi.org** | Standard `pip install rosclaw-know`; no infra | Source is publicly indexed even though the GitHub repo is private |
| Private **pypiserver / Gemfury / AWS CodeArtifact** | Honors the private-repo constraint | Consumers need an index URL + credential per env |
| GitHub Releases as a wheel artifact | Stays inside the org | No `pip install <name>` story; consumers do `pip install <url>` |

Until this is decided, downstream consumers (including `rosclaw`)
should install via path or git URL:

```toml
# rosclaw/pyproject.toml
dependencies = [
    # local-dev: editable from sibling checkout
    "rosclaw-know @ file:///path/to/rosclaw-know",
    # or git: pinned by SHA
    "rosclaw-know @ git+https://github.com/ros-claw/rosclaw-know.git@<sha>",
]
```

## Publication runbook (when greenlit)

### Public PyPI

```bash
# 1. Verify version is bumped (no .devN suffix unless intentional).
grep '^version' pyproject.toml

# 2. Clean dist/ and build fresh artifacts.
rm -rf dist/
python -m build

# 3. (Optional) Quick install-and-import test in a fresh venv.
python -m venv /tmp/rk-check
/tmp/rk-check/bin/pip install dist/*.whl
/tmp/rk-check/bin/python -c "import rosclaw_know; print(rosclaw_know.__version__)"

# 4. Upload (twine prompts for credentials or reads ~/.pypirc).
python -m pip install twine
python -m twine upload dist/*
```

For pre-release versions (`1.5.0a1`, `1.5.0a2`, …), consumers must
opt in:

```bash
pip install --pre rosclaw-know
```

### Private index (pypiserver / Gemfury)

```bash
python -m twine upload --repository-url https://pypi.<org>.internal/legacy/ dist/*
```

Consumers configure either via `~/.pip/pip.conf` (`index-url = ...`)
or a `pip install --index-url=... rosclaw-know` flag.

### GitHub Releases

```bash
# 1. Tag the release.
git tag -a v1.5.0a1 -m "v1.5.0a1 — alpha cut for rosclaw integration"
git push origin v1.5.0a1

# 2. CI workflow `release-assets.yml` (Phase 4) attaches wheel + sdist
#    + asset bundle to the GitHub Release automatically.
```

Consumers install via:

```toml
"rosclaw-know @ https://github.com/ros-claw/rosclaw-know/releases/download/v1.5.0a1/rosclaw_know-1.5.0a1-py3-none-any.whl"
```

## Versioning policy (recommended)

- `1.5.0a1`, `1.5.0a2`, ... — internal alpha, used during the
  rosclaw-integration window.
- `1.5.0rc1` — feature-frozen; only bug-fixes accepted.
- `1.5.0` — first stable, published only after rosclaw runtime tests
  pass against it.
- `1.5.1` — patch series for catalog/asset bumps that don't break the
  pre-flight Task Pack API.
- `1.6.0+` — new compiler features (new sprint).

## What never gets published

- `.env`, `.env.local`, anything under `.gitignore`.
- `data/raw/`, `data/processed/` — these are local research artifacts,
  not catalog assets.  The catalog assets (under `data/assets/`) are
  published via the **release-assets** CI workflow (Phase 4), not via
  the wheel itself, so consumers can fetch them independently of the
  Python install.
