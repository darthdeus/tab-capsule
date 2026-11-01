set positional-arguments

python := env_var_or_default("PYTHON", "python3")

# List available commands.
default:
    @just --list

# Generate a standalone demo using only synthetic data.
demo output_dir="demo-output":
    {{ quote(python) }} demo.py --output-dir "$1"

# Install development-only browser-test dependencies.
setup:
    npm ci

# Run Python tests, compilation and browser checks.
check: test compile test-browser

# Run synthetic collection and rendering tests.
test:
    {{ quote(python) }} -m unittest discover -v

# Check Python syntax.
compile:
    {{ quote(python) }} -m py_compile tab_capsule.py demo.py

# Run browser checks with an isolated temporary profile.
test-browser:
    PYTHON={{ quote(python) }} npm test

# Invoke the CLI with any arguments, for example: just cli --help.
cli *args:
    {{ quote(python) }} tab_capsule.py "$@"

# Collect open tabs and visits, then render the archive; accepts CLI options.
run *args:
    {{ quote(python) }} tab_capsule.py run "$@"

# Collect open-tab snapshots; accepts CLI options.
snapshot *args:
    {{ quote(python) }} tab_capsule.py snapshot "$@"

# Import browsing-history visits; accepts CLI options.
import-history *args:
    {{ quote(python) }} tab_capsule.py import-history "$@"

# Render an existing archive; accepts CLI options.
render *args:
    {{ quote(python) }} tab_capsule.py render "$@"

# Show archive statistics; accepts CLI options.
stats *args:
    {{ quote(python) }} tab_capsule.py stats "$@"
