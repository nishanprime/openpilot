# jetlink's tests import the package from the submodule checkout: the same
# path a device gets from the jetlink symlink launch_chffrplus.sh makes. PC has
# no jetlink install (scripts/lint/check_dependencies.py caps the dependencies),
# so outside tests the adapter answers as if the link were off.
import sys
from pathlib import Path

JETLINK_REPO = str(Path(__file__).resolve().parents[4] / 'jetlink_repo')
if JETLINK_REPO in sys.path:
  sys.path.remove(JETLINK_REPO)
sys.path.insert(0, JETLINK_REPO)
