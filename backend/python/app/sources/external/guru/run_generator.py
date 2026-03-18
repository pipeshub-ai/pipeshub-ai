"""Runner script to generate the Guru DataSource wrapper.

Execute this script to regenerate guru.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.guru.run_generator
"""

from app.sources.external.guru.code_generator import generate_datasource


def main() -> None:
    """Generate the Guru DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "guru.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Guru DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
