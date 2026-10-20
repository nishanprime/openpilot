"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's hardwared hooks. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time
from collections.abc import Callable

from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot import jetlink_adapter

# how long a shutdown waits on a Jetlink host that has not taken the request to power off:
# jetlink's own SHUTDOWN_TIMEOUT of 25 s, written out so it holds without a jetlink checkout
JETLINK_OFF_WAIT_T = 25.0


class JetlinkHardwared:
  """Jetlink's hooks into hardwared's hardware_thread, one object so hardwared.py carries one-line
  call sites.

  A Jetlink host on its own supply outlives the comma, so a shutdown asks it once to power off and
  DoShutdown waits until it took the request or JETLINK_OFF_WAIT_T has passed. A Mac or a phone
  refuses, and says so. hardware_thread keeps publishing deviceState through that wait, and no
  drive may start under the power-off that follows (powering_off is a startup condition). With the
  link off nothing is asked, and DoShutdown goes on the same loop as upstream's.
  """

  def __init__(self) -> None:
    self.now = time.monotonic
    self.off_requested_ts: float | None = None

  @property
  def powering_off(self) -> bool:
    return self.off_requested_ts is not None

  def alerts(self, set_offroad_alert: Callable[..., None]) -> None:
    # a link the user turned on that cannot come up is otherwise silently absent
    reason = jetlink_adapter.reason()
    set_offroad_alert(jetlink_adapter.OFFROAD_ALERT, reason is not None, extra_text=reason)

  def shutdown_ready(self, should_shutdown: bool, off_ts: float | None) -> bool:
    """True once hardwared may put DoShutdown. Once asked, the power monitor is not consulted again."""
    if self.off_requested_ts is None:
      if not should_shutdown:
        return False
      cloudlog.warning(f"shutting device down, offroad since {off_ts}")
      jetlink_adapter.request_shutdown(f"comma shutting down, offroad since {off_ts}")
      self.off_requested_ts = self.now()
    return not jetlink_adapter.shutdown_pending() or self.now() - self.off_requested_ts >= JETLINK_OFF_WAIT_T
