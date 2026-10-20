"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's accelerator event tests. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import inspect
from types import SimpleNamespace

from openpilot.cereal import custom, messaging
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import EVENT_NAME, EVENTS, Events, EventName, ET
from openpilot.selfdrive.selfdrived.state import State, StateMachine
from openpilot.sunnypilot.mads.state import StateMachine as MadsStateMachine, State as MadsState
from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENT_NAME_SP, EVENTS_SP, EventsSP
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import Alert
from openpilot.sunnypilot.selfdrive.selfdrived.jetlink_events import (EVENT_NAME_JETLINK, EVENTS_JETLINK, HANDBACK_TICKS, OFFER_TICKS,
                                                                      SWITCHING_TICKS, JetlinkSwapEvents)

EventNameSP = custom.OnroadEventSP.EventName
EventNameJetlink = custom.OnroadEventJetlink.EventName


class SwapEventsTest(OpenpilotTestCase):
  """The swap events alone, then through selfdrived's own state machines below."""

  def setUp(self):
    super().setUp()
    self.sm = messaging.SubMaster(['modelV2', 'modelDataV2SP'])
    self.events = Events()
    self.events_sp = EventsSP()
    self.swaps = JetlinkSwapEvents()
    for service in self.sm.services:
      self.sm.data[service] = self.sm[service].as_builder()
      self.sm.seen[service] = self.sm.alive[service] = self.sm.valid[service] = True

  def step(self, state='none', big=False, alive=True, enabled=False, mads=False) -> set[str]:
    """One selfdrived tick, `mads` being MADS in any state but disabled; what was raised,
    native, sunnypilot and Jetlink, by name."""
    self.sm['modelDataV2SP'].jetlink.state = state
    self.sm['modelV2'].big = big
    self.sm.alive['modelV2'] = alive
    self.events.clear()
    self.events_sp.clear()
    self.swaps.update(self.sm, enabled or mads, self.events, self.events_sp)
    return {EVENT_NAME[e] for e in self.events.names} | {EVENT_NAME_SP.get(e, e) for e in self.events_sp.names}

  def drive_big(self, **kwargs) -> None:
    """The large model swapped in and past its first second."""
    for _ in range(SWITCHING_TICKS + 1):
      self.step(state='running', big=True, **kwargs)

  def ticks_with(self, event: str, **kwargs) -> int:
    """How many ticks in a row `event` is raised from here on, up to a minute."""
    for n in range(6000):
      if event not in self.step(**kwargs):
        return n
    return 6000


class TestEventTable(OpenpilotTestCase):
  def test_event_ordinals(self):
    # logs store the ordinal
    self.assertEqual((int(EventNameJetlink.bigModelAvailable), int(EventNameJetlink.bigModelLinkLost)), (0, 1))
    self.assertEqual(set(EVENTS_JETLINK), set(EVENT_NAME_JETLINK))

  def test_link_lost_is_a_warning_and_nothing_else(self):
    alerts = EVENTS_JETLINK[EventNameJetlink.bigModelLinkLost]
    self.assertEqual(set(alerts), {ET.WARNING})
    alert = alerts[ET.WARNING]
    self.assertEqual((alert.alert_text_1, alert.alert_text_2), ("Big Model Lost", "Using small model"))

  def test_the_offer_has_no_control_effect(self):
    alerts = EVENTS_JETLINK[EventNameJetlink.bigModelAvailable]
    self.assertEqual(set(alerts), {ET.PERMANENT})
    alert = alerts[ET.PERMANENT]
    self.assertEqual((alert.alert_text_1, alert.alert_text_2), ('Big Model Ready', 'Disengage fully to switch'))

  def test_jetlink_events_ride_in_events_sp_and_its_message(self):
    events_sp = EventsSP()
    events_sp.jetlink.add(EventNameJetlink.bigModelLinkLost)
    self.assertTrue('bigModelLinkLost' in events_sp.names)
    self.assertTrue(events_sp.contains(ET.WARNING))
    msg = messaging.new_message('onroadEventsSP')
    msg.onroadEventsSP.jetlink.events = events_sp.jetlink.to_msg()
    self.assertEqual([e.name for e in msg.onroadEventsSP.jetlink.events], ['bigModelLinkLost'])
    self.assertTrue(msg.onroadEventsSP.jetlink.events[0].warning)
    events_sp.clear()
    self.assertEqual(events_sp.names, [])

  def test_selfdrived_holds_its_errors_on_settling_and_ignores_the_owner(self):
    from openpilot.selfdrive.selfdrived import selfdrived
    update_events = inspect.getsource(selfdrived.SelfdriveD.update_events)
    self.assertTrue('self.jetlink_swaps.settling' in update_events)
    self.assertTrue('self.enabled or self.mads.enabled' in update_events)
    self.assertTrue('JetlinkSwapEvents.OPTIONAL_PROCESSES' in inspect.getsource(selfdrived.SelfdriveD.__init__))
    self.assertTrue('onroadEventsSP.jetlink.events' in inspect.getsource(selfdrived.SelfdriveD.publish_selfdriveState))


class TestSettling(SwapEventsTest):
  """A switch either way is settling for a second: selfdrived holds the comm and localization
  errors the switch's dropped frames would raise."""

  def test_a_swap_in_settles_for_a_second(self):
    self.step()
    self.assertFalse(self.swaps.settling)
    settled = [(self.step(state='running', big=True), self.swaps.settling)[1] for _ in range(SWITCHING_TICKS + 5)]
    self.assertEqual(settled, [True] * SWITCHING_TICKS + [False] * 5)

  def test_a_hand_back_settles_for_a_second(self):
    self.drive_big()
    self.assertFalse(self.swaps.settling)
    settled = [(self.step(state='retrying', big=False), self.swaps.settling)[1] for _ in range(SWITCHING_TICKS + 5)]
    self.assertEqual(settled, [True] * SWITCHING_TICKS + [False] * 5)


class TestHandBack(SwapEventsTest):
  def test_a_fall_while_engaged_warns_for_five_seconds(self):
    self.drive_big(enabled=True)
    # the tick of the fall is the first of them
    self.assertEqual(self.step(state='retrying', enabled=True), {'bigModelLinkLost'})
    self.assertEqual(1 + self.ticks_with('bigModelLinkLost', state='retrying', enabled=True), HANDBACK_TICKS)
    self.assertEqual(HANDBACK_TICKS, 500)

  def test_a_mads_only_fall_warns(self):
    # alpha longitudinal braked off, MADS steering on the big model: the link falls
    self.drive_big(mads=True)
    self.assertEqual(self.step(state='retrying', mads=True), {'bigModelLinkLost'})
    self.assertEqual(1 + self.ticks_with('bigModelLinkLost', state='retrying', mads=True), HANDBACK_TICKS)

  def test_a_disengage_ends_it_and_a_reengage_does_not_bring_it_back(self):
    self.drive_big(enabled=True)
    for _ in range(100):
      self.assertEqual(self.step(state='retrying', enabled=True), {'bigModelLinkLost'})
    self.assertEqual(self.step(state='retrying'), set())
    self.assertEqual(self.step(state='retrying', enabled=True), set())

  def test_a_fall_while_disengaged_is_silent(self):
    self.drive_big()
    self.assertEqual(self.step(state='retrying'), set())
    # the fall was consumed: engaging afterwards does not replay it
    self.assertEqual(self.step(state='retrying', enabled=True), set())

  def test_a_chestnut_fall_is_not_jetlinks(self):
    # comma's board has the native bigModelFailed for this, and nothing here adds to it
    self.step(state='none', big=True, enabled=True)
    self.assertEqual(self.step(state='none', enabled=True), set())

  def test_it_never_raises_big_model_failed(self):
    # upstream's text for it says to restart the car
    self.drive_big(enabled=True, mads=True)
    for state in ('retrying', 'unavailable', 'joining', 'ready', 'none'):
      self.assertFalse('bigModelFailed' in self.step(state=state, enabled=True, mads=True))
      self.assertFalse('bigModelFailed' in self.step(state=state, alive=False, enabled=True, mads=True))


class RealStateMachines(SwapEventsTest):
  """selfdrived's own machinery: the main state machine, MADS's (sunnypilot's, unchanged), and
  the alert manager, in the order SelfdriveD.step runs them."""

  def machines(self, enabled=False, mads=False):
    main = StateMachine()
    main.state = State.enabled if enabled else State.disabled
    selfdrive = SimpleNamespace(state_machine=main, events=self.events, events_sp=self.events_sp, enabled=enabled)
    madsm = MadsStateMachine(SimpleNamespace(selfdrive=selfdrive))
    madsm.state = MadsState.enabled if mads else MadsState.disabled
    return main, selfdrive, madsm


class TestHandBackOnTheRealStateMachines(RealStateMachines):
  def drive(self, ticks, enabled=True, mads=False, disengage_at=None):
    """Engaged on the big model, then the link falls; the main and MADS states at the end, and
    the alert on screen at every tick from the fall."""
    main, selfdrive, madsm = self.machines(enabled, mads)
    am = AlertManager()
    self.drive_big(enabled=enabled, mads=mads)
    shown = []
    for frame in range(ticks):
      if frame == disengage_at:
        main.state, madsm.state = State.disabled, MadsState.disabled
        selfdrive.enabled = enabled = mads = False
      self.step(state='retrying', enabled=enabled, mads=mads)
      enabled, _ = main.update(self.events)
      selfdrive.enabled = enabled
      mads, _ = madsm.update()
      clear = set() if ET.WARNING in main.current_alert_types else {ET.WARNING}
      am.add_many(frame, self.events.create_alerts(main.current_alert_types, []) +
                  self.events_sp.create_alerts(main.current_alert_types, []))
      am.process_alerts(frame, clear)
      shown.append(am.current_alert.alert_text_1)
    return main.state, madsm.state, shown

  def test_engaged_it_stays_engaged_and_warns_for_five_seconds(self):
    main, madsm, shown = self.drive(HANDBACK_TICKS + 100, mads=True)
    self.assertEqual((main, madsm), (State.enabled, MadsState.enabled))
    self.assertEqual(shown[:HANDBACK_TICKS], ["Big Model Lost"] * HANDBACK_TICKS)
    self.assertEqual(set(shown[HANDBACK_TICKS + 1:]), {""})

  def test_mads_only_it_keeps_steering_and_warns(self):
    # longitudinal braked off, MADS steering: MADS stays enabled and the driver is told
    main, madsm, shown = self.drive(HANDBACK_TICKS + 100, enabled=False, mads=True)
    self.assertEqual((main, madsm), (State.disabled, MadsState.enabled))
    self.assertEqual(shown[:HANDBACK_TICKS], ["Big Model Lost"] * HANDBACK_TICKS)
    self.assertEqual(set(shown[HANDBACK_TICKS + 1:]), {""})

  def test_a_disengage_clears_it_at_once(self):
    _, _, shown = self.drive(300, mads=True, disengage_at=100)
    self.assertEqual(set(shown[:100]), {"Big Model Lost"})
    self.assertEqual(set(shown[100:]), {""})


class TestSwitching(RealStateMachines):
  """For a second after a swap nothing engages: the large model starts from an empty history.
  Upstream's bigModelLoading no-entry, which both state machines read."""

  def test_a_second_of_no_entry_after_a_swap_then_the_chime(self):
    self.step(state='ready')
    self.assertEqual(self.step(state='running', big=True), {'bigModelLoading'})
    self.assertEqual(1 + self.ticks_with('bigModelLoading', state='running', big=True), SWITCHING_TICKS)
    self.assertEqual(SWITCHING_TICKS, 100)
    self.assertTrue(ET.NO_ENTRY in EVENTS[EventName.bigModelLoading])

  def test_the_chime_waits_for_the_end_of_the_second(self):
    # selfdrived's native block raises bigModelReady on a chestnut's load; for a swap it now
    # says "engage now", at the end of the second
    self.step(state='ready')
    heard = []
    for tick in range(SWITCHING_TICKS + 10):
      self.events.clear()
      self.events_sp.clear()
      if tick == 0:
        self.events_sp.add(EventNameSP.bigModelReady)
      self.sm['modelDataV2SP'].jetlink.state = 'running'
      self.sm['modelV2'].big = True
      self.swaps.update(self.sm, False, self.events, self.events_sp)
      heard.append(EventNameSP.bigModelReady in self.events_sp.names)
    self.assertEqual([i for i, h in enumerate(heard) if h], [SWITCHING_TICKS])

  def test_a_fall_inside_the_second_ends_it_without_the_chime(self):
    self.step(state='ready')
    for _ in range(10):
      self.step(state='running', big=True)
    self.assertEqual(self.step(state='retrying'), set())
    for _ in range(SWITCHING_TICKS):
      self.assertEqual(self.step(state='retrying'), set())

  def drive_presses(self, press_at: int, ticks: int = SWITCHING_TICKS + 40):
    """A swap at tick 0, then one press of cruise and one of MADS at `press_at`, as a car sends
    them: an edge, never repeated. Returns (main enabled, MADS state) at every tick."""
    main, selfdrive, madsm = self.machines()
    self.step(state='ready')
    engaged = []
    for tick in range(ticks):
      self.step(state='running', big=True, enabled=selfdrive.enabled, mads=madsm.state != MadsState.disabled)
      if tick == press_at:
        self.events.add(EventName.buttonEnable)
        self.events_sp.add(EventNameSP.lkasEnable)
      enabled, _ = main.update(self.events)
      selfdrive.enabled = enabled
      madsm.update()
      engaged.append((enabled, madsm.state))
    return engaged

  def test_a_press_inside_the_second_is_refused_and_mads_stays_off(self):
    # sunnypilot's MADS, unchanged: a no-entry refuses its enable, and it does not wait in
    # paused. The driver presses again after "Big Model Active"
    engaged = self.drive_presses(press_at=27)
    self.assertEqual(engaged[27], (False, MadsState.disabled))
    self.assertEqual(set(engaged[27:]), {(False, MadsState.disabled)})

  def test_a_press_after_the_second_engages_both(self):
    engaged = self.drive_presses(press_at=SWITCHING_TICKS + 5)
    self.assertEqual(engaged[SWITCHING_TICKS + 4], (False, MadsState.disabled))
    self.assertEqual(engaged[SWITCHING_TICKS + 5], (True, MadsState.enabled))

  def test_a_chestnut_never_switches_like_this(self):
    self.step(state='none')
    self.assertEqual(self.step(state='none', big=True), set())


class TestOffer(SwapEventsTest):
  """The offer to switch. The large model swaps in only while nothing is in control (openpilot
  or MADS in any state but disabled). Ready while something is, the driver is told once to
  disengage fully; ready while nothing is, it swaps at once and bigModelReady says so."""

  def offered(self, available=False, big=False, enabled=False, mads=False) -> bool:
    state = 'ready' if available else ('running' if big else 'none')
    return 'bigModelAvailable' in self.step(state, big=big, alive=self.sm.alive['modelV2'], enabled=enabled, mads=mads)

  def test_ready_while_engaged_offers_once_for_three_seconds(self):
    self.assertFalse(self.offered(enabled=True))
    self.assertEqual(self.ticks_with('bigModelAvailable', state='ready', enabled=True), OFFER_TICKS)
    for _ in range(1000):
      self.assertFalse(self.offered(available=True, enabled=True))

  def test_mads_alone_is_in_control(self):
    # MADS steering with longitudinal braked off: still in control, so the offer, not a swap
    self.assertTrue(self.offered(available=True, mads=True))

  def test_ready_while_nothing_is_in_control_never_offers(self):
    for _ in range(100):
      self.assertFalse(self.offered(available=True))
    self.assertFalse(self.offered(big=True))

  def test_engaging_while_it_still_waits_offers(self):
    self.assertFalse(self.offered(available=True))
    self.assertTrue(self.offered(available=True, enabled=True))

  def test_no_repeat_at_every_stop(self):
    self.assertTrue(self.offered(available=True, mads=True))
    for _ in range(2 * OFFER_TICKS):
      self.offered(available=True, mads=True)
    for _ in range(3):
      self.assertFalse(self.offered(available=True, mads=True))

  def test_the_swap_ends_it_at_once(self):
    self.assertTrue(self.offered(available=True, mads=True))
    self.assertTrue(self.offered(available=True, mads=True))
    # the driver turns MADS and longitudinal off and it swaps
    self.assertFalse(self.offered(big=True))
    for _ in range(OFFER_TICKS):
      self.assertFalse(self.offered(big=True))

  def test_on_screen_it_never_outlives_the_swap(self):
    am = AlertManager()
    shown = []
    for frame in range(4 * OFFER_TICKS):
      big = frame >= 50
      self.offered(available=not big, big=big, enabled=True)
      am.add_many(frame, self.events_sp.create_alerts([ET.PERMANENT], [None, None, self.sm, False, 0, None]))
      am.process_alerts(frame, set())
      shown.append((am.current_alert.alert_text_1, am.current_alert.alert_text_2))
    offer = ("Big Model Ready", "Disengage fully to switch")
    self.assertTrue(offer in shown[:50])
    self.assertFalse(offer in shown[int(0.2 / 0.01) + 50:])

  def test_it_rearms_after_the_link_goes_and_comes_back(self):
    self.assertTrue(self.offered(available=True, enabled=True))
    self.assertFalse(self.offered(big=True))   # swapped at a window, then...
    self.assertFalse(self.offered())           # lost, waiting to reconnect
    self.assertTrue(self.offered(available=True, enabled=True))

  def test_after_a_loss_while_engaged_it_waits_for_the_warning(self):
    self.offered(big=True, enabled=True)
    self.assertFalse(self.offered(enabled=True))
    waited = 0
    while not self.offered(available=True, enabled=True):
      waited += 1
      self.assertLess(waited, 2 * HANDBACK_TICKS)
    self.assertEqual(waited, HANDBACK_TICKS - 1)
    self.assertEqual(self.ticks_with('bigModelAvailable', state='ready', enabled=True), OFFER_TICKS - 1)

  def test_chestnut_and_old_messages_do_not_announce_availability(self):
    self.assertEqual(custom.ModelDataV2SP.new_message().jetlink.state, 'none')
    self.assertFalse(self.offered(enabled=True))
    self.assertFalse(self.offered(big=True, enabled=True))
    self.assertFalse(self.offered(enabled=True))

  def test_missing_invalid_or_stale_messages_never_announce(self):
    for service in ('modelV2', 'modelDataV2SP'):
      for check in ('seen', 'alive', 'valid'):
        with self.subTest(service=service, check=check):
          checks = getattr(self.sm, check)
          checks[service] = False
          self.assertFalse(self.offered(available=True, enabled=True))
          checks[service] = True
    self.assertTrue(self.offered(available=True, enabled=True))

  def test_the_offer_and_the_chime_after_the_switch_read_differently(self):
    offer = EVENTS_JETLINK[EventNameJetlink.bigModelAvailable][ET.PERMANENT]
    chime = EVENTS_SP[EventNameSP.bigModelReady][ET.PERMANENT]
    assert not isinstance(chime, Alert), "bigModelReady's text follows Jetlink's state"
    self.sm['modelDataV2SP'].jetlink.state = 'running'
    self.assertEqual(chime(None, None, self.sm, False, 0, None).alert_text_1, 'Big Model Active')
    self.assertNotEqual(offer.alert_text_1, 'Big Model Active')
    # a chestnut's is still "Big Model Ready"
    self.sm['modelDataV2SP'].jetlink.state = 'none'
    self.assertEqual(chime(None, None, self.sm, False, 0, None).alert_text_1, 'Big Model Ready')


class TestOptionalProcesses(OpenpilotTestCase):
  def test_a_dead_link_owner_never_blocks_engagement(self):
    # selfdrived's processNotRunning is NO_ENTRY: the owner has to be one selfdrived ignores, or
    # losing it costs the drive instead of the big model
    from openpilot.sunnypilot import jetlink_adapter
    from openpilot.system.manager.process_config import managed_processes
    self.assertEqual(JetlinkSwapEvents.OPTIONAL_PROCESSES, {jetlink_adapter.OWNER})
    self.assertTrue('jetlinkd' in managed_processes)
