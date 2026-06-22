# dazzle-preservelib

A new project created from git-repokit-template

## Installation

```bash
pip install dazzle_preservelib
```

### From Source

```bash
git clone https://github.com/DazzleLib/dazzle-preservelib.git
cd dazzle-preservelib
pip install -e ".[dev]"
```

## Usage

```bash
dazzle-preservelib --help
```

## Development

```bash
# Clone and install
git clone https://github.com/DazzleLib/dazzle-preservelib.git
cd dazzle-preservelib
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Install git hooks (if using repokit-common submodule)
bash scripts/repokit-common/install-hooks.sh
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) for details.

