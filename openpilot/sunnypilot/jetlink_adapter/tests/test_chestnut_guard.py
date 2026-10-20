"""
A computer on the USB-C port for Jetlink is not a chestnut.

Both modelds decide CHESTNUT from chestnut_present() or cable_connected(): the
cable is seen before a chestnut enumerates. A Mac, Jetson or iPhone on the port
for Jetlink reads the same on the CC pin, and the big-model pick is Jetlink's
too, so without a guard modeld would load the big model for a chestnut that is
not there, fail, and request an onroad cycle to retry (ChestnutRetry). The
cable counts only while the Jetlink setting is off.

Each modeld's real main() runs up to the line that publishes the decision
(ChestnutLoading) and is stopped there.
"""
import sys
from types import SimpleNamespace
from unittest import mock

from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.jetlink_adapter import KEYS


class Decided(Exception):
  def __init__(self, chestnut: bool):
    super().__init__(chestnut)
    self.chestnut = chestnut


class StopAtTheDecision:
  """The modeld module's Params: stops main() where it publishes CHESTNUT."""

  def put_bool(self, key, value, *args, **kwargs):
    if key == "ChestnutLoading":
      raise Decided(bool(value))

  def remove(self, key):
    pass


class ChestnutGuard:
  MODULE: str

  def patches(self, *, present: bool, cable: bool, big_pick: bool) -> list:
    raise NotImplementedError

  def decide(self, *, present: bool, cable: bool, link: int, big_pick: bool = True) -> bool:
    import importlib
    modeld = importlib.import_module(self.MODULE)
    Params().put(KEYS.link, link, block=True)
    patches = [mock.patch.object(modeld, 'Params', StopAtTheDecision), mock.patch.object(modeld, 'cloudlog'),
               mock.patch.object(modeld, 'chestnut_present', return_value=present),
               mock.patch.object(modeld, 'cable_connected', return_value=cable),
               mock.patch.dict(sys.modules, {'tinygrad.runtime.ops_amd': SimpleNamespace(AMDDevice=SimpleNamespace())}),
               *self.patches(present=present, cable=cable, big_pick=big_pick)]
    for patch in patches:
      patch.start()
    try:
      modeld.main()
    except Decided as d:
      return d.chestnut
    finally:
      for patch in reversed(patches):
        patch.stop()
    raise AssertionError("main() never published ChestnutLoading")

  def test_a_jetlink_host_on_the_port_is_not_a_chestnut(self):
    for link in (1, 2):   # USB (Mac, Jetson, PC, Android), iOS
      self.assertFalse(self.decide(present=False, cable=True, link=link), link)

  def test_with_the_link_off_a_cable_is_still_a_chestnut_coming_up(self):
    # upstream's rule, unchanged for a device without Jetlink: the cable is seen first
    self.assertTrue(self.decide(present=False, cable=True, link=0))

  def test_a_chestnut_that_enumerated_is_one_whatever_the_link_says(self):
    for link in (0, 1, 2):
      self.assertTrue(self.decide(present=True, cable=True, link=link), link)

  def test_nothing_on_the_port_is_no_chestnut(self):
    for link in (0, 1):
      self.assertFalse(self.decide(present=False, cable=False, link=link), link)

  def test_unset_link_is_off(self):
    Params().remove(KEYS.link)
    import importlib
    modeld = importlib.import_module(self.MODULE)
    with mock.patch.object(modeld.jetlink_adapter, 'link_on', wraps=modeld.jetlink_adapter.link_on) as link_on:
      self.assertTrue(self.decide(present=False, cable=True, link=0))
    link_on.assert_called()


class TestStockModeld(ChestnutGuard, OpenpilotTestCase):
  MODULE = 'openpilot.selfdrive.modeld.modeld'

  def patches(self, *, present, cable, big_pick):
    import openpilot.selfdrive.modeld.modeld as modeld
    # the Default big model compiled into the tree (chestnut_compiled), which the pick stands in for here
    return [mock.patch.object(modeld, 'chestnut_compiled', return_value=big_pick)]


class TestModeldTinygrad(ChestnutGuard, OpenpilotTestCase):
  MODULE = 'openpilot.sunnypilot.modeld_v2.modeld'

  def patches(self, *, present, cable, big_pick):
    import openpilot.sunnypilot.modeld_v2.modeld as modeld
    # the big-model slot's pick, which with Jetlink is Jetlink's model, has its driving pkl
    return [mock.patch.object(modeld, 'get_active_bundle', return_value=object()),
            mock.patch.object(modeld, '_find_driving_pkl', return_value='driving.pkl' if big_pick else None),
            mock.patch.object(modeld, 'setproctitle')]

  def test_without_a_big_pick_there_is_no_chestnut_load(self):
    self.assertFalse(self.decide(present=False, cable=True, link=0, big_pick=False))
