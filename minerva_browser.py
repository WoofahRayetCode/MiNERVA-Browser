"""
MiNERVA Archive Browser entry point.
"""
from minerva.constants import APP_VERSION, GITHUB_REPO, log_activity
from minerva.ui.app import MinervaApp

if __name__ == "__main__":
    log_activity("app.launch")
    app = MinervaApp()
    log_activity("app.mainloop.start")
    app.mainloop()
    log_activity("app.mainloop.exit")
