# ruff: noqa: T201
"""Runner script to generate the DataStax DataSource wrapper.

Execute this script to regenerate datastax.py from the method definitions
in code_generator.py.

Usage:
    python -m app.sources.external.datastax.run_generator
"""

from app.sources.external.datastax.code_generator import generate_datasource


def main() -> None:
    """Generate the DataStax DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "datastax.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated DataStax DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
