# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
# ruff: noqa: E402,F403

"""Compatibility entry point for the Phase 0 calendar-spread strategy version."""

import sys
from pathlib import Path


_OKX_EXAMPLE_DIR = Path(__file__).resolve().parent
if str(_OKX_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_OKX_EXAMPLE_DIR))

from calendar_spread_research.strategies.phase0_v0_flow_validation import *
from calendar_spread_research.strategies.phase0_v0_flow_validation import main


if __name__ == "__main__":
    main()
