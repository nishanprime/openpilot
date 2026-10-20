"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.params import Params


class ModelStateBase:
  # modeld's share of dropped camera frames, written before every run(). A plain model ignores it;
  # Jetlink's joining model hands a lagging large model back on it
  frame_drop_ratio: float = 0.0

  def __init__(self):
    self.lat_delay = Params().get("LagdValueCache", return_default=True)
