"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's jetlink adapter tests. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

manager and jetlinkd, the gadget owner: when it runs, and that a dead one is
started again.
"""
from unittest import mock

from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot import jetlink_adapter
from openpilot.sunnypilot.jetlink_adapter import KEYS


class FakeProc:
  """multiprocessing.Process as manager uses it, with no process behind it."""

  def __init__(self, name=None, target=None, args=()):
    self.name, self.args = name, args
    self.exitcode = None
    self.pid = 4242
    self.started = 0

  def start(self):
    self.started += 1

  def is_alive(self):
    return self.exitcode is None

  def join(self, timeout=None):
    pass


def owner():
  from openpilot.system.manager.process_config import managed_processes
  return managed_processes[jetlink_adapter.OWNER]


class TestWhenTheOwnerRuns(OpenpilotTestCase):
  def test_manager_runs_it_while_the_link_is_on_and_no_chestnut_is_fitted(self):
    proc = owner()
    self.assertEqual(proc.module, jetlink_adapter.__name__)
    params = Params()
    with mock.patch.object(jetlink_adapter, '_bound', None), \
         mock.patch('openpilot.selfdrive.modeld.helpers.chestnut_present', return_value=False):
      for mode in jetlink_adapter.MODES:
        params.put(KEYS.link, jetlink_adapter.MODES.index(mode), block=True)
        for started in (False, True):
          self.assertEqual(proc.should_run(started, params, None), mode != 'off', mode)
    with mock.patch.object(jetlink_adapter, '_bound', None), \
         mock.patch('openpilot.selfdrive.modeld.helpers.chestnut_present', return_value=True):
      self.assertFalse(proc.should_run(False, params, None))


class TestManagerStartsADeadOwnerAgain(OpenpilotTestCase):
  """jetlinkd holds the gadget for as long as the link is on, so manager starts
  it again when it dies, where a plain PythonProcess leaves one that exited
  alone for good. jetlink's owner adopts what the dead one left and backs off a
  crash loop itself, so manager only has to start it, once per loop; the
  backoff for one that dies young is RestartingPythonProcess's, tested in
  sunnypilot/system/manager/tests/test_processes.py."""

  def loops(self, proc, *, should_run=True):
    """manager's loop over this one process, with the Processes it makes, on
    a clock of its own (self.clock, seconds)."""
    from openpilot.system.manager import process
    made = []
    self.clock = 1000.0

    def make(**kwargs):
      made.append(FakeProc(**kwargs))
      return made[-1]
    patches = [mock.patch.object(process, 'Process', side_effect=make), mock.patch.object(proc, 'proc', None),
               mock.patch.object(proc, 'shutting_down', False),
               mock.patch.object(proc, 'should_run', lambda started, params, CP: should_run),
               mock.patch.object(proc, 'now', lambda: self.clock), mock.patch.object(proc, 'started_at', 0.0),
               mock.patch.object(proc, 'backoff', 0.0), mock.patch.object(proc, 'next_start', 0.0)]
    for patch in patches:
      patch.start()
      self.addCleanup(patch.stop)

    def loop(seconds: float = 0.5):
      # params and CarParams only reach should_run, which is patched above
      process.ensure_running({proc.name: proc}.values(), False, params=mock.MagicMock(), CP=mock.MagicMock())
      self.clock += seconds
    return made, loop

  def test_the_owner_is_started_again_once_a_loop_and_never_twice(self):
    proc = owner()
    made, loop = self.loops(proc)
    loop()
    self.assertEqual([p.started for p in made], [1])
    self.assertEqual(made[0].args, (jetlink_adapter.__name__, jetlink_adapter.OWNER))
    for _ in range(3):
      loop(60.0)   # alive: never a second one beside it
    self.assertEqual([p.started for p in made], [1])
    made[0].exitcode = 1   # it died, having run a while
    loop()
    self.assertEqual([p.started for p in made], [1, 1], 'the dead owner was not started again, or twice')
    self.assertIs(proc.proc, made[1])
    loop(60.0)
    self.assertEqual(len(made), 2)
    made[1].exitcode = -9   # killed, this time
    loop()
    loop()
    self.assertEqual([p.started for p in made], [1, 1, 1])

  def test_a_dead_owner_the_link_no_longer_wants_is_only_reaped(self):
    proc = owner()
    made, loop = self.loops(proc)
    loop()
    made[0].exitcode = 1
    proc.should_run = lambda started, params, CP: False
    loop()
    self.assertIsNone(proc.proc)
    self.assertEqual(len(made), 1)
