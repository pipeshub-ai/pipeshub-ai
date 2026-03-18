"""Runner script to generate the Coupa DataSource wrapper.

Execute this script to regenerate coupa.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.coupa.run_generator
"""

from app.sources.external.coupa.code_generator import generate_datasource


def main() -> None:
    """Generate the Coupa DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "coupa.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Coupa DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
