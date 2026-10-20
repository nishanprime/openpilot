"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's accelerator events. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Jetlink's onroad events, beside sunnypilot's in EventsSP (events_sp.jetlink), and
what selfdrived raises as the large model joins mid-drive and leaves again.

A chestnut is loaded before the first modelV2; Jetlink's large model joins onto
a modelV2 the small model already publishes, and can leave and come back. It
swaps in only while nothing is in control (openpilot, or MADS in any state but
disabled), so the driver is told when it is ready and disengages fully to use
it. For a second after a swap nothing engages while the large model builds its
history and proves it keeps up (jetlink hands back on a held frame in that
second), and the "Big Model Active" chime at the end of it says the driver can.
When it leaves, the small model drives on and the driver is told; nothing
disengages.

Either way the switch costs modeld a frame or two, and a frame the camera
dropped is an invalid pose from the model and a locationd soft disable on the
next tick. So a switch is `settling` for a second, which selfdrived treats as
it does a chestnut's load (big_model_settling).
"""
import bisect

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom, log
from opendbc.car.structs import car
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.selfdrived.events import Events, EventName
from openpilot.sunnypilot import jetlink_adapter
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import EventsBase, Priority, ET, Alert, AlertCallbackType

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert
EventNameSP = custom.OnroadEventSP.EventName
EventNameJetlink = custom.OnroadEventJetlink.EventName
JetlinkState = custom.ModelDataV2Jetlink.State

EVENT_NAME_JETLINK = {v: k for k, v in EventNameJetlink.schema.enumerants.items()}

# how long each event is raised for, in selfdrived's ticks. The link-lost warning covers about
# the time the small model takes to refill its history; the no-entry after a swap, about 20
# frames of the large model's. An enable inside that second is refused and pressed again after
# the chime
OFFER_TICKS = round(3. / DT_CTRL)
HANDBACK_TICKS = round(5. / DT_CTRL)
SWITCHING_TICKS = round(1. / DT_CTRL)


def big_model_ready_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int,
                          personality) -> Alert:
  # sunnypilot's bigModelReady: Jetlink's comes a second after its swap, when the driver can
  # engage on it; its offer to switch is bigModelAvailable, titled "Big Model Ready"
  jetlink = sm['modelDataV2SP'].jetlink.state != JetlinkState.none
  return Alert("Big Model Active" if jetlink else "Big Model Ready", "",
               AlertStatus.normal, AlertSize.small,
               Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 2.)


EVENTS_JETLINK: dict[int, dict[str, Alert | AlertCallbackType]] = {
  # ready while something is in control: it swaps in only when nothing is, so the next
  # engagement after a full disengage drives it. Raised for 3 s, no longer than the wait
  EventNameJetlink.bigModelAvailable: {
    ET.PERMANENT: Alert(
      "Big Model Ready",
      "Disengage fully to switch",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, .2),
  },

  # lost or too slow while engaged: the small model drives on from a reset history and nothing
  # disengages. As loud as a soft disable for 5 s, but it says what happened, not TAKE CONTROL:
  # nothing has let go. A disengage ends it
  EventNameJetlink.bigModelLinkLost: {
    ET.WARNING: Alert(
      "Big Model Lost",
      "Using small model",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.steerRequired, AudibleAlert.warningSoft, .2),
  },
}


class EventsJetlink(EventsBase):
  def __init__(self):
    super().__init__()
    self.event_counters = dict.fromkeys(EVENTS_JETLINK.keys(), 0)

  def get_events_mapping(self) -> dict[int, dict[str, Alert | AlertCallbackType]]:
    return EVENTS_JETLINK

  def get_event_name(self, event: int):
    return EVENT_NAME_JETLINK[event]

  def get_event_msg_type(self):
    return custom.OnroadEventJetlink.Event


class EventsWithJetlink(EventsBase):
  """EventsSP's base: sunnypilot's events, with Jetlink's beside them in `jetlink`. The two
  enums share int values, so add/has/remove/contains_in_list take a name of their own list
  (events_sp.jetlink.add for Jetlink's); what selfdrived asks of all of them at once (clear,
  contains an event type, create_alerts, names for its log-on-change check) covers both,
  sunnypilot's first. to_msg stays sunnypilot's list, as published (onroadEventsSP.events,
  longitudinalPlanSP.events); onroadEventsSP.jetlink.events carries Jetlink's."""

  def __init__(self):
    super().__init__()
    self.jetlink = EventsJetlink()

  @property
  def names(self) -> list:
    return self.events + [EVENT_NAME_JETLINK[e] for e in self.jetlink.events] if self.jetlink.events else self.events

  def clear(self) -> None:
    super().clear()
    self.jetlink.clear()

  def contains(self, event_type: str) -> bool:
    return super().contains(event_type) or self.jetlink.contains(event_type)

  def create_alerts(self, event_types: list[str], callback_args=None):
    return super().create_alerts(event_types, callback_args) + self.jetlink.create_alerts(event_types, callback_args)

  def add_from_msg(self, events):
    super().add_from_msg(events)
    for e in getattr(events, 'jetlink', ()):
      bisect.insort(self.jetlink.events, e)


class JetlinkSwapEvents:
  # Jetlink is optional: its owner exiting costs the big model, never engagement. manager
  # starts it again, but until then it is not running, which processNotRunning would hold as
  # NO_ENTRY
  OPTIONAL_PROCESSES = frozenset({jetlink_adapter.OWNER})

  def __init__(self):
    self.offered = False
    self.big_model_running = False
    # ticks left of each event, and of the settling after a switch either way
    self.offer = self.handback = self.switching = self.settle = 0

  @property
  def settling(self) -> bool:
    """Within a second of the big model swapping in or handing back."""
    return self.settle > 0

  def update(self, sm: messaging.SubMaster, in_control: bool, events: Events, events_sp) -> None:
    """`in_control`: openpilot or MADS is engaged, MADS even while its lateral is paused: the
    adapter's swap gate (jetlink_adapter.Adapter.in_control) is shut exactly then, and also
    while its inputs are late or invalid. `events_sp` is selfdrived's EventsSP."""
    state = sm['modelDataV2SP'].jetlink.state
    big = sm['modelV2'].big

    # stale state neither offers the switch nor rearms the offer
    if all(sm.seen[s] and sm.alive[s] and sm.valid[s] for s in ('modelV2', 'modelDataV2SP')):
      if state != JetlinkState.ready or big:
        # ended by the swap as well as by the link going: an offer still on screen after the
        # switch read as if it had not happened
        self.offered = False
        self.offer = 0
      elif in_control and not self.offered and self.handback == 0:
        # with nothing in control it swaps in at once and bigModelReady says so. Once per
        # readiness, not at every stop, and after the link-lost warning, which would hide it
        self.offered = True
        self.offer = OFFER_TICKS

    # a chestnut dropping modelV2.big raises the native bigModelFailed; this is Jetlink's alone
    running_big = sm.alive['modelV2'] and sm.valid['modelV2'] and big and state != JetlinkState.none
    if running_big and not self.big_model_running:
      self.switching = SWITCHING_TICKS + 1   # the no-entry, then the chime
      self.settle = SWITCHING_TICKS + 1      # counted down below, this tick included
    elif self.big_model_running and not running_big:
      self.switching = 0
      self.settle = SWITCHING_TICKS + 1
      if in_control:
        self.handback = HANDBACK_TICKS
    self.big_model_running = running_big
    if self.settle > 0:
      self.settle -= 1
    if not in_control:
      self.handback = 0

    if self.offer > 0:
      self.offer -= 1
      events_sp.jetlink.add(EventNameJetlink.bigModelAvailable)
    if self.handback > 0:
      self.handback -= 1
      events_sp.jetlink.add(EventNameJetlink.bigModelLinkLost)
    if self.switching > 0:
      self.switching -= 1
      # the chime waits for the end of the second, when it means the driver can engage
      events_sp.remove(EventNameSP.bigModelReady)
      if self.switching:
        # upstream's no-entry for a big model that is not ready to drive. Native, since the
        # main state machine reads only native events
        events.add(EventName.bigModelLoading)
      else:
        events_sp.add(EventNameSP.bigModelReady)
