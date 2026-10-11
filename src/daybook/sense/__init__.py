"""Screen facts, privacy, and capture; pixels never reach persistence."""

from ._gate import gate as gate
from ._screen import ReplayScreen as ReplayScreen
from ._screen import X11Screen as X11Screen
from ._sense import Sense as Sense
from ._types import (
    PrivacySettings as PrivacySettings,
)
from ._types import (
    Rect as Rect,
)
from ._types import (
    Screen as Screen,
)
from ._types import (
    ScreenState as ScreenState,
)
from ._types import (
    VisibleWindow as VisibleWindow,
)
