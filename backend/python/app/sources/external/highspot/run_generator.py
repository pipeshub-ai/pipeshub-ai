"""Runner script to generate the Highspot DataSource wrapper.

Execute this script to regenerate highspot.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.highspot.run_generator
"""

from app.sources.external.highspot.code_generator import generate_datasource


def main() -> None:
    """Generate the Highspot DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "highspot.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Highspot DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
