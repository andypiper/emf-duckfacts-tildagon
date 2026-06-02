import asyncio
import time

import app
import async_helpers
import requests

from app_components import Notification, clear_background
from app_components.tokens import label_font_size, small_font_size
from app_components.utils import wrap_text
from events.input import Buttons, BUTTON_TYPES
from system.eventbus import eventbus
from system.patterndisplay.events import PatternDisable, PatternEnable
from tildagonos import tildagonos

_FACT_URL = "https://03vpefsitf.execute-api.eu-west-1.amazonaws.com/prod/"
_FACT_WIDTH = 180
_LED_YELLOW = (80, 60, 0)
_NUM_LEDS = 12
_SWEEP_PERIOD = 2000  # ms for one full sweep around the badge while fetching
_FLASH_PERIOD = 400  # ms per on/off phase of the post-fetch double flash


class DuckFactsApp(app.App):
    def __init__(self):
        super().__init__()
        self.fact = "Ready for a Duck Fact?\nPress confirm!"
        self.notification = None
        self.button_states = Buttons(self)
        self._wrapped_lines = None
        self._font_size = label_font_size  # larger for the initial prompt

        self._fetching = False
        self._should_fetch = False
        self._anim_phase = 0.0
        # post-fetch double flash state machine: 4 = start ON, counts to 0
        self._flash_steps = 0
        self._flash_timer = 0

    async def run(self, render_update):
        last_time = time.ticks_ms()
        while True:
            cur_time = time.ticks_ms()
            delta = time.ticks_diff(cur_time, last_time)
            last_time = cur_time

            if self._should_fetch and not self._fetching:
                self._should_fetch = False
                await self._fetch_fact(render_update)
                last_time = time.ticks_ms()  # reset after blocking fetch
                continue

            if self.update(delta) is not False:
                await render_update()
            else:
                await asyncio.sleep(0.05)

    async def _fetch_fact(self, render_update):
        self._fetching = True
        self._anim_phase = 0.0
        eventbus.emit(PatternDisable())
        try:
            response = await async_helpers.unblock(
                requests.get, render_update, _FACT_URL
            )
            self.fact = response.json()["fact"]
        except Exception:
            self.fact = "No ducks available!\nCheck your wifi."
        finally:
            self._fetching = False
            self._wrapped_lines = None
            self._font_size = small_font_size
            self.notification = Notification("QUACK!")
            # Start double flash: set LEDs ON and kick off state machine
            self._set_leds(_LED_YELLOW)
            self._flash_steps = 4
            self._flash_timer = _FLASH_PERIOD

    def _set_leds(self, colour):
        for i in range(1, _NUM_LEDS + 1):
            tildagonos.leds[i] = colour
        tildagonos.leds.write()

    def background_update(self, delta):
        if self._fetching:
            # Sweep: one LED front moves smoothly around the ring
            self._anim_phase = (
                self._anim_phase + delta * _NUM_LEDS / _SWEEP_PERIOD
            ) % _NUM_LEDS
            lit = int(self._anim_phase)
            frac = self._anim_phase - lit
            for i in range(_NUM_LEDS):
                if i < lit:
                    tildagonos.leds[i + 1] = _LED_YELLOW
                elif i == lit:
                    # fractional leading edge fades in
                    tildagonos.leds[i + 1] = (
                        int(_LED_YELLOW[0] * frac),
                        int(_LED_YELLOW[1] * frac),
                        0,
                    )
                else:
                    tildagonos.leds[i + 1] = (0, 0, 0)
            tildagonos.leds.write()
        elif self._flash_steps > 0:
            # Double flash: ON→OFF→ON→OFF, each phase _FLASH_PERIOD ms
            self._flash_timer -= delta
            if self._flash_timer <= 0:
                self._flash_steps -= 1
                self._flash_timer = _FLASH_PERIOD
                if self._flash_steps == 0:
                    self._set_leds((0, 0, 0))
                    eventbus.emit(PatternEnable())
                elif self._flash_steps % 2 == 0:
                    self._set_leds(_LED_YELLOW)
                else:
                    self._set_leds((0, 0, 0))

    def select_handler(self):
        self.button_states.clear()
        self._should_fetch = True

    def back_handler(self):
        self.button_states.clear()
        self.minimise()

    def update(self, delta):
        if self.notification:
            self.notification.update(delta)
            if not self.notification._open and self.notification._is_closed():
                self.notification = None
        if self.button_states.get(BUTTON_TYPES["CANCEL"]):
            self.back_handler()
        if self.button_states.get(BUTTON_TYPES["CONFIRM"]):
            self.select_handler()

    def draw(self, ctx):
        clear_background(ctx)
        ctx.save()

        ctx.font_size = self._font_size
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(1, 1, 1)

        if self._wrapped_lines is None:
            self._wrapped_lines = wrap_text(
                ctx, self.fact, self._font_size, width=_FACT_WIDTH
            )

        lines = self._wrapped_lines
        y_start = -(len(lines) - 1) * self._font_size / 2
        for i, line in enumerate(lines):
            ctx.move_to(0, y_start + i * self._font_size)
            ctx.text(line)

        ctx.restore()

        if self.notification:
            self.notification.draw(ctx)


__app_export__ = DuckFactsApp
