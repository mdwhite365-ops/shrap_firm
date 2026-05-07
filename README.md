# Shrap

A self-developing, self-improving, self-trading firm built primarily by AI agents under human architectural direction.

**Status:** Phase 0 — Documentation. No code yet.

See [`docs/00-vision.md`](docs/00-vision.md) for the full vision.

## Repository structure
cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*.so
*.egg-info/
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Environments and secrets
.env
.env.*
!.env.example
*.key
*.pem
secrets/

# Editors
.vscode/
.idea/
*.swp
*.swo
.DS_Store

# Data and models (don't commit large binaries)
data/raw/
data/processed/
*.parquet
*.csv
*.feather
*.h5
models/
*.pt
*.pth
*.gguf

# Logs and outputs
logs/
*.log
output/
tmp/

# Trading-specific (NEVER commit)
broker_credentials/
api_keys/
positions_real/
