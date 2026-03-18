"""Runner script to generate the Interact DataSource wrapper.

Execute this script to regenerate interact.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.interact.run_generator
"""

from app.sources.external.interact.code_generator import generate_datasource


def main() -> None:
    """Generate the Interact DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "interact.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Interact DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
