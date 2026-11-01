set positional-arguments

python := env_var_or_default("PYTHON", "python")

# List available commands.
default:
    @just --list

# Generate a standalone demo using only synthetic data.
demo output_dir="demo-output":
    uv run --locked {{ quote(python) }} demo.py --output-dir "$1"

# Install Python and browser-test development dependencies.
setup:
    uv sync --locked
    npm ci

# Run all Python and browser checks.
check: check-python test-browser

# Run Python checks without Node or a browser.
check-python: lint typecheck test
    uv run --locked {{ quote(python) }} -m compileall -q tab_capsule tests tab_capsule.py demo.py

# Check Python lint, import order and formatting.
lint:
    uv run --locked ruff check .
    uv run --locked ruff format --check .

# Check Python data contracts and function signatures.
typecheck:
    uv run --locked mypy

# Format Python sources.
format:
    uv run --locked ruff format .

# Run synthetic collection and rendering tests.
test:
    uv run --locked {{ quote(python) }} -m unittest discover -v

# Run browser checks with an isolated temporary profile.
test-browser:
    PYTHON={{ quote(python) }} uv run --locked npm test

# Invoke the CLI with any arguments, for example: just cli --help.
cli *args:
    uv run --locked {{ quote(python) }} tab_capsule.py "$@"
