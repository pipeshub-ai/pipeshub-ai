# ruff: noqa: T201
"""Runner script to generate the LumApps DataSource wrapper.

Execute this script to regenerate lumapps.py from the method definitions
in code_generator.py.

Usage:
    python -m app.sources.external.lumapps.run_generator
"""

from app.sources.external.lumapps.code_generator import generate_datasource


def main() -> None:
    """Generate the LumApps DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "lumapps.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated LumApps DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
