"""Runner script to generate the eSalesManager DataSource wrapper.

Execute this script to regenerate esalesmanager.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.esalesmanager.run_generator
"""

from app.sources.external.esalesmanager.code_generator import generate_datasource


def main() -> None:
    """Generate the eSalesManager DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "esalesmanager.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated eSalesManager DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
