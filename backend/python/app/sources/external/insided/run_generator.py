"""Runner script to generate the InSided DataSource wrapper.

Execute this script to regenerate insided.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.insided.run_generator
"""

from app.sources.external.insided.code_generator import generate_datasource


def main() -> None:
    """Generate the InSided DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "insided.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated InSided DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
