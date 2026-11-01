set positional-arguments

python := env_var_or_default("PYTHON", "python")

# List available commands.
default:
    @just --list

# Generate a standalone demo using only synthetic data.
demo output_dir="demo-output":
    uv run --locked {{ quote(python) }} demo.py --output-dir "$1"

# Install Python and browser-test development dependencies.
setup: sync
    npm ci

# Sync the locked Python development environment.
sync:
    uv sync --locked

# Run all Python and browser checks.
check: check-python test-browser

# Run lint, formatting, Python tests and compilation checks.
check-python: lint format-check test compile

# Lint Python and check import order.
lint:
    uv run --locked ruff check .

# Format Python sources.
format:
    uv run --locked ruff format .

# Check Python formatting without changing files.
format-check:
    uv run --locked ruff format --check .

# Run synthetic collection and rendering tests.
test:
    uv run --locked {{ quote(python) }} -m unittest discover -v

# Check Python syntax.
compile:
    uv run --locked {{ quote(python) }} -m py_compile tab_capsule.py demo.py

# Run browser checks with an isolated temporary profile.
test-browser:
    PYTHON={{ quote(python) }} uv run --locked npm test

# Invoke the CLI with any arguments, for example: just cli --help.
cli *args:
    uv run --locked {{ quote(python) }} tab_capsule.py "$@"

# Collect open tabs and visits, then render the archive; accepts CLI options.
run *args:
    uv run --locked {{ quote(python) }} tab_capsule.py run "$@"

# Collect open-tab snapshots; accepts CLI options.
snapshot *args:
    uv run --locked {{ quote(python) }} tab_capsule.py snapshot "$@"

# Import browsing-history visits; accepts CLI options.
import-history *args:
    uv run --locked {{ quote(python) }} tab_capsule.py import-history "$@"

# Render an existing archive; accepts CLI options.
render *args:
    uv run --locked {{ quote(python) }} tab_capsule.py render "$@"

# Show archive statistics; accepts CLI options.
stats *args:
    uv run --locked {{ quote(python) }} tab_capsule.py stats "$@"
