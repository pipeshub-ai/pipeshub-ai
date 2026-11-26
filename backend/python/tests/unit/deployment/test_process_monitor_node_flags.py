"""The image starts Node with no flag its Node refuses.

Node 24 makes AsyncLocalStorage use the async context frame by default and refuses
``--experimental-async-context-frame`` ("node: bad option"); an image on Node 24 that passed it never started Node
(the R51-COMPAT upgrade lane, after the Node 24 images of SYNCMAIN-1114).
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[5]


def test_the_image_starts_node_without_a_flag_its_node_refuses() -> None:
    major = re.search(r"^FROM node:(\d+)", (ROOT / "Dockerfile").read_text(), re.M)
    assert major, "the Dockerfile names no Node image"
    launch = re.search(r"^\s*node (.*)dist/index\.js &$", (ROOT / "deployment/process_monitor.sh").read_text(), re.M)
    assert launch, "process_monitor.sh starts no Node"
    if int(major.group(1)) >= 24:
        assert "--experimental-async-context-frame" not in launch.group(1).split()
