from importlib.metadata import version

import likhit


def test_package_import_exposes_version() -> None:
    assert likhit.__version__ == "0.1.0"
    assert version("likhit") == likhit.__version__
