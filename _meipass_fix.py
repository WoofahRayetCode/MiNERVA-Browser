import sys
import os
if hasattr(sys, "_MEIPASS"):
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ.get("PATH", "")
