# ruff: noqa: T201
"""Runner script to generate the Benchling DataSource wrapper.

Execute this script to regenerate benchling.py from the method definitions
in code_generator.py.

Usage:
    python -m app.sources.external.benchling.run_generator
"""

from app.sources.external.benchling.code_generator import generate_datasource


def main() -> None:
    """Generate the Benchling DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "benchling.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Benchling DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
