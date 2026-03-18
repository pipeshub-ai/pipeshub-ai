"""Runner script to generate the Simpplr DataSource wrapper.

Execute this script to regenerate simpplr.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.simpplr.run_generator
"""

from app.sources.external.simpplr.code_generator import generate_datasource


def main() -> None:
    """Generate the Simpplr DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "simpplr.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Simpplr DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
