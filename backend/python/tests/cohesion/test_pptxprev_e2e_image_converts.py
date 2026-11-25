"""PPTXPREV: the e2e/human stack's app image converts documents as the shipped image does.

The stack ran this tree on bare python:3.12-slim, without the LibreOffice the shipped image installs, so every
PPT/PPTX preview failed with a 500 there and nowhere else. Dockerfile.app installs the shipped image's conversion
packages; this test fails when the two lists drift, and when the stack stops running the app on that image.
"""

from __future__ import annotations

import re

from tests.cohesion.support import REPO_ROOT

E2E = REPO_ROOT / "deployment" / "e2e"
CONVERSION_PACKAGE = re.compile(r"\b(?:libreoffice-[a-z-]+|fonts-[a-z0-9-]+)\b")


def _stage(dockerfile: str, name: str | None) -> str:
    """The instructions of stage ``name`` (the whole file when None), comments dropped."""
    text = (REPO_ROOT / dockerfile).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    if name is None:
        return "\n".join(lines)
    stages = re.split(r"(?m)^FROM\s", "\n".join(lines))
    matching = [stage for stage in stages if re.match(rf"\S+\s+AS\s+{re.escape(name)}\s*$", stage.splitlines()[0])]
    assert len(matching) == 1, f"{dockerfile} has no single stage {name}"
    return matching[0]


def _packages(text: str) -> set[str]:
    return set(CONVERSION_PACKAGE.findall(text))


def test_pptxprev_the_e2e_app_image_installs_the_shipped_images_conversion_packages() -> None:
    shipped = _packages(_stage("Dockerfile.base", "runtime-base")) | _packages(_stage("Dockerfile", "runtime-base"))
    assert "libreoffice-impress-nogui" in shipped
    assert _packages(_stage("deployment/e2e/Dockerfile.app", None)) == shipped


def test_pptxprev_the_stack_runs_the_app_on_the_image_built_from_dockerfile_app() -> None:
    overlay = (E2E / "docker-compose.e2e.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^  pipeshub-ai:\n    <<: \*e2e-python\n    image: \$\{E2E_APP_IMAGE:\?\}$", overlay)
    script = (E2E / "run.sh").read_text(encoding="utf-8")
    assert 'APP_DOCKERFILE="$HERE/Dockerfile.app"' in script
    # Both a new stack and a refreshed one (scripts/human-test-stack.sh refresh -> run.sh rebuild) get the image.
    for function in ("\nprepare() {", "\nrebuild() {"):
        body = script[script.index(function) : script.index("\n}\n", script.index(function))]
        assert "ensure_app_image" in body, function.strip()



def _apt_installed(text: str) -> set[str]:
    """The packages each ``apt-get install`` of ``text`` names."""
    found: set[str] = set()
    for command in re.findall(r"apt-get install((?:[^&]|&(?!&))*)", text.replace("\\\n", " ")):
        found.update(word for word in command.split() if not word.startswith("-") and word != "\\")
    return found


def test_wp81_git_1_the_e2e_app_image_installs_the_git_the_worker_fetches_repositories_with() -> None:
    """WP-81: a long research run's two approved clones were refused "Repository fetch is not available on this deployment":
    the shipped image installs git for repository_fetch (WP-63), the stack's app image did not."""
    assert "git" in _apt_installed(_stage("Dockerfile", "runtime-base"))
    assert "git" in _apt_installed(_stage("deployment/e2e/Dockerfile.app", None))
