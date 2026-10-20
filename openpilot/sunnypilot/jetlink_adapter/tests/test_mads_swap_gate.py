"""
The large model never swaps in while MADS steers.

The swap is jetlink's JoiningModelState._maybe_swap, which waits for the
adapter's in_control() to say nothing is in control: openpilot (carControl.enabled)
or MADS (carControlSP.mads.enabled, which controlsd copies from selfdrived's MADS).
A driver with MADS and alpha longitudinal who brakes cancels longitudinal while
MADS keeps steering: that must hold the swap off, as must MADS paused (a stop,
a blinker), overriding and soft disabling. Only both off opens the window.

These run the real pinned JoiningModelState with its own join thread, fed the
adapter's real in_control(); only the link and the two models are fakes.
"""
import threading
import time
from unittest import mock

from jetlink.openpilot import joining
from jetlink.openpilot.joining import JoiningModelState

from openpilot.cereal import custom, messaging
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.jetlink_adapter import IN_CONTROL, Adapter
from openpilot.sunnypilot.mads.state import ACTIVE_STATES, ENABLED_STATES

MadsState = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState
STEERING_STATES = {name: getattr(MadsState, name) for name in MadsState.schema.enumerants}


class FakeSmall:
  """openpilot's ModelState as the joining model drives it."""
  chestnut = False
  desire_key = 'desire_pulse'
  numpy_inputs = {}

  def __init__(self):
    self.lat_delay = self.frame_drop_ratio = 0.0
    self.in_control = True
    self.PLANPLUS_CONTROL = 0

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    return {'from': 'small'}


class FakeBig(FakeSmall):
  chestnut = True
  behind = None

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    return {'from': 'big'}


def mads_message(state) -> custom.CarControlSP:
  """carControlSP as controlsd publishes it from selfdrived's MADS: enabled and
  active by sunnypilot's own state sets (mads/state.py)."""
  cc_sp = messaging.new_message('carControlSP').carControlSP
  cc_sp.mads.state = state
  cc_sp.mads.enabled = state in ENABLED_STATES
  cc_sp.mads.active = state in ACTIVE_STATES
  cc_sp.mads.available = True
  return cc_sp


class Car:
  """The adapter's SubMaster, set by hand: what controlsd publishes."""

  def __init__(self, op: Adapter):
    op.in_control()   # makes the SubMaster
    assert op._sm is not None
    self.sm = op._sm
    self.sm.update = mock.MagicMock(return_value=None)
    for service in IN_CONTROL:
      self.sm.data[service] = getattr(messaging.new_message(service), service)
      self.sm.alive[service] = self.sm.valid[service] = True

  def set(self, *, openpilot: bool, mads):
    self.sm.data['carControl'].enabled = openpilot
    self.sm.data['carControlSP'] = mads_message(mads)


class TestInControlFollowsMads(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.op = Adapter()
    self.car = Car(self.op)

  def test_every_mads_state_but_disabled_is_in_control(self):
    enabled = {state for name, state in STEERING_STATES.items() if name != 'disabled'}
    self.assertEqual(enabled, set(ENABLED_STATES), "MADS's enabled set moved")
    for name, state in STEERING_STATES.items():
      for openpilot in (False, True):
        self.car.set(openpilot=openpilot, mads=state)
        self.assertEqual(self.op.in_control(), openpilot or name != 'disabled', (name, openpilot))

  def test_braking_out_of_longitudinal_with_mads_steering_is_in_control(self):
    # alpha longitudinal and MADS engaged, then the brake: longitudinal cancels,
    # MADS keeps steering (enabled) or pauses with the brake (paused)
    self.car.set(openpilot=True, mads=MadsState.enabled)
    self.assertTrue(self.op.in_control())
    for after_brake in (MadsState.enabled, MadsState.paused):
      self.car.set(openpilot=False, mads=after_brake)
      self.assertTrue(self.op.in_control(), str(after_brake))


class TestNoSwapWhileMadsSteers(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.op = Adapter()
    self.car = Car(self.op)
    self.client = mock.MagicMock(name='link')
    self.built = []

    def connect(stopped):
      return self.client, mock.sentinel.spec

    def build(client, spec):
      self.built.append(FakeBig())
      return self.built[-1]

    patches = [mock.patch.object(joining.gadget, 'host_attached', return_value=True),
               mock.patch.object(joining, 'KEEPALIVE_PERIOD', 3600.0)]
    for patch in patches:
      patch.start()
      self.addCleanup(patch.stop)
    self.small = FakeSmall()
    self.model = JoiningModelState(self.small, connect, build, progress=mock.MagicMock(name='progress'),
                                   log=mock.MagicMock(name='log'), in_control=self.op.in_control)
    self.addCleanup(self.model.close)

  def wait_until_ready(self):
    deadline = time.monotonic() + 10
    while self.model.big_model_state != 'ready':
      self.assertLess(time.monotonic(), deadline, f"the link never joined: {self.model.big_model_state}")
      time.sleep(0.01)

  def frames(self, n: int) -> list[str]:
    return [self.model.run({}, {}, {})['from'] for _ in range(n)]

  def test_the_join_lands_on_a_drive_with_mads_steering_and_waits(self):
    self.car.set(openpilot=False, mads=MadsState.enabled)
    self.frames(joining.SMALL_WARMUP_FRAMES + 1)
    self.wait_until_ready()
    self.assertEqual(self.frames(100), ['small'] * 100)
    self.assertEqual((self.built, self.model.handovers, self.model.big_model_state), ([], 0, 'ready'))

  def test_no_mads_state_but_disabled_lets_it_swap(self):
    self.car.set(openpilot=False, mads=MadsState.enabled)
    self.frames(joining.SMALL_WARMUP_FRAMES + 1)
    self.wait_until_ready()
    for name, state in STEERING_STATES.items():
      if name == 'disabled':
        continue
      for openpilot in (False, True):
        self.car.set(openpilot=openpilot, mads=state)
        self.assertEqual(self.frames(20), ['small'] * 20, (name, openpilot))
    self.assertEqual(self.built, [], 'the large model was built while MADS was steering')

  def test_brake_cancels_longitudinal_mads_keeps_steering_then_both_off_swaps(self):
    # the driver's day: MADS + alpha long engaged, the brake cancels long and
    # MADS steers on, then lateral off too, then the swap on the next frame
    self.car.set(openpilot=True, mads=MadsState.enabled)
    self.frames(joining.SMALL_WARMUP_FRAMES + 1)
    self.wait_until_ready()
    self.assertEqual(self.frames(20), ['small'] * 20)
    self.car.set(openpilot=False, mads=MadsState.enabled)   # braked: long off, MADS steering
    self.assertEqual(self.frames(20), ['small'] * 20)
    self.car.set(openpilot=False, mads=MadsState.paused)    # MADS paused on the brake
    self.assertEqual(self.frames(20), ['small'] * 20)
    self.assertEqual(self.built, [])
    self.car.set(openpilot=False, mads=MadsState.disabled)  # lateral off too: the window opens
    self.assertEqual(self.frames(1), ['big'])
    self.assertEqual((len(self.built), self.model.handovers, self.model.big_model_state), (1, 1, 'running'))

  def test_a_controls_message_gone_stale_holds_the_swap_off(self):
    self.car.set(openpilot=False, mads=MadsState.disabled)
    self.car.sm.alive['carControlSP'] = False   # controlsd stopped publishing MADS
    self.frames(joining.SMALL_WARMUP_FRAMES + 1)
    self.wait_until_ready()
    self.assertEqual(self.frames(20), ['small'] * 20)
    self.assertEqual(self.built, [])

  def test_the_swap_reads_in_control_on_the_frame_thread_before_every_frame(self):
    asked = []
    real = self.op.in_control
    self.model._ask_in_control = lambda: (asked.append(threading.current_thread()), real())[1]
    self.car.set(openpilot=False, mads=MadsState.enabled)
    self.frames(5)
    self.assertEqual(asked, [threading.current_thread()] * 5)
