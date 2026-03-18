"""Runner script to generate the SAP Ariba DataSource wrapper.

Execute this script to regenerate ariba.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.ariba.run_generator
"""

from app.sources.external.ariba.code_generator import generate_datasource


def main() -> None:
    """Generate the SAP Ariba DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "ariba.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated SAP Ariba DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
