"""Runner script to generate the Seismic DataSource wrapper.

Execute this script to regenerate seismic.py from the endpoint definitions
in code_generator.py.

Usage:
    python -m app.sources.external.seismic.run_generator
"""

from app.sources.external.seismic.code_generator import generate_datasource


def main() -> None:
    """Generate the Seismic DataSource file."""
    code = generate_datasource()
    output_path = __file__.replace("run_generator.py", "seismic.py")
    with open(output_path, "w") as f:
        f.write(code)
    print(f"Generated Seismic DataSource -> {output_path}")
    print(f"  Total characters: {len(code)}")


if __name__ == "__main__":
    main()
