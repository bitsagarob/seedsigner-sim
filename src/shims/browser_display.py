"""
A SeedSigner display driver that draws to a browser canvas.

Mirrors the fork's desktop_display.py, but where that one pushes frames into a
pygame window this one hands raw RGB bytes to JavaScript. Everything above it,
the Renderer and every screen, is unmodified SeedSigner.
"""

from dataclasses import dataclass

from PIL import Image

from seedsigner.hardware.displays.display_driver import BaseDisplayDriver


@dataclass
class BrowserDisplay(BaseDisplayDriver):
    """
    `_width` and `_height` come from BaseDisplayDriver and describe the emulated
    panel. `sink` is a callback supplied by the worker that forwards a frame to
    the page.
    """

    sink: object = None

    def __post_init__(self):
        self.buffer = Image.new("RGB", (self.width, self.height))
        self.inverted = False

    def invert(self, enabled: bool = True):
        # The real panel inverts in hardware; there is nothing to do here, and
        # the colours already arrive the right way round.
        self.inverted = enabled

    def show_image(self, image: Image.Image, x_start: int = 0, y_start: int = 0):
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        if image.mode != "RGB":
            image = image.convert("RGB")

        self.buffer = image
        if self.sink is not None:
            self.sink(image.tobytes())

    def ShowImage(self, image, x_start: int = 0, y_start: int = 0):
        """Some drivers in this codebase use the capitalised spelling."""
        self.show_image(image, x_start, y_start)

    def clear(self):
        self.show_image(Image.new("RGB", (self.width, self.height)))

    def cleanup(self):
        pass


def install(sink, width: int, height: int) -> None:
    """
    Make the factory hand back a BrowserDisplay whatever the configured display
    type is, so the wallet's own settings do not have to be touched.
    """
    from seedsigner.hardware.displays import display_driver

    def instantiate(cls, display_type=None, width=width, height=height):
        return BrowserDisplay(_width=width, _height=height, sink=sink)

    display_driver.DisplayDriverFactory.instantiate_display_driver = classmethod(instantiate)
