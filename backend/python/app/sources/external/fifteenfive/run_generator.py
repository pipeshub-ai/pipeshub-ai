"""Runner script to generate the 15Five DataSource wrapper.

Execute this script to regenerate fifteenfive.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.fifteenfive.run_generator
"""

from app.sources.external.fifteenfive.code_generator import generate_datasource


def main() -> None:
    """Generate the 15Five DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "fifteenfive.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated 15Five DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
