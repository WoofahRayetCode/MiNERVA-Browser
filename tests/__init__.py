import os
import tempfile
from pathlib import Path

if not os.environ.get("MINERVA_ERROR_LOG"):
    os.environ["MINERVA_ERROR_LOG"] = str(Path(tempfile.gettempdir()) / "minerva_test_error.log")
