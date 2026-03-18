"""Runner script to generate the PlusPlus DataSource wrapper.

Execute this script to regenerate plusplus.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.plusplus.run_generator
"""

from app.sources.external.plusplus.code_generator import generate_datasource


def main() -> None:
    """Generate the PlusPlus DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "plusplus.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated PlusPlus DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
