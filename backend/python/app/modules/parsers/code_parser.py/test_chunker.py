import json
from pathlib import Path

from parser import detect_language, extract_blocks

INPUT_PATH = Path(
    "/Users/pipeshub/workspace/pipeshub-ai-kaushal/repo2/backend/python/app/models/blocks.py"
)

with INPUT_PATH.open("rb") as f:
    source = f.read()

blocks = extract_blocks(source, detect_language(INPUT_PATH.name))

output_path = INPUT_PATH.with_name(f"{INPUT_PATH.stem}.code_blocks.json")
with output_path.open("w") as out:
    json.dump(
        [
            {
                k: v.decode() if isinstance(v, bytes) else v
                for k, v in block.__dict__.items()
            }
            for block in blocks
        ],
        out,
        indent=2,
    )
