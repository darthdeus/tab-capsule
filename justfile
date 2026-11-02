set positional-arguments

python := env_var_or_default("PYTHON", "python")

# List available commands.
default:
    @just --list

# Generate a standalone demo using only synthetic data.
demo output_dir="demo-output":
    uv run --locked {{ quote(python) }} demo.py --output-dir "$1"

# Seed an isolated, realistic archive and fake Chrome profiles (30 days, 6,000 visits by default).
seed *args:
    uv run --locked {{ quote(python) }} demo.py --keep-db --corpus realistic --output-dir demo-output/sandbox --days 30 --visits-per-day 100 --profiles 2 "$@"

# Run the CLI against seeded data; both paths are pinned to the synthetic workspace.
sandbox *args:
    test -f demo-output/sandbox/capsule.db
    uv run --locked {{ quote(python) }} tab_capsule.py "$@" --data-dir demo-output/sandbox --chrome-dir demo-output/sandbox/chrome

# Install the Python development tools (Ruff, mypy); tests need none of them.
setup:
    uv sync --locked

# Run lint, formatting, type checks, all tests (including the browser) and compilation.
check: lint typecheck test compile

# Run checks that need only python3 and Chrome: no uv, no network, no packages.
check-offline:
    {{ quote(python) }} -m unittest discover -v
    {{ quote(python) }} -m compileall -q tab_capsule tests tab_capsule.py demo.py

# Compile all Python sources.
compile:
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

# Run all tests, including headless-Chrome page checks (CHROME_BIN selects the browser).
test:
    uv run --locked {{ quote(python) }} -m unittest discover -v

# Run tests without a browser.
test-python:
    TAB_CAPSULE_SKIP_BROWSER=1 uv run --locked {{ quote(python) }} -m unittest discover -v

# Run only the headless-Chrome page checks, each in an isolated browser context.
test-browser:
    uv run --locked {{ quote(python) }} -m unittest -v tests.test_browser

# Invoke the CLI with any arguments, for example: just cli --help.
cli *args:
    uv run --locked {{ quote(python) }} tab_capsule.py "$@"
