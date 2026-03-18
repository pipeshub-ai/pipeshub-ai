"""Runner script to generate the OneTrust DataSource wrapper.

Execute this script to regenerate onetrust.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.onetrust.run_generator
"""

from app.sources.external.onetrust.code_generator import generate_datasource


def main() -> None:
    """Generate the OneTrust DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "onetrust.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated OneTrust DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
