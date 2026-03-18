"""Runner script to generate the Fellow DataSource wrapper.

Execute this script to regenerate fellow.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.fellow.run_generator
"""

from app.sources.external.fellow.code_generator import generate_datasource


def main() -> None:
    """Generate the Fellow DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "fellow.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Fellow DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
