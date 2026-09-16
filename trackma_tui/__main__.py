# This file is part of trackma-tui.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys

# Query the terminal before importing the app: importing it pulls in
# textual-image, which runs a terminal query of its own at import time, and two
# queries racing each other is how the answer goes missing.
from trackma_tui import terminal

_background = terminal.background_color()

from trackma_tui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(_background))
