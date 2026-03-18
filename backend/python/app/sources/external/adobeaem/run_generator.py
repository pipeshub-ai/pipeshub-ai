"""Runner script to generate the Adobe AEM DataSource wrapper.

Execute this script to regenerate adobeaem.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.adobeaem.run_generator
"""

from app.sources.external.adobeaem.code_generator import generate_datasource


def main() -> None:
    """Generate the Adobe AEM DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "adobeaem.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Adobe AEM DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
