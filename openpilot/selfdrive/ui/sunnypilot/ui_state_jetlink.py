"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's UI state. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot import jetlink_adapter


class UIStateJetlink:
  """Jetlink in UIStateSP: its snapshot for the panels and the chestnut icon, and the USB port it
  holds, which turns ADB off."""

  def __init__(self):
    # jetlink's snapshot (jetlink.openpilot.Status) from the params pass; None with a chestnut
    # fitted or no jetlink on this device
    self.jetlink = None
    # Jetlink holds the USB port, so ADB is off and its toggle grayed out
    self.adb_blocked: bool = False
    self._jetlink_state_name: str = 'none'
    self.sm_services_ext.append("modelDataV2SP")

  def update(self) -> None:
    # read where sm is updated, so the params thread never touches a message
    self._jetlink_state_name = str(self.sm['modelDataV2SP'].jetlink.state)

  def update_params(self) -> None:
    # on the 5 Hz params pass, not per frame in a layout; a fitted chestnut owns chestnut_state
    self.jetlink = None if self.sm['deviceState'].chestnutPresent else jetlink_adapter.status()
    self._enforce_usb_port()
    # a host configures the gadget ~25 s after a cold boot, after the one-shot usb_unknown
    # decision; recognizing it late still clears "unknown"
    if (view := self.jetlink_view) is not None and view.present and self.usb_unknown:
      self.usb_unknown = False

  @property
  def jetlink_view(self):
    """jetlink's snapshot when the chestnut icon is the link's: no chestnut fitted, and something to
    show. Presence comes from jetlink: the comma is the gadget and enumerates nothing."""
    s = self.jetlink
    return s if s is not None and (s.enabled or s.present or s.progress is not None) else None

  def _jetlink_state(self, view):
    """ChestnutState for the link: progress and the records offroad, modelV2 and Jetlink's state onroad."""
    from openpilot.selfdrive.ui.ui_state import ChestnutState  # defined by the class that mixes this in
    model_seen = self.sm.recv_frame["modelV2"] > self.started_frame
    running_big = self.sm.alive["modelV2"] and self.sm["modelV2"].big
    return ChestnutState(view.icon(self.started, model_seen, running_big, self._jetlink_state_name))

  def _enforce_usb_port(self) -> None:
    """ADB and Jetlink both need the comma's USB port: the link on turns ADB off, and the developer
    panels grey its toggle out. Here, not in the panels, so a link set from sunnylink counts too.
    jetlink's owner retries the port in seconds while ADB's gadget still holds it."""
    self.adb_blocked = self.jetlink is not None and self.jetlink.enabled
    if self.adb_blocked and self.params.get_bool("AdbEnabled"):
      self.params.put_bool("AdbEnabled", False, block=True)
