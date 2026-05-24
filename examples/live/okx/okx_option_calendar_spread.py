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

"""Compatibility entry point for the fixed two-leg calendar-spread reference."""

import sys
from pathlib import Path


_OKX_EXAMPLE_DIR = Path(__file__).resolve().parent
if str(_OKX_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_OKX_EXAMPLE_DIR))

from calendar_spread_research.strategies.reference_fixed_calendar_spread import *
from calendar_spread_research.strategies.reference_fixed_calendar_spread import node


if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
