"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's accelerator UI tests. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

ui_state's Jetlink view beside upstream's chestnut state, the panels' helpers, and the comma 4
(mici) panels built for real in a hidden window of their own process. jetlink is one mock: the
adapter's status(), which the params pass reads.
"""
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.jetlink_adapter import KEYS

ROOT = Path(__file__).resolve().parents[5]


def jetlink_status(**fields):
  """jetlink's snapshot as the UI's params pass takes it, nothing to show unless a field says so.
  A link turned on reads as USB unless the mode is given."""
  from jetlink.openpilot import Status
  base = {'enabled': False, 'mode': 'usb' if fields.get('enabled') else 'off', 'transport': 'USB', 'present': False,
          'port': None, 'ready': False, 'reason': None, 'progress': None, 'model': None, 'default_model': None}
  return Status(**{**base, **fields})


class FakeSM:
  """What the Jetlink view and its per-frame update read off the UI's SubMaster."""

  def __init__(self, board=False, big=False, alive=False, recv=0, state='none'):
    self.board = board
    self.recv_frame = {"modelV2": recv}
    self.alive = {"modelV2": alive}
    self.big = big
    self.state = state

  def __getitem__(self, name):
    return {
      "deviceState": SimpleNamespace(chestnutPresent=self.board),
      "modelDataV2SP": SimpleNamespace(jetlink=SimpleNamespace(state=self.state)),
      "modelV2": SimpleNamespace(big=self.big),
    }[name]


class UITest(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    from openpilot.selfdrive.ui.ui_state import ui_state
    # the singleton, its SubMaster swapped for FakeSM on purpose
    self.ui_state: Any = ui_state
    self.params = Params()
    ui_state.params = self.params
    saved = {k: getattr(ui_state, k) for k in ('sm', 'started', 'started_frame', 'jetlink', 'chestnut_present', '_jetlink_state_name',
                                               'usb_connected', 'usb_connected_ts', 'usb_unknown', 'chestnut_compiled', 'adb_blocked')}
    self.addCleanup(lambda: [setattr(ui_state, k, v) for k, v in saved.items()])

  @contextmanager
  def jetlink(self, installed=True, **fields):
    """The adapter's status() while the block runs, and a params pass over it."""
    status = jetlink_status(**fields) if installed else None
    with mock.patch("openpilot.sunnypilot.jetlink_adapter.status", return_value=status) as reads:
      self.ui_state.update_params()
      yield reads

  def state(self, view, state='none', sm=None, started=False):
    """chestnut_state for Jetlink's snapshot `view` and modeld's Jetlink state by name."""
    ui = self.ui_state
    ui.sm, ui.started, ui.started_frame = sm or FakeSM(), started, 0
    ui.jetlink, ui._jetlink_state_name = view, state
    ui._update_chestnut_state()
    return ui.chestnut_state


class TestTheView(UITest):
  def test_a_fitted_chestnut_never_asks_jetlink(self):
    from openpilot.selfdrive.ui.ui_state import ChestnutState
    ui = self.ui_state
    ui.sm, ui.started, ui.chestnut_present = FakeSM(board=True), False, True
    with mock.patch("openpilot.sunnypilot.jetlink_adapter.status", side_effect=AssertionError("consulted")):
      ui.update_params()
      self.assertIsNone(ui.jetlink)
      self.assertIsNone(ui.jetlink_view)
      ui.chestnut_compiled = True
      ui._update_chestnut_state()
    self.assertEqual(ui.chestnut_state, ChestnutState.READY)

  def test_no_jetlink_or_nothing_to_show_is_upstreams_icon(self):
    from openpilot.selfdrive.ui.ui_state import ChestnutState
    ui = self.ui_state
    ui.sm, ui.started, ui.chestnut_present = FakeSM(), False, False
    for installed in (False, True):
      with self.jetlink(installed=installed):
        self.assertIsNone(ui.jetlink_view)
        ui._update_chestnut_state()
      self.assertEqual(ui.chestnut_state, ChestnutState.DISCONNECTED)

  def test_offroad_states(self):
    from openpilot.selfdrive.ui.ui_state import ChestnutState
    view = lambda **kw: jetlink_status(enabled=True, **{'present': True, **kw})  # noqa: E731
    self.assertEqual(self.state(view(present=False)), ChestnutState.DISCONNECTED)
    self.assertEqual(self.state(view(ready=True)), ChestnutState.READY)
    self.assertEqual(self.state(view()), ChestnutState.UNCOMPILED)
    self.assertEqual(self.state(view(progress={'stage': 'build', 'frac': 0.3})), ChestnutState.LOADING)
    self.assertEqual(self.state(view(progress={'stage': 'failed', 'frac': 1.0})), ChestnutState.FAILED)

  def test_onroad_states(self):
    from openpilot.selfdrive.ui.ui_state import ChestnutState
    driving = FakeSM(alive=True, recv=1)
    view = jetlink_status(enabled=True, present=True, ready=True)
    for state, expected in (('joining', ChestnutState.LOADING), ('retrying', ChestnutState.LOADING),
                            # loaded, waiting for nothing to be in control: on a MADS drive, the rest of it
                            ('ready', ChestnutState.WAITING),
                            ('running', ChestnutState.ACTIVE), ('unavailable', ChestnutState.FAILED)):
      with self.subTest(state=state):
        self.assertEqual(self.state(view, state, driving, started=True), expected)
    # a big frame is proof, whatever the state field says
    self.assertEqual(self.state(view, 'unavailable', FakeSM(big=True, alive=True, recv=1), started=True), ChestnutState.ACTIVE)

  def test_the_state_name_is_read_where_sm_updates(self):
    from openpilot.selfdrive.ui.sunnypilot.ui_state_jetlink import UIStateJetlink
    self.ui_state.sm = FakeSM(state='joining')
    UIStateJetlink.update(self.ui_state)
    self.assertEqual(self.ui_state._jetlink_state_name, 'joining')

  def test_the_ui_subscribes_to_jetlinks_state(self):
    self.assertTrue("modelDataV2SP" in self.ui_state.sm_services_ext)


class TestTheUsbPort(UITest):
  @contextmanager
  def usb(self, present):
    from openpilot.selfdrive.ui import ui_state as module
    with mock.patch.object(module, 'cable_connected', return_value=True), mock.patch.object(module, 'get_usb_state', return_value=[]), \
         mock.patch("openpilot.sunnypilot.jetlink_adapter.status", return_value=jetlink_status(present=present)):
      yield

  def test_a_present_host_is_not_an_unknown_usb_device(self):
    ui = self.ui_state
    ui.sm = FakeSM()
    ui.usb_connected, ui.usb_connected_ts, ui.usb_unknown = True, time.monotonic() - 11.0, False
    with self.usb(present=True):
      ui.update_params()   # builds the view
      ui.usb_connected_ts = time.monotonic() - 11.0
      ui.update_params()   # decides
    self.assertIs(ui.usb_unknown, False)
    with self.usb(present=False):
      ui.update_params()
      ui.usb_connected_ts = time.monotonic() - 11.0
      ui.update_params()
    self.assertIs(ui.usb_unknown, True)

  def test_a_host_recognized_late_clears_unknown(self):
    ui = self.ui_state
    ui.sm = FakeSM()
    ui.usb_connected, ui.usb_connected_ts, ui.usb_unknown = True, None, True
    with self.usb(present=False):
      ui.update_params()
      self.assertIs(ui.usb_unknown, True)
    with self.usb(present=True):
      ui.update_params()
      self.assertIs(ui.usb_unknown, False)

  def test_the_link_on_turns_adb_off_and_blocks_it(self):
    self.ui_state.sm = FakeSM()
    self.params.put_bool("AdbEnabled", True, block=True)
    with self.jetlink(enabled=True):
      pass
    self.assertTrue(self.ui_state.adb_blocked)
    self.assertFalse(self.params.get_bool("AdbEnabled"))

  def test_the_link_off_or_no_jetlink_leaves_adb_alone(self):
    self.ui_state.sm = FakeSM()
    for installed in (True, False):
      self.params.put_bool("AdbEnabled", True, block=True)
      with self.jetlink(installed=installed, enabled=False):
        pass
      self.assertFalse(self.ui_state.adb_blocked)
      self.assertTrue(self.params.get_bool("AdbEnabled"))


class TestTheChestnutGuard(UITest):
  """The UI decides a chestnut at the onroad edge from the cable too: a Jetlink host on the port
  is no chestnut, or the panel would hide the link toggle and show the chestnut's icons."""

  def test_a_jetlink_host_is_no_chestnut_and_with_the_link_off_a_cable_is_one(self):
    import inspect
    from openpilot.selfdrive.ui.ui_state import UIState
    src = inspect.getsource(UIState)
    self.assertTrue("(self.chestnut_compiled and self.usb_connected and not jetlink_adapter.link_on())" in src)
    # the guard reads the setting as a file
    from openpilot.sunnypilot import jetlink_adapter
    self.params.put(KEYS.link, 1, block=True)
    self.assertTrue(jetlink_adapter.link_on())
    self.params.put(KEYS.link, 0, block=True)
    self.assertFalse(jetlink_adapter.link_on())


class TestPanelHelpers(UITest):
  def test_link_mode_reads_the_index_and_an_unknown_one_is_off(self):
    from openpilot.selfdrive.ui.sunnypilot.jetlink_panel import link_mode
    for i, mode in enumerate(('off', 'usb', 'ios')):
      self.params.put(KEYS.link, i, block=True)
      self.assertEqual(link_mode(), mode)
    self.params.put(KEYS.link, 7, block=True)
    self.assertEqual(link_mode(), 'off')

  def test_the_toggle_is_offered_wherever_jetlink_is_and_never_beside_a_chestnut(self):
    from openpilot.selfdrive.ui.sunnypilot.jetlink_panel import link_toggle_meaningful
    ui = self.ui_state
    ui.sm, ui.chestnut_present = FakeSM(), False
    with self.jetlink(installed=False):
      self.assertFalse(link_toggle_meaningful())
    with self.jetlink():
      self.assertTrue(link_toggle_meaningful())
    ui.chestnut_present = True
    self.assertFalse(link_toggle_meaningful())

  def test_the_status_line_says_what_is_on_the_port(self):
    from openpilot.selfdrive.ui.sunnypilot.jetlink_panel import link_status
    self.ui_state.sm = FakeSM()
    for fields, line in (({'present': True}, "Jetlink connected: USB."), ({'port': 'empty'}, "Nothing on the USB port."),
                         ({'port': 'host'}, "A device is on the USB port."), ({}, "")):
      with self.jetlink(enabled=True, **fields):
        self.assertEqual(link_status(), line, fields)

  def test_progress_names_the_cable_once_it_drops_enough(self):
    from openpilot.selfdrive.ui.sunnypilot.jetlink_panel import big_model_progress
    self.ui_state.sm = FakeSM()
    with self.jetlink(enabled=True, progress={'stage': 'connect', 'frac': 0.0, 'msg': 'waiting for jetlink', 'drops': 3}):
      self.assertEqual(big_model_progress(), ('connect', 0.0, 'waiting for jetlink, check cable (3 drops)'))
    with self.jetlink(enabled=True, progress={'stage': 'ready', 'frac': 1.0, 'msg': ''}):
      self.assertIsNone(big_model_progress())


class TestModelInfo(UITest):
  def test_an_empty_big_slot_names_jetlinks_default_without_a_chestnut(self):
    from openpilot.selfdrive.ui.sunnypilot.model_info import default_model
    from openpilot.sunnypilot.models.model_name import DEFAULT_BIG_MODEL, DEFAULT_MODEL
    ui = self.ui_state
    ui.sm, ui.chestnut_present = FakeSM(), False
    with self.jetlink(enabled=True, default_model='Cinque Terre V3 Model'):
      self.assertEqual(default_model('chestnut'), 'Cinque Terre V3 Model')
      self.assertEqual(default_model('qcom'), DEFAULT_MODEL)
    ui.chestnut_present = True
    self.assertEqual(default_model('chestnut'), DEFAULT_BIG_MODEL)

  def test_waiting_reads_as_ready(self):
    from openpilot.selfdrive.ui.sunnypilot.model_info import big_model_state
    from openpilot.selfdrive.ui.ui_state import ChestnutState
    self.ui_state.chestnut_state = ChestnutState.WAITING
    self.assertEqual(big_model_state(), 'ready')

  def test_what_drives_is_jetlinks_model_while_it_is_active(self):
    from openpilot.selfdrive.ui.sunnypilot.model_info import carrying_model
    from openpilot.selfdrive.ui.ui_state import ChestnutState
    ui = self.ui_state
    ui.sm, ui.chestnut_present = FakeSM(), False
    with self.jetlink(enabled=True, present=True, ready=True, model='Cinque Terre V3 Model'):
      ui.chestnut_state = ChestnutState.ACTIVE
      self.assertEqual(carrying_model(), ('jetlink', 'Cinque Terre V3 Model', 'Cinque Terre V3 Model'))


# The comma 4's panels, built and drawn for real: a raylib window of their own, in a process of
# their own, so a crash of the window is this test's failure and not the runner's
MICI_PANELS = '''
import json, sys
from unittest import mock
import pyray as rl
from jetlink.openpilot import Status
from openpilot.common.params import Params
from openpilot.sunnypilot.jetlink_adapter import KEYS, MODES

rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
from openpilot.system.ui.lib.application import gui_app
gui_app.init_window("jetlink-ui-test")

status = Status(enabled=True, mode='usb', transport='USB', present=True, port='host', ready=False, reason=None,
                progress={'stage': 'build', 'frac': 0.4, 'msg': 'preparing'}, model='Cinque Terre V3 Model', default_model=None)
found = {}
with mock.patch('openpilot.sunnypilot.jetlink_adapter.status', return_value=status):
  from openpilot.selfdrive.ui.ui_state import ui_state
  params = Params()
  params.put(KEYS.link, MODES.index('ios'), block=True)
  params.put_bool("AdbEnabled", True, block=True)
  ui_state.params = params
  ui_state.update_params()

  from openpilot.selfdrive.ui.sunnypilot.mici.layouts.models import ModelsLayoutMici
  panel = ModelsLayoutMici()
  for _ in range(3):
    panel._cache_size_time = 0.0
    panel.render(rl.Rectangle(0, 0, 536, 240))
  found['in_items'] = panel.link_toggle in panel.main_items
  found['visible'] = bool(panel.link_toggle.is_visible)
  found['value'] = panel.link_toggle.value
  found['info'] = panel.current_model_info.info_text.text
  params.put(KEYS.link, 9, block=True)   # a value this build does not know
  panel._cache_size_time = 0.0
  panel.render(rl.Rectangle(0, 0, 536, 240))
  found['unknown_value'] = panel.link_toggle.value

  from openpilot.selfdrive.ui.sunnypilot.mici.layouts.developer import DeveloperLayoutMiciSP
  dev = DeveloperLayoutMiciSP()
  found['adb_blocked'] = ui_state.adb_blocked
  found['adb_enabled_param'] = params.get_bool("AdbEnabled")
  found['adb_toggle_enabled'] = bool(dev._adb_toggle.enabled)

  from openpilot.selfdrive.ui.sunnypilot.mici.layouts.settings import SettingsLayoutSP
  settings = SettingsLayoutSP()
  pushed = []
  with mock.patch.object(gui_app, 'push_widget', side_effect=pushed.append):
    next(i for i in settings._scroller._items if getattr(i, 'text', None) == 'developer')._click_callback()
  found['developer_panel'] = type(pushed[0]).__name__ if pushed else None
gui_app.close()
print(json.dumps(found))
'''


class TestTheComma4Panels(OpenpilotTestCase):
  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    env = {**os.environ, 'BIG': '0', 'PYTHONPATH': os.pathsep.join([str(ROOT), *sys.path])}
    out = subprocess.run([sys.executable, '-c', MICI_PANELS], capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300)
    assert out.returncode == 0, out.stderr[-3000:]
    cls.found = json.loads(out.stdout.strip().splitlines()[-1])

  def test_the_models_panel_offers_the_setting(self):
    self.assertTrue(self.found['in_items'])
    self.assertTrue(self.found['visible'])
    self.assertEqual(self.found['value'], 'iOS')

  def test_a_setting_this_build_does_not_know_shows_off(self):
    self.assertEqual(self.found['unknown_value'], 'off')

  def test_the_panel_says_what_jetlink_is_doing(self):
    self.assertEqual(self.found['info'], 'preparing 40%')

  def test_adb_is_off_and_its_toggle_grayed_while_the_link_holds_the_port(self):
    self.assertTrue(self.found['adb_blocked'])
    self.assertFalse(self.found['adb_enabled_param'])
    self.assertFalse(self.found['adb_toggle_enabled'])

  def test_the_settings_developer_button_opens_the_jetlink_aware_panel(self):
    self.assertEqual(self.found['developer_panel'], 'DeveloperLayoutMiciSP')
