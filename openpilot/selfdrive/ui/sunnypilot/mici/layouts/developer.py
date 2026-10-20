"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's mici developer panel. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.mici.layouts.settings.developer import DeveloperLayoutMici
from openpilot.selfdrive.ui.ui_state import ui_state


class DeveloperLayoutMiciSP(DeveloperLayoutMici):
  def __init__(self):
    super().__init__()
    # Jetlink holds the USB port that ADB needs (UIStateJetlink turns ADB off while the link is on)
    self._adb_toggle.set_enabled(lambda: ui_state.is_offroad() and not ui_state.adb_blocked)
