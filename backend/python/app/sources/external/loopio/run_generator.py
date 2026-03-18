"""Runner script to generate the Loopio DataSource wrapper.

Execute this script to regenerate loopio.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.loopio.run_generator
"""

from app.sources.external.loopio.code_generator import generate_datasource


def main() -> None:
    """Generate the Loopio DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "loopio.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Loopio DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
