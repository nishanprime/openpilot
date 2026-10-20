"""
Copyright (c) 2026-, Zeph Leggett.

Adapted from zoompilot's manager process tests. Licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace

from openpilot.common.test import OpenpilotTestCase
from openpilot.system.manager.process import PythonProcess
from openpilot.system.manager.process_config import always_run, managed_processes
from openpilot.sunnypilot import jetlink_adapter
from openpilot.sunnypilot.system.manager.processes import RestartingPythonProcess


class FakeRestarting(RestartingPythonProcess):
  t = 0.0
  starts = 0

  def now(self) -> float:
    return self.t

  def die(self):
    die(self.proc)


def die(proc):
  # a fake_start proc, untyped: the real one is a multiprocessing.Process
  proc.exitcode = 1


def fake_start(self):
  self.starts += 1
  self.proc = SimpleNamespace(exitcode=None, pid=1, is_alive=lambda: True)


def fake_stop(self, *args, **kwargs):
  self.proc = None


class TestRestartingProcess(OpenpilotTestCase):
  def setup_method(self):
    monkeypatch = self._fixture("monkeypatch")
    monkeypatch.setattr(PythonProcess, "start", fake_start)
    monkeypatch.setattr(PythonProcess, "stop", fake_stop)

  def test_backoff_doubles_then_resets_after_a_long_run(self):
    p = FakeRestarting("fake", "openpilot.fake", always_run)
    p.start()
    for expected in (p.BACKOFF, 2 * p.BACKOFF):
      p.t += 1.
      p.die()
      p.start()
      assert p.backoff == expected and p.proc is None
      p.t = p.next_start - 0.1
      p.start()
      assert p.proc is None
      p.t = p.next_start
      p.start()
      assert p.proc is not None
    p.t += p.QUICK_DEATH + 1.
    p.die()
    p.start()
    assert p.backoff == 0. and p.proc is not None

  def test_backoff_is_capped(self):
    p = FakeRestarting("fake", "openpilot.fake", always_run)
    p.start()
    for _ in range(8):
      p.die()
      p.start()
      p.t = p.next_start
      p.start()
    assert p.backoff == p.BACKOFF_MAX


class TestTheProcessList(OpenpilotTestCase):
  def test_jetlinkd_runs_the_adapter_beside_the_model_manager(self):
    names = list(managed_processes)
    owner = managed_processes[jetlink_adapter.OWNER]
    assert isinstance(owner, RestartingPythonProcess)
    assert owner.module == jetlink_adapter.__name__
    assert names.index(jetlink_adapter.OWNER) == names.index("models_manager") + 1

  def test_nothing_else_changed(self):
    # extend_procs adds jetlinkd and nothing more: every other process is upstream's, as it was
    others = [p for p in managed_processes.values() if p.name != jetlink_adapter.OWNER]
    assert not any(isinstance(p, RestartingPythonProcess) for p in others)
