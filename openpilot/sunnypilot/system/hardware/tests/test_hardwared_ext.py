"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's hardwared hook tests. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import inspect
from unittest import mock

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot import jetlink_adapter
from openpilot.sunnypilot.system.hardware.hardwared_ext import JETLINK_OFF_WAIT_T, JetlinkHardwared


class JetlinkHardwaredTest(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.ext = JetlinkHardwared()
    self.clock = 100.0
    self.ext.now = lambda: self.clock
    self.pending = False
    self.asked = []
    patches = [mock.patch.object(jetlink_adapter, 'request_shutdown', side_effect=self.request),
               mock.patch.object(jetlink_adapter, 'shutdown_pending', side_effect=lambda: self.pending)]
    for patch in patches:
      patch.start()
      self.addCleanup(patch.stop)

  def request(self, reason=''):
    self.asked.append(reason)
    return self.pending


class TestShutdown(JetlinkHardwaredTest):
  def test_with_the_link_off_it_shuts_down_on_upstreams_loop(self):
    # request_shutdown has nothing to ask with the link off: nothing pending
    self.assertFalse(self.ext.shutdown_ready(False, None))
    self.assertFalse(self.ext.powering_off)
    self.assertTrue(self.ext.shutdown_ready(True, 5.0))
    self.assertEqual(len(self.asked), 1)

  def test_a_host_that_takes_the_request(self):
    self.pending = True
    self.assertFalse(self.ext.shutdown_ready(True, 5.0))
    self.assertTrue(self.ext.powering_off)
    self.clock += 10
    self.assertFalse(self.ext.shutdown_ready(False, 5.0), "once asked, the power monitor is not consulted again")
    self.pending = False
    self.assertTrue(self.ext.shutdown_ready(False, 5.0))
    self.assertEqual(len(self.asked), 1, "asked once")

  def test_a_host_that_never_answers_is_waited_for_25_s_at_most(self):
    self.pending = True
    self.assertFalse(self.ext.shutdown_ready(True, 5.0))
    self.clock += JETLINK_OFF_WAIT_T - 0.1
    self.assertFalse(self.ext.shutdown_ready(True, 5.0))
    self.clock += 0.1
    self.assertTrue(self.ext.shutdown_ready(True, 5.0))
    self.assertEqual(JETLINK_OFF_WAIT_T, 25.0)


class TestAlert(JetlinkHardwaredTest):
  def test_the_reason_is_the_offroad_alert(self):
    set_alert = mock.MagicMock()
    with mock.patch.object(jetlink_adapter, 'reason', return_value=None):
      self.ext.alerts(set_alert)
    set_alert.assert_called_with('Offroad_JetlinkUnavailable', False, extra_text=None)
    with mock.patch.object(jetlink_adapter, 'reason', return_value='no gadget'):
      self.ext.alerts(set_alert)
    set_alert.assert_called_with('Offroad_JetlinkUnavailable', True, extra_text='no gadget')


class TestHardwared(OpenpilotTestCase):
  def test_without_jetlink_the_shutdown_is_never_held(self):
    # no checkout, or one that fails: the hooks answer as the link off, nothing pending
    with mock.patch.object(jetlink_adapter, '_bound', None), mock.patch.dict('sys.modules', {'jetlink': None}):
      self.assertTrue(JetlinkHardwared().shutdown_ready(True, 5.0))

  def test_the_hooks_in_hardwared(self):
    from openpilot.system.hardware import hardwared
    src = inspect.getsource(hardwared.hardware_thread)
    self.assertTrue('startup_conditions["not_powering_off"] = not jetlink.powering_off' in src)
    self.assertTrue('jetlink.alerts(set_offroad_alert_if_changed)' in src)
    self.assertTrue('if jetlink.shutdown_ready(power_monitor.should_shutdown(' in src)
    self.assertEqual(src.count('params.put_bool("DoShutdown", True'), 1, "DoShutdown goes through the Jetlink wait alone")
