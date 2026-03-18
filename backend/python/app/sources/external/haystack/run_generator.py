"""Runner script to generate the Haystack DataSource wrapper.

Execute this script to regenerate haystack.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.haystack.run_generator
"""

from app.sources.external.haystack.code_generator import generate_datasource


def main() -> None:
    """Generate the Haystack DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "haystack.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Haystack DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
