"""PyInstaller entry point (the package itself uses relative imports)."""

import sys

from rc_controller.app import main

sys.exit(main())
