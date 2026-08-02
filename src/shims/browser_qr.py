"""
Make the screens that display a QR actually draw one.

QRDisplayScreen puts every pixel of its output inside QRDisplayThread, and its
_run() does nothing but block waiting for a button. This port has no threads --
the shim standing in for threading.Thread drops anything loop-shaped -- so that
thread never runs and every QR the wallet wants to show comes out blank:
exported xpubs, signed PSBTs, SeedQR backups, addresses. The flow appears to
work and hands back an empty screen.

Rather than reimplement the drawing, the trick the camera preview already uses
applies here too: let SeedSigner's own loop body run one pass at a time. Its
last statement is a sleep sized to hold each frame for a sixth of a second, so
one pass is exactly one animation frame at the intended rate, and animated QRs
advance on their own without needing a timer this environment does not have.

The pump hangs off wait_for rather than off _run, because waiting for a button
is the only thing _run does. Everything above stays unmodified SeedSigner: the
brightness adjustment, the tip toast, the encoder's frame sequence and the exit
conditions all remain theirs.
"""

from seedsigner.gui.screens.screen import QRDisplayScreen

# Supplied by install(). Returns the next button value, or None if nothing is
# pressed, without blocking.
_poll_key = None


def _draw_one_frame(screen) -> bool:
    """Run one pass of the display thread's loop. False if there is none to run."""
    threads = getattr(screen, "threads", None)
    if not threads:
        return False

    display = threads[-1]
    passes = [True, False]
    type(display).keep_running = property(
        lambda self: passes.pop(0) if passes else False
    )
    try:
        display.run()
    finally:
        del type(display).keep_running
    return True


def _install_pump():
    original_run = QRDisplayScreen._run

    def _run(self):
        buttons = self.hw_inputs
        original_wait = buttons.wait_for

        def wait_and_draw(keys=[]):
            # One frame, then a look at the buttons. The frame is what paces
            # this: without it the loop would spin, and with it a press is
            # answered within a frame, which is far quicker than it feels.
            while True:
                if not _draw_one_frame(self):
                    return original_wait(keys)
                pressed = _poll_key()
                if pressed is not None and (not keys or pressed in keys):
                    buttons.last_input_time = 0
                    return pressed

        buttons.wait_for = wait_and_draw
        try:
            return original_run(self)
        finally:
            del buttons.wait_for

    QRDisplayScreen._run = _run


def install(poll_key):
    """`poll_key` returns a HardwareButtonsConstants value, or None if no press."""
    global _poll_key
    _poll_key = poll_key
    _install_pump()
