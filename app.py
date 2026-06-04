import asyncio
import gzip
import json
import math
import os
import random
import sys
import time

import app
import async_helpers
import requests
import settings

from app_components import Notification, clear_background
from app_components.background import Background as bg
from app_components.tokens import button_labels, label_font_size, small_font_size
from app_components.utils import wrap_text
from events.input import Buttons, BUTTON_TYPES
from system.eventbus import eventbus
from system.patterndisplay.events import PatternDisable, PatternEnable
from tildagonos import tildagonos

try:
    import imu as _imu
    _HAS_IMU = True
except ImportError:
    _HAS_IMU = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FACT_URL = "https://03vpefsitf.execute-api.eu-west-1.amazonaws.com/prod/"
_MASTODON_LOOKUP = "https://mastodon.social/api/v1/accounts/lookup?acct=emfducks"
_MASTODON_STATUSES = (
    "https://mastodon.social/api/v1/accounts/{}/statuses"
    "?limit=1&exclude_reblogs=true"
)

_FACT_WIDTH = 160
_FACT_MAX_HEIGHT = 175
_FACT_MIN_FONT = 12
_LONG_PRESS_MS = 1500

_NUM_LEDS = 12
_SWEEP_PERIOD = 2000
_SWEEP_COLOUR = (120, 90, 0)
_FLASH_PERIOD = 400
_ANIM_MS = 150
_SPRITE_SCALE = 3
_AVATAR_RADIUS = 50

# LED rotation palettes for the lights view, matched to duck colour scheme
_LED_MALLARD_COLOURS = (  # duck 1: green → brown → orange
    (0, 90, 0),
    (0, 90, 0),
    (0, 80, 0),
    (0, 80, 0),
    (90, 55, 0),
    (90, 55, 0),
    (80, 50, 0),
    (80, 50, 0),
    (190, 70, 0),
    (190, 70, 0),
    (170, 65, 0),
    (170, 65, 0),
)
_LED_YELLOW_COLOURS = (  # duck 2 & 3: bright yellow → gold → amber
    (200, 160, 0),
    (200, 160, 0),
    (200, 160, 0),
    (200, 160, 0),
    (160, 110, 0),
    (160, 110, 0),
    (160, 110, 0),
    (160, 110, 0),
    (180, 90, 0),
    (180, 90, 0),
    (180, 90, 0),
    (180, 90, 0),
)
_LED_DUCK_PERIOD = 2000
_LED_PARTY_PERIOD = 1400  # faster rotation on the DUCK PARTY screen

# Map sprite filename prefix → LED palette
_DUCK_LED_MAP = {
    "duck_": _LED_MALLARD_COLOURS,
    "duck2_": _LED_YELLOW_COLOURS,
    "duck3_": _LED_YELLOW_COLOURS,
}

_NON_QUACK = ["*honk*", "*hiss*", "*whistle*", "*squeak*", "*coo*"]

_PHOTO_LOADING_PHRASES = [
    "Looking for ducks...",
    "Finding a duck...",
    "Duck Hunting",
    "One moment please...",
    "Quackspotting",
    "Wildfowl sighted...",
    "Scanning the pond...",
    "Duck radar active...",
    "Awaiting quacks!",
    "Checking the reeds",
    "Pond patrol...",
    "Consulting mallards...",
    "Wait for a duck",
    "Looking at the lake",
    "Ruffling feathers...",
    "Binoculars active",
    "Locating Anatidae...",
    "Duck Season!"
]

# Party mode themes for the DUCK PARTY lights screen.
# Switch via: settings.set("duckfacts_party", "2024") / settings.save()
# Defaults to "2026"
_PARTY_2026 = "2026"
_PARTY_2024 = "2024"

# 2026 star colours: #F77F02 orange, #F9E200 yellow, white
# LED rotation always follows the loaded duck's colour scheme (self._duck_leds).
_PARTY_MODES = {
    _PARTY_2026: {
        "subtitle": "IN SPAAACE!",
        "title_rgb": (0.9, 0.7, 0.1),
        "subtitle_rgb": (0.5, 0.8, 1.0),
        "star_colours": ((247, 127, 2), (249, 226, 0), (255, 255, 255)),
    },
    _PARTY_2024: {
        "subtitle": "SOLAR POWER!",
        "title_rgb": (1.0, 0.9, 0.1),
        "subtitle_rgb": (1.0, 0.5, 0.0),
        "star_colours": ((255, 200, 0), (255, 140, 0), (255, 245, 200)),
    },
}

# Views
_HOME = "home"
_FACT = "fact"
_MASTODON = "mastodon"
_PHOTO = "photo"
_LEDS = "leds"
_FAVS = "favs"
_CREDITS = "credits"
_PROMPT_CLEAR = "prompt_clear"

# Mastodon sub-pages ordered top to bottom (UP/DOWN navigate)
_MASTO_PAGES = ["qr", "avatar", "content", "time"]
_PHOTO_PAGES = ["image", "info"]

# Exclusion zones for DUCK PARTY stars: (x_min, x_max, y_min, y_max)
# Based on sprite scale=3, fh≈25, oy≈-47; label_font_size≈26px.
# Subtitle zone covers both "IN SPAAACE!" and "SOLAR POWER!" (similar widths).
_STAR_EXCLUSIONS = (
    (-42, 42, -50, 30),  # duck sprite body
    (-77, 77, -82, -53),  # "DUCK PARTY" title
    (-82, 82, 35, 62),  # subtitle
)


def _star_ok(x, y):
    for x0, x1, y0, y1 in _STAR_EXCLUSIONS:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return False
    return True


# ---------------------------------------------------------------------------
# Asset path resolution
# ---------------------------------------------------------------------------

if sys.implementation.name == "micropython":
    _ASSET_PATH = "assets/"
    try:
        for _a in os.listdir("/apps"):
            if _a.startswith("andypiper_emf"):
                _ASSET_PATH = f"/apps/{_a}/assets/"
                break
    except OSError:
        pass
else:
    _ASSET_PATH = os.path.dirname(os.path.abspath(__file__)) + "/assets/"

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _feistiness_colour(rating):
    if rating < 34:
        return (60, 40, 0)
    elif rating < 67:
        return (120, 90, 0)
    else:
        return (200, 70, 0)


def _load_sprite(filename):
    try:
        with open(_ASSET_PATH + filename, "rb") as f:
            return json.loads(gzip.decompress(f.read()).decode())
    except OSError:
        return None


def _load_local_facts():
    try:
        with open(_ASSET_PATH + "facts.txt.gz", "rb") as f:
            text = gzip.decompress(f.read()).decode()
        return [ln for ln in text.splitlines() if ln.strip()]
    except OSError:
        return []


def _load_favs():
    try:
        return json.loads(settings.get("duckfacts_favs", "[]"))
    except Exception:
        return []


def _save_favs(favs):
    settings.set("duckfacts_favs", json.dumps(favs))
    settings.save()


def _increment_fact_count():
    n = settings.get("duckfacts_count", 0) + 1
    settings.set("duckfacts_count", n)
    settings.save()
    return n


def _strip_html(text):
    for tag in ("<br>", "<br/>", "<br />", "</p>", "</li>"):
        text = text.replace(tag, "\n")
    result = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            result.append(ch)
    out = "".join(result)
    for entity, char in (
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&apos;", "'"),
    ):
        out = out.replace(entity, char)
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip()


def _break_long_words(text, max_chars=14):
    """Replace URLs with [link] and split words > max_chars.

    fill_line() in app_components/utils.py has an infinite-loop bug when a
    word is wider than the line width: the inner while loop shrinks 'word' to
    '' but never updates 'remaining_word', spinning forever.  Pre-breaking long
    words prevents that path from being reached.
    """
    result = []
    for paragraph in text.split("\n"):
        words = []
        for word in paragraph.split(" "):
            if word.startswith("http://") or word.startswith("https://"):
                words.append("[link]")
            elif len(word) > max_chars:
                while len(word) > max_chars:
                    words.append(word[:max_chars])
                    word = word[max_chars:]
                if word:
                    words.append(word)
            else:
                words.append(word)
        result.append(" ".join(words))
    return "\n".join(result)


def _auto_wrap(ctx, text, max_height, width=_FACT_WIDTH):
    """Wrap text, stepping font down until block fits or hits minimum."""
    text = _break_long_words(text)
    font_size = small_font_size
    while font_size >= _FACT_MIN_FONT:
        ctx.font_size = font_size
        lines = wrap_text(ctx, text, font_size, width=width)
        if len(lines) * font_size <= max_height:
            return font_size, lines
        font_size -= 2
    return font_size, lines


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class DuckFactsApp(app.App):
    def __init__(self):
        super().__init__()
        self._view = _HOME

        # Fact display
        self.fact = None
        self.notification = None
        self.button_states = Buttons(self)
        self._wrapped_lines = None
        self._font_size = small_font_size
        self._fetch_mode = "live"
        self._local_facts = _load_local_facts()

        # Fetch state
        self._fetching = False
        self._should_fetch = False
        self._fetching_mastodon = False
        self._should_fetch_mastodon = False
        self._fetching_photo = False
        self._should_fetch_photo = False
        self._photo_path = settings.get("duckfacts_photo_path", _ASSET_PATH + "duck_photo.jpg")
        self._photo_title = settings.get("duckfacts_photo_title", "Rubber Duck")
        self._photo_description = settings.get("duckfacts_photo_description", "Just, you know, a duck.")
        self._photo_attribution = settings.get("duckfacts_photo_attribution", "starwatchers-studio")
        self._photo_license = settings.get("duckfacts_photo_license", "via itch.io")
        self._photo_sub = "image"
        self._photo_wrapped_title = None
        self._photo_title_font = small_font_size
        self._photo_wrapped_attrib = None
        self._photo_attrib_font = small_font_size
        self._photo_wrapped_desc = None
        self._photo_desc_font = small_font_size
        self._photo_loading_text = "Looking for ducks..."

        # Long-press for favourites / systems
        self._confirm_hold_ms = 0
        self._confirm_was_held = False
        self._left_hold_ms = 0
        self._left_was_held = False
        self._up_hold_ms = 0
        self._up_was_held = False
        self._credits_page = 0
        self._credits_secret_unlocked = False

        # Shake-to-fact (local facts)
        self._shake_cooldown = 0
        self._last_magnitude = (
            9.8  # approximate resting gravity; overwritten on first read
        )
        if _HAS_IMU:
            try:
                _acc = _imu.acc_read()
                self._last_magnitude = math.sqrt(
                    _acc[0] ** 2 + _acc[1] ** 2 + _acc[2] ** 2
                )
            except Exception:
                pass

        # LED animation
        self._anim_phase = 0.0
        self._flash_steps = 0
        self._flash_timer = 0
        self._flash_colour = _SWEEP_COLOUR

        # Duck sprite — load one random animation; only one kept in heap at a time
        _names = [
            "duck_idle_normal.json.gz",
            "duck_walk_normal.json.gz",
            "duck_idle_bounce.json.gz",
            "duck_walk_bounce.json.gz",
            "duck2_idle_normal.json.gz",
            "duck2_walk_normal.json.gz",
            "duck2_idle_bounce.json.gz",
            "duck2_walk_bounce.json.gz",
            "duck3_idle_normal.json.gz",
            "duck3_walk_normal.json.gz",
        ]
        _start = random.randint(0, len(_names) - 1)
        self._sprite = None
        _chosen = "duck_"
        for _i in range(len(_names)):
            _n = _names[(_start + _i) % len(_names)]
            _s = _load_sprite(_n)
            if _s is not None:
                self._sprite = _s
                _chosen = _n
                break
        # LED palette for the lights view matches the loaded duck's colours
        self._duck_leds = _LED_MALLARD_COLOURS
        for _prefix, _pal in _DUCK_LED_MAP.items():
            if _chosen.startswith(_prefix):
                self._duck_leds = _pal
                break
        self._anim_frame = 0
        self._anim_timer = _ANIM_MS

        # Party mode configuration
        self._party = _PARTY_MODES.get(
            settings.get("duckfacts_party", _PARTY_2026),
            _PARTY_MODES[_PARTY_2026],
        )

        # Stars for the DUCK PARTY screen: [x, y, phase, speed_per_frame, colour_idx]
        # Rejection-sample to keep stars out of the duck and text areas.
        _nc = len(self._party["star_colours"])
        self._stars = []
        for _ in range(500):
            if len(self._stars) >= 25:
                break
            _sx = random.randint(-110, 110)
            _sy = random.randint(-110, 110)
            if _star_ok(_sx, _sy):
                self._stars.append(
                    [
                        _sx,
                        _sy,
                        random.random() * 6.28,
                        0.03 + random.random() * 0.12,
                        random.randint(0, _nc - 1),
                    ]
                )

        # Mastodon view
        self._mastodon_post = None
        self._mastodon_time = None
        self._mastodon_sub = "content"
        self._mastodon_wrapped = None
        self._masto_font = small_font_size
        cached = settings.get("mastodon_emfducks_post", None)
        if cached:
            try:
                post = json.loads(cached)
                self._mastodon_post = post.get("content")
                self._mastodon_time = post.get("time")
            except Exception:
                pass

        # Sprites for UI
        self._mastodon_sprite = _load_sprite("mastodon.json.gz")
        self._qr_sprite = _load_sprite("emfducks_qr.json.gz")
        self._icon_bolt = _load_sprite("icon_bolt.json.gz")
        self._icon_confetti = _load_sprite("icon_confetti.json.gz")
        self._icon_binoculars = _load_sprite("icon_binoculars.json.gz")
        self._icon_heart = _load_sprite("icon_heart.json.gz")
        self._icon_refresh = _load_sprite("icon_refresh.json.gz")
        self._icon_info = _load_sprite("icon_info.json.gz")

        # Favourites
        self._fav_facts = _load_favs()
        self._fav_index = 0
        self._fav_wrapped = None
        self._fav_font = small_font_size

        # Cached display values
        self._fact_count = settings.get("duckfacts_count", 0)

        # Cleanup orphaned photo files from assets/
        try:
            active_photo_filename = self._photo_path.split("/")[-1]
            for f in os.listdir(_ASSET_PATH):
                if f.startswith("duck_photo_") and f.endswith(".jpg"):
                    if f != active_photo_filename:
                        try:
                            os.remove(_ASSET_PATH + f)
                        except Exception:
                            pass
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Async loop
    # -----------------------------------------------------------------------

    async def run(self, render_update):
        last_time = time.ticks_ms()
        while True:
            cur_time = time.ticks_ms()
            delta = time.ticks_diff(cur_time, last_time)
            last_time = cur_time

            if self._should_fetch and not self._fetching:
                self._should_fetch = False
                await self._fetch_fact(render_update)
                last_time = time.ticks_ms()
                continue

            if self._should_fetch_mastodon and not self._fetching_mastodon:
                self._should_fetch_mastodon = False
                await self._fetch_mastodon(render_update)
                last_time = time.ticks_ms()
                continue

            if self._should_fetch_photo and not self._fetching_photo:
                self._should_fetch_photo = False
                await self._fetch_photo(render_update)
                last_time = time.ticks_ms()
                continue

            if self.update(delta) is not False:
                await render_update()
            else:
                await asyncio.sleep(0.05)

    async def _fetch_fact(self, render_update):
        self._fetching = True
        self._anim_phase = 0.0
        eventbus.emit(PatternDisable())
        led_colour = _feistiness_colour(50)
        sound = "QUACK!"
        try:
            if self._fetch_mode == "local" and self._local_facts:
                self.fact = random.choice(self._local_facts)
                await asyncio.sleep(0.05)
            else:
                response = await async_helpers.unblock(
                    requests.get, render_update, _FACT_URL
                )
                try:
                    data = response.json()
                    self.fact = data["fact"]
                    led_colour = _feistiness_colour(
                        data.get("feistynessRating", 50))
                    if not data.get("quack", True):
                        sound = random.choice(_NON_QUACK)
                finally:
                    response.close()
        except Exception:
            self.fact = "No ducks available!\nCheck your wifi."
        finally:
            self._fact_count = _increment_fact_count()
            self._fetching = False
            self._wrapped_lines = None
            self._font_size = small_font_size
            self._view = _FACT
            self.notification = Notification(sound)
            self._set_leds(led_colour)
            self._flash_colour = led_colour
            self._flash_steps = 4
            self._flash_timer = _FLASH_PERIOD

    async def _fetch_mastodon(self, render_update):
        self._fetching_mastodon = True
        eventbus.emit(PatternDisable())
        try:
            account_id = settings.get("mastodon_emfducks_id", None)
            if not account_id:
                r = await async_helpers.unblock(
                    requests.get, render_update, _MASTODON_LOOKUP
                )
                try:
                     account_id = r.json()["id"]
                finally:
                     r.close()
                settings.set("mastodon_emfducks_id", account_id)
                settings.save()

            r = await async_helpers.unblock(
                requests.get,
                render_update,
                _MASTODON_STATUSES.format(account_id),
            )
            try:
                data = r.json()
                r.close()
                if data:
                    latest = data[0]
                    content = _strip_html(latest.get("content", ""))
                    raw_ts = latest.get("created_at", "")
                    time_str = (
                        raw_ts[:16].replace("T", " ") if len(raw_ts) >= 16 else raw_ts[:10]
                    )
                    self._mastodon_post = content
                    self._mastodon_time = time_str
                    self._mastodon_wrapped = None
                    settings.set(
                        "mastodon_emfducks_post",
                        json.dumps({"content": content, "time": time_str}),
                    )
                    settings.save()
            finally:
                r.close()
        except Exception as e:
            print("Mastodon fetch error:", e)
            if self._mastodon_post is None:
                self._mastodon_post = "Could not fetch @emfducks.\nCheck your wifi."
                self._mastodon_time = ""
        finally:
            self._fetching_mastodon = False
            eventbus.emit(PatternEnable())

    async def _fetch_photo(self, render_update):
        self._fetching_photo = True
        self._photo_loading_text = random.choice(_PHOTO_LOADING_PHRASES)
        eventbus.emit(PatternDisable())
        await render_update()
        try:
            headers = {
                "User-Agent": "tildagon-duck-facts"
            }
            use_random_duk = (random.randint(0, 1) == 1)
            download_url = None
            title = "Duck"
            description = "No description"
            attrib = "Unknown"
            lic = "Unknown"

            for attempt in range(2):
                data = None
                if use_random_duk:
                    resp = await async_helpers.unblock(
                        requests.get,
                        render_update,
                        "https://random-d.uk/api/v2/quack",
                        headers=headers
                    )
                    try:
                        if resp.status_code == 200:
                            data = resp.json()
                            if data and data.get("url"):
                                download_url = data.get("url")
                                try:
                                    fn = download_url.split("/")[-1].split(".")[0]
                                    title = f"Duck #{fn}" if fn.isdigit() else "Random Duck"
                                except Exception:
                                    title = "Random Duck"
                                description = "A lovely random duck"
                                attrib = "random-d.uk"
                                lic = "Various"
                    finally:
                        resp.close()
                else:
                    resp = await async_helpers.unblock(
                        requests.get,
                        render_update,
                        "https://ducks.now/api/v0/random/",
                        headers=headers
                    )
                    try:
                        if resp.status_code == 200:
                            data = resp.json()
                            if data and data.get("download_url"):
                                download_url = data.get("download_url")
                                title = data.get("title", "Duck")
                                description = data.get("description", "No description")
                                attrib = data.get("attribution_name", "Unknown")
                                lic = data.get("attribution_license", "Unknown")
                                if "Creative Commons" in lic:
                                    lic = lic.replace("Creative Commons", "CC")
                                    lic = lic.replace("Attribution-Share Alike", "BY-SA")
                                    lic = lic.replace("Attribution", "BY")
                                    lic = lic.replace("International", "")
                                    lic = lic.replace("License", "")
                                    lic = lic.strip()
                                elif lic == "Public domain":
                                    lic = "PD"
                    finally:
                        resp.close()

                if download_url:
                    break
                use_random_duk = not use_random_duk

            if download_url:
                proxy_url = f"https://wsrv.nl/?url={download_url}&w=150&h=150&output=jpg&q=80"
                img_resp = await async_helpers.unblock(
                    requests.get,
                    render_update,
                    proxy_url,
                    headers=headers
                )
                try:
                    if img_resp.status_code == 200:
                        content = img_resp.content
                        if content and len(content) > 0:
                            t = time.ticks_ms()
                            new_path = f"{_ASSET_PATH}duck_photo_{t}.jpg"
                            with open(new_path, "wb") as f:
                                f.write(content)

                            old_path = self._photo_path
                            default_path = _ASSET_PATH + "duck_photo.jpg"
                            if old_path != default_path:
                                try:
                                    os.remove(old_path)
                                except Exception:
                                    pass

                            self._photo_path = new_path
                            settings.set("duckfacts_photo_path", new_path)

                            if sys.implementation.name != "micropython":
                                try:
                                    import sys as _sys
                                    for mod_name, mod in _sys.modules.items():
                                        if mod_name == "ctx" or mod_name.endswith(".ctx"):
                                            if hasattr(mod, "_img_cache") and old_path in mod._img_cache:
                                                del mod._img_cache[old_path]
                                            if hasattr(mod, "_img_cache") and new_path in mod._img_cache:
                                                del mod._img_cache[new_path]
                                except Exception:
                                    pass

                            self._photo_title = title
                            self._photo_description = description
                            self._photo_attribution = attrib
                            self._photo_license = lic

                            settings.set("duckfacts_photo_title", self._photo_title)
                            settings.set("duckfacts_photo_description", self._photo_description)
                            settings.set("duckfacts_photo_attribution", self._photo_attribution)
                            settings.set("duckfacts_photo_license", self._photo_license)
                            settings.save()

                            self._photo_wrapped_title = None
                            self._photo_wrapped_attrib = None
                            self._photo_wrapped_desc = None
                            self._photo_sub = "image"
                            self._fact_count = _increment_fact_count()
                finally:
                    img_resp.close()
        except Exception as e:
            print("Photo fetch error:", e)
        finally:
            self._fetching_photo = False
            # Double flash in gentle yellow when done
            led_colour = (120, 90, 0)
            self._set_leds(led_colour)
            self._flash_colour = led_colour
            self._flash_steps = 4
            self._flash_timer = _FLASH_PERIOD

    # -----------------------------------------------------------------------
    # Favourites
    # -----------------------------------------------------------------------

    def _save_favourite(self):
        if not self.fact:
            return
        if self.fact not in self._fav_facts:
            self._fav_facts.append(self.fact)
            _save_favs(self._fav_facts)
            msg = "Saved!"
        else:
            msg = "Already saved"
        self.notification = Notification(msg)

    # -----------------------------------------------------------------------
    # LEDs
    # -----------------------------------------------------------------------

    def _set_leds(self, colour):
        for i in range(1, _NUM_LEDS + 1):
            tildagonos.leds[i] = colour
        tildagonos.leds.write()

    def background_update(self, delta):
        if self._fetching or self._fetching_mastodon or self._fetching_photo:
            self._anim_phase = (
                self._anim_phase + delta * _NUM_LEDS / _SWEEP_PERIOD
            ) % _NUM_LEDS
            lit = int(self._anim_phase)
            frac = self._anim_phase - lit
            for i in range(_NUM_LEDS):
                if i < lit:
                    tildagonos.leds[i + 1] = _SWEEP_COLOUR
                elif i == lit:
                    tildagonos.leds[i + 1] = (
                        int(_SWEEP_COLOUR[0] * frac),
                        int(_SWEEP_COLOUR[1] * frac),
                        0,
                    )
                else:
                    tildagonos.leds[i + 1] = (0, 0, 0)
            tildagonos.leds.write()
        elif self._flash_steps > 0:
            self._flash_timer -= delta
            if self._flash_timer <= 0:
                self._flash_steps -= 1
                self._flash_timer = _FLASH_PERIOD
                if self._flash_steps == 0:
                    self._set_leds((0, 0, 0))
                    eventbus.emit(PatternEnable())
                elif self._flash_steps % 2 == 0:
                    self._set_leds(self._flash_colour)
                else:
                    self._set_leds((0, 0, 0))
        elif self._view == _LEDS:
            self._anim_phase = (
                self._anim_phase + delta * _NUM_LEDS / _LED_PARTY_PERIOD
            ) % _NUM_LEDS
            offset = int(self._anim_phase)
            for i in range(_NUM_LEDS):
                tildagonos.leds[i + 1] = self._duck_leds[(i + offset) % _NUM_LEDS]
            tildagonos.leds.write()

    # -----------------------------------------------------------------------
    # Input
    # -----------------------------------------------------------------------

    def update(self, delta):
        bg.update(delta)
        if self.notification:
            self.notification.update(delta)
            if not self.notification._open and self.notification._is_closed():
                self.notification = None

        # CANCEL always: exit from home, return to home from content views
        if self.button_states.get(BUTTON_TYPES["CANCEL"]):
            self.button_states.clear()
            self._confirm_hold_ms = 0
            self._confirm_was_held = False
            self._left_hold_ms = 0
            self._left_was_held = False
            self._up_hold_ms = 0
            self._up_was_held = False
            if self._view == _HOME:
                self.minimise()
            else:
                if self._view == _LEDS:
                    self._set_leds((0, 0, 0))
                    eventbus.emit(PatternEnable())
                self._view = _HOME
                self._wrapped_lines = None
            return

        if self._view in (_HOME, _LEDS):
            if self._sprite and not self._fetching:
                self._anim_timer -= delta
                if self._anim_timer <= 0:
                    self._anim_frame = (self._anim_frame + 1) % len(
                        self._sprite["frames"]
                    )
                    self._anim_timer += _ANIM_MS

        if self._view == _HOME:
            # Shake triggers a random local fact
            if _HAS_IMU and not self._fetching:
                self._shake_cooldown = max(0, self._shake_cooldown - delta)
                try:
                    acc = _imu.acc_read()
                    mag = math.sqrt(acc[0] ** 2 + acc[1] ** 2 + acc[2] ** 2)
                    if (
                        self._shake_cooldown == 0
                        and abs(mag - self._last_magnitude) > 5.0
                    ):
                        self._fetch_mode = "local"
                        self._should_fetch = True
                        self._shake_cooldown = 3000
                    self._last_magnitude = mag
                except Exception:
                    pass

            # Clock-face layout:
            #   CANCEL(top-L)=exit  UP(top)=free    RIGHT(top-R)=live
            #   LEFT(bot-L)=@emf    DOWN(bot)=lights CONFIRM(bot-R)=photo
            if self.button_states.get(BUTTON_TYPES["RIGHT"]):
                self.button_states.clear()
                self._fetch_mode = "live"
                self._should_fetch = True
            elif self.button_states.get(BUTTON_TYPES["LEFT"]):
                self.button_states.clear()
                self._mastodon_sub = "content"
                self._view = _MASTODON
                if self._mastodon_post is None and not self._fetching_mastodon:
                    self._should_fetch_mastodon = True
            elif self.button_states.get(BUTTON_TYPES["DOWN"]):
                self.button_states.clear()
                self._anim_phase = 0.0
                eventbus.emit(PatternDisable())
                self._view = _LEDS
            elif self.button_states.get(BUTTON_TYPES["CONFIRM"]):
                self.button_states.clear()
                self._photo_path = _ASSET_PATH + "duck_photo.jpg"
                self._photo_title = "Duck"
                self._photo_description = "It's a duck"
                self._photo_attribution = "starwatchers-studio"
                self._photo_license = "itch.io"
                self._photo_wrapped_title = None
                self._photo_wrapped_attrib = None
                self._photo_wrapped_desc = None
                self._photo_sub = "image"
                self._view = _PHOTO

            # UP (top button): short press = credits, long press = prompt clear quack count
            up_now = self.button_states.get(BUTTON_TYPES["UP"])
            if up_now:
                self._up_hold_ms += delta
                self._up_was_held = True
            elif self._up_was_held:
                self._up_was_held = False
                hold = self._up_hold_ms
                self._up_hold_ms = 0
                self.button_states.clear()
                if hold >= _LONG_PRESS_MS:
                    self._view = _PROMPT_CLEAR
                else:
                    self._credits_page = 0
                    self._view = _CREDITS
            else:
                self._up_hold_ms = 0

        elif self._view == _FACT:
            if self.button_states.get(BUTTON_TYPES["CONFIRM"]):
                self.button_states.clear()
                self._fetch_mode = "live"
                self._should_fetch = True

            left_now = self.button_states.get(BUTTON_TYPES["LEFT"])
            if left_now:
                self._left_hold_ms += delta
                self._left_was_held = True
            elif self._left_was_held:
                self._left_was_held = False
                hold = self._left_hold_ms
                self._left_hold_ms = 0
                self.button_states.clear()
                if hold >= _LONG_PRESS_MS:
                    self._save_favourite()
                else:
                    self._fav_index = 0
                    self._fav_wrapped = None
                    self._view = _FAVS
            else:
                self._left_hold_ms = 0

        elif self._view == _MASTODON:
            idx = _MASTO_PAGES.index(self._mastodon_sub)
            if self.button_states.get(BUTTON_TYPES["UP"]):
                self.button_states.clear()
                self._mastodon_sub = _MASTO_PAGES[max(0, idx - 1)]
            elif self.button_states.get(BUTTON_TYPES["DOWN"]):
                self.button_states.clear()
                self._mastodon_sub = _MASTO_PAGES[min(len(_MASTO_PAGES) - 1, idx + 1)]
            elif self.button_states.get(BUTTON_TYPES["CONFIRM"]):
                self.button_states.clear()
                self._mastodon_post = None
                self._mastodon_wrapped = None
                self._mastodon_sub = "content"
                self._should_fetch_mastodon = True

        elif self._view == _FAVS:
            if self._fav_facts:
                if self.button_states.get(BUTTON_TYPES["UP"]):
                    self.button_states.clear()
                    self._fav_index = max(0, self._fav_index - 1)
                    self._fav_wrapped = None
                elif self.button_states.get(BUTTON_TYPES["DOWN"]):
                    self.button_states.clear()
                    self._fav_index = min(len(self._fav_facts) - 1, self._fav_index + 1)
                    self._fav_wrapped = None

        elif self._view == _PHOTO:
            idx = _PHOTO_PAGES.index(self._photo_sub)
            if self.button_states.get(BUTTON_TYPES["UP"]) and idx > 0:
                self.button_states.clear()
                self._photo_sub = _PHOTO_PAGES[idx - 1]
            elif self.button_states.get(BUTTON_TYPES["DOWN"]) and idx < len(_PHOTO_PAGES) - 1:
                self.button_states.clear()
                self._photo_sub = _PHOTO_PAGES[idx + 1]
            elif self.button_states.get(BUTTON_TYPES["CONFIRM"]):
                self.button_states.clear()
                if not self._fetching_photo:
                    self._should_fetch_photo = True

        elif self._view == _CREDITS:
            if _HAS_IMU:
                try:
                    acc = _imu.acc_read()
                    mag = math.sqrt(acc[0] ** 2 + acc[1] ** 2 + acc[2] ** 2)
                    if abs(mag - self._last_magnitude) > 5.0:
                        self._credits_secret_unlocked = True
                        self._credits_page = 2
                    self._last_magnitude = mag
                except Exception:
                    pass

            max_page = 2 if self._credits_secret_unlocked else 1
            if self.button_states.get(BUTTON_TYPES["UP"]):
                self.button_states.clear()
                self._credits_page = max(0, self._credits_page - 1)
            elif self.button_states.get(BUTTON_TYPES["DOWN"]):
                self.button_states.clear()
                self._credits_page = min(max_page, self._credits_page + 1)

        elif self._view == _PROMPT_CLEAR:
            confirm = self.button_states.get(BUTTON_TYPES["CONFIRM"])
            any_pressed = False
            for btn_name in BUTTON_TYPES:
                if self.button_states.get(BUTTON_TYPES[btn_name]):
                    any_pressed = True
                    break
            if any_pressed:
                self.button_states.clear()
                if confirm:
                    self._fact_count = 0
                    settings.set("duckfacts_count", 0)
                    settings.save()
                    self.notification = Notification("Cleared!")
                self._view = _HOME

    # -----------------------------------------------------------------------
    # Drawing
    # -----------------------------------------------------------------------

    def draw(self, ctx):
        if self._view == _LEDS:
            ctx.rgb(0, 0, 0).rectangle(-120, -120, 240, 240).fill()
        else:
            bg.draw(ctx)
        if self._view == _HOME:
            self._draw_home(ctx)
        elif self._view == _FACT:
            self._draw_fact(ctx)
        elif self._view == _MASTODON:
            self._draw_mastodon(ctx)
        elif self._view == _FAVS:
            self._draw_favs(ctx)
        elif self._view == _LEDS:
            self._draw_leds(ctx)
        elif self._view == _PHOTO:
            self._draw_photo(ctx)
        elif self._view == _CREDITS:
            self._draw_credits(ctx)
        elif self._view == _PROMPT_CLEAR:
            self._draw_prompt_clear(ctx)
        else:
            self._draw_stub(ctx)
        if self.notification:
            self.notification.draw(ctx)
        self.draw_overlays(ctx)

    def _draw_button_icon(self, ctx, sprite, bx, by, align="center"):
        """Draw a single-frame icon sprite at a button position.

        align: 'center' | 'left' | 'right' — horizontal alignment relative to bx.
        The icon is always vertically centred on by.
        """
        if sprite is None:
            return
        iw, ih = sprite["w"], sprite["h"]
        if align == "right":
            ox = bx - iw
        elif align == "left":
            ox = bx
        else:
            ox = bx - iw // 2
        oy = by - ih // 2
        self._draw_sprite(ctx, 0, 1, ox, oy, sprite=sprite)

    def _draw_sprite(self, ctx, frame_idx, scale, ox, oy, sprite=None):
        if sprite is None:
            sprite = self._sprite
        palette = sprite["palette"]
        for seg in sprite["frames"][frame_idx]:
            code, x, y, w = seg
            r, g, b = palette[code]
            ctx.rgb(r / 255, g / 255, b / 255)
            ctx.rectangle(ox + x * scale, oy + y * scale, w * scale, scale).fill()

    def _draw_wrapped_lines(self, ctx, lines, font_size, y_offset=0):
        ctx.font_size = font_size
        y_start = -(len(lines) - 1) * font_size / 2 + y_offset
        for i, line in enumerate(lines):
            ctx.move_to(0, y_start + i * font_size)
            ctx.text(line)

    def _draw_home(self, ctx):
        count = self._fact_count
        busy = self._fetching or self._fetching_mastodon

        if self._sprite:
            fw, fh = self._sprite["w"], self._sprite["h"]
            ox = -(fw * _SPRITE_SCALE) // 2
            oy = -(fh * _SPRITE_SCALE) // 2 - 25
            ctx.save()
            self._draw_sprite(ctx, self._anim_frame, _SPRITE_SCALE, ox, oy)
            ctx.font_size = small_font_size
            ctx.text_align = ctx.CENTER
            ctx.text_baseline = ctx.MIDDLE
            text_y = oy + fh * _SPRITE_SCALE + 26
            if busy:
                ctx.rgb(0.6, 0.6, 0.6)
                ctx.move_to(0, text_y).text("fetching...")
            elif count > 0:
                ctx.rgb(0.35, 0.5, 0.25)
                ctx.move_to(0, text_y).text(f"quacked: {count}")
            ctx.restore()
        else:
            ctx.save()
            ctx.font_size = label_font_size
            ctx.text_align = ctx.CENTER
            ctx.text_baseline = ctx.MIDDLE
            ctx.rgb(1, 1, 1)
            ctx.move_to(0, 0).text("Duck Facts")
            ctx.restore()

        if not busy:
            if self._up_hold_ms > 200:
                frac = min(self._up_hold_ms / _LONG_PRESS_MS, 1.0)
                ctx.save()
                ctx.rgb(0, frac * 0.5, frac * 0.8)
                ctx.arc(0, -100, 12, 0, 2 * math.pi, True).fill()
                ctx.restore()

            button_labels(ctx)
            self._draw_back_arrow(ctx)
            self._draw_button_icon(ctx, self._icon_info, 0, -100, align="center")
            self._draw_button_icon(ctx, self._icon_bolt, 75, -75, align="right")
            self._draw_button_icon(ctx, self._icon_binoculars, 75, 75, align="right")
            self._draw_button_icon(ctx, self._icon_confetti, 0, 100, align="center")
            if self._mastodon_sprite:
                mh = self._mastodon_sprite["h"]
                self._draw_sprite(
                    ctx, 0, 1, -75, 75 - mh // 2, sprite=self._mastodon_sprite
                )
            else:
                ctx.save()
                ctx.font_size = small_font_size
                ctx.text_align = ctx.LEFT
                ctx.text_baseline = ctx.MIDDLE
                ctx.rgb(1, 1, 1)
                ctx.move_to(-75, 75).text("@")
                ctx.restore()

    def _draw_fact(self, ctx):
        # Long-press glow behind heart icon (LEFT position)
        if self._left_hold_ms > 200:
            frac = min(self._left_hold_ms / _LONG_PRESS_MS, 1.0)
            ctx.save()
            ctx.rgb(frac * 0.8, frac * 0.1, frac * 0.2)
            ctx.arc(-65, 75, 12, 0, 2 * math.pi, True).fill()
            ctx.restore()

        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(1, 1, 1)

        if self.fact is None:
            ctx.font_size = small_font_size
            ctx.move_to(0, 0).text("No fact yet")
        else:
            if self._wrapped_lines is None:
                self._font_size, self._wrapped_lines = _auto_wrap(
                    ctx, self.fact, _FACT_MAX_HEIGHT
                )
            self._draw_wrapped_lines(ctx, self._wrapped_lines, self._font_size)

        ctx.restore()

        self._draw_back_arrow(ctx)
        self._draw_button_icon(ctx, self._icon_refresh, 75, 75, align="right")

        self._draw_button_icon(ctx, self._icon_heart, -75, 75, align="left")

    def _draw_mastodon(self, ctx):
        idx = _MASTO_PAGES.index(self._mastodon_sub)
        has_up = idx > 0
        has_down = idx < len(_MASTO_PAGES) - 1

        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(1, 1, 1)

        if self._fetching_mastodon:
            ctx.font_size = small_font_size
            ctx.move_to(0, 0).text("Fetching @emfducks...")
        elif self._mastodon_sub == "qr":
            if self._qr_sprite:
                scale = 3
                qw = self._qr_sprite["w"] * scale
                qh = self._qr_sprite["h"] * scale
                ox, oy = -qw // 2, -qh // 2
                ctx.rgb(1, 1, 1)
                ctx.rectangle(ox, oy, qw, qh).fill()
                self._draw_sprite(ctx, 0, scale, ox, oy, sprite=self._qr_sprite)
            else:
                ctx.font_size = small_font_size
                ctx.move_to(0, 0).text("@emfducks")
        elif self._mastodon_sub == "avatar":
            avatar_path = _ASSET_PATH + "emfducks_avatar.jpg"
            try:
                ctx.save()
                ctx.begin_path()
                ctx.move_to(_AVATAR_RADIUS, 0)
                ctx.arc(0, 0, _AVATAR_RADIUS, 0, 2 * math.pi, False)
                ctx.clip()
                ctx.image(
                    avatar_path,
                    -_AVATAR_RADIUS,
                    -_AVATAR_RADIUS,
                    _AVATAR_RADIUS * 2,
                    _AVATAR_RADIUS * 2,
                )
                ctx.restore()
            except Exception:
                ctx.font_size = small_font_size
                ctx.move_to(0, 0).text("@emfducks")
        elif self._mastodon_sub == "time":
            ts = self._mastodon_time or ""
            date_part = ts[:10] if ts else "no date"
            time_part = ts[11:16] if len(ts) >= 16 else ""
            ctx.font_size = label_font_size
            ctx.move_to(0, -20).text("@emfducks")
            ctx.font_size = small_font_size
            ctx.rgb(0.65, 0.65, 0.65)
            ctx.move_to(0, 10).text(date_part)
            if time_part:
                ctx.move_to(0, 10 + small_font_size).text(time_part)
        else:
            # content
            post = self._mastodon_post or "No post loaded."
            if self._mastodon_wrapped is None:
                self._masto_font, self._mastodon_wrapped = _auto_wrap(
                    ctx, post, _FACT_MAX_HEIGHT
                )
            self._draw_wrapped_lines(ctx, self._mastodon_wrapped, self._masto_font)

        ctx.restore()
        button_labels(ctx)
        self._draw_back_arrow(ctx)
        self._draw_button_icon(ctx, self._icon_refresh, 75, 75, align="right")
        if has_up:
            self._draw_up_arrow(ctx)
        if has_down:
            self._draw_down_arrow(ctx)

    def _draw_favs(self, ctx):
        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        if not self._fav_facts:
            ctx.font_size = small_font_size
            ctx.rgb(0.65, 0.65, 0.65)
            ctx.move_to(0, -12).text("No saved facts yet.")
            ctx.move_to(0, 12).text("Hold confirm to save.")
        else:
            # Counter badge
            ctx.font_size = small_font_size
            ctx.rgb(0.4, 0.6, 0.9)
            ctx.move_to(0, -90).text(f"{self._fav_index + 1} / {len(self._fav_facts)}")

            fact = self._fav_facts[self._fav_index]
            if self._fav_wrapped is None:
                self._fav_font, self._fav_wrapped = _auto_wrap(
                    ctx, fact, 150  # leave room for counter above
                )

            ctx.rgb(1, 1, 1)
            self._draw_wrapped_lines(ctx, self._fav_wrapped, self._fav_font, y_offset=5)

        ctx.restore()
        button_labels(ctx)
        self._draw_back_arrow(ctx)
        if len(self._fav_facts) > 1:
            if self._fav_index > 0:
                self._draw_up_arrow(ctx)
            if self._fav_index < len(self._fav_facts) - 1:
                self._draw_down_arrow(ctx)

    def _draw_back_arrow(self, ctx):
        """Filled left-pointing arrow at the CANCEL button position (-75, -75)."""
        ctx.save()
        ctx.rgb(1, 1, 1)
        ctx.begin_path()
        ctx.move_to(-75, -75)
        ctx.line_to(-68, -83)
        ctx.line_to(-68, -78)
        ctx.line_to(-61, -78)
        ctx.line_to(-61, -72)
        ctx.line_to(-68, -72)
        ctx.line_to(-68, -67)
        ctx.close_path()
        ctx.fill()
        ctx.restore()

    def _draw_up_arrow(self, ctx):
        """Filled upward arrow at the UP button position (0, -100)."""
        ctx.save()
        ctx.rgb(1, 1, 1)
        ctx.begin_path()
        ctx.move_to(0, -108)
        ctx.line_to(-7, -100)
        ctx.line_to(-2, -100)
        ctx.line_to(-2, -92)
        ctx.line_to(2, -92)
        ctx.line_to(2, -100)
        ctx.line_to(7, -100)
        ctx.close_path()
        ctx.fill()
        ctx.restore()

    def _draw_down_arrow(self, ctx):
        """Filled downward arrow at the DOWN button position (0, 100)."""
        ctx.save()
        ctx.rgb(1, 1, 1)
        ctx.begin_path()
        ctx.move_to(0, 108)
        ctx.line_to(7, 100)
        ctx.line_to(2, 100)
        ctx.line_to(2, 92)
        ctx.line_to(-2, 92)
        ctx.line_to(-2, 100)
        ctx.line_to(-7, 100)
        ctx.close_path()
        ctx.fill()
        ctx.restore()

    def _draw_leds(self, ctx):
        # Twinkling stars in party-mode colours
        sc = self._party["star_colours"]
        for star in self._stars:
            brightness = (math.sin(star[2]) + 1) / 2
            sr, sg, sb = sc[star[4]]
            ctx.rgb(sr / 255 * brightness, sg / 255 * brightness, sb / 255 * brightness)
            size = 2 if brightness > 0.65 else 1
            ctx.rectangle(star[0], star[1], size, size).fill()
            star[2] = (star[2] + star[3]) % 6.284

        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        tr, tg, tb = self._party["title_rgb"]
        sr, sg, sb = self._party["subtitle_rgb"]
        subtitle = self._party["subtitle"]

        if self._sprite:
            fw, fh = self._sprite["w"], self._sprite["h"]
            ox = -(fw * _SPRITE_SCALE) // 2
            oy = -(fh * _SPRITE_SCALE) // 2 - 10

            ctx.font_size = label_font_size
            ctx.rgb(tr, tg, tb)
            ctx.move_to(0, oy - 20).text("DUCK PARTY")

            self._draw_sprite(ctx, self._anim_frame, _SPRITE_SCALE, ox, oy)

            ctx.font_size = label_font_size
            ctx.rgb(sr, sg, sb)
            ctx.move_to(0, oy + fh * _SPRITE_SCALE + 20).text(subtitle)
        else:
            ctx.font_size = label_font_size
            ctx.rgb(tr, tg, tb)
            ctx.move_to(0, -20).text("DUCK PARTY")
            ctx.rgb(sr, sg, sb)
            ctx.move_to(0, 20).text(subtitle)

        ctx.restore()

    def _draw_photo(self, ctx):
        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(1, 1, 1)

        if self._fetching_photo:
            ctx.font_size = small_font_size
            ctx.move_to(0, 0).text(self._photo_loading_text)
            ctx.restore()
            button_labels(ctx)
            self._draw_back_arrow(ctx)
            return

        if self._photo_sub == "image":
            r_clip = 70
            img_y = 0
            try:
                ctx.save()
                ctx.begin_path()
                ctx.move_to(r_clip, img_y)
                ctx.arc(0, img_y, r_clip, 0, 2 * math.pi, False)
                ctx.clip()
                ctx.image(
                    self._photo_path,
                    -r_clip,
                    img_y - r_clip,
                    r_clip * 2,
                    r_clip * 2,
                )
                ctx.restore()
            except Exception:
                ctx.rgb(0.3, 0.3, 0.3)
                ctx.begin_path()
                ctx.arc(0, img_y, r_clip, 0, 2 * math.pi, False)
                ctx.fill()
                ctx.rgb(1, 1, 1)
                ctx.font_size = small_font_size
                ctx.move_to(0, img_y).text("No image")

            self._draw_down_arrow(ctx)

        elif self._photo_sub == "info":
            if self._photo_wrapped_title is None:
                title = self._photo_title or "Rubber Duck"
                self._photo_title_font, self._photo_wrapped_title = _auto_wrap(
                    ctx, title, max_height=35, width=170
                )
            ctx.rgb(0.4, 0.6, 0.9)
            self._draw_wrapped_lines(ctx, self._photo_wrapped_title, self._photo_title_font, y_offset=-80)

            if self._photo_wrapped_desc is None:
                desc = self._photo_description or "A duck photo"
                self._photo_desc_font, self._photo_wrapped_desc = _auto_wrap(
                    ctx, desc, max_height=55, width=170
                )
            ctx.rgb(1, 1, 1)
            self._draw_wrapped_lines(ctx, self._photo_wrapped_desc, self._photo_desc_font, y_offset=-20)

            if self._photo_wrapped_attrib is None:
                attrib = f"by {self._photo_attribution}" if self._photo_attribution else "by Unknown"
                self._photo_attrib_font, self._photo_wrapped_attrib = _auto_wrap(
                    ctx, attrib, max_height=30, width=170
                )
            ctx.rgb(0.7, 0.7, 0.7)
            self._draw_wrapped_lines(ctx, self._photo_wrapped_attrib, self._photo_attrib_font, y_offset=35)

            if self._photo_license:
                ctx.font_size = small_font_size - 2
                ctx.rgb(0.5, 0.5, 0.5)
                ctx.move_to(0, 75).text(self._photo_license)

            self._draw_up_arrow(ctx)

        ctx.restore()
        button_labels(ctx)
        self._draw_back_arrow(ctx)
        self._draw_button_icon(ctx, self._icon_binoculars, 75, 75, align="right")

    def _draw_credits(self, ctx):
        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        ctx.rgb(0.4, 0.6, 0.9)
        ctx.font_size = label_font_size
        if self._credits_page == 2:
            ctx.move_to(0, -75).text("Creator")
        else:
            ctx.move_to(0, -75).text("Thanks!")

        ctx.rgb(1, 1, 1)
        ctx.font_size = small_font_size

        if self._credits_page == 0:
            ctx.move_to(0, -30).text("@emfducks")
            ctx.move_to(0, 10).text("caz-bee (sprites)")
            ctx.move_to(0, 50).text("starwatchers-studio")
        elif self._credits_page == 1:
            ctx.move_to(0, -45).text("ducks.now (photos)")
            ctx.move_to(0, -15).text("random-d.uk (photos)")
            ctx.move_to(0, 15).text("Anon duck facts API")
            ctx.move_to(0, 45).text("bjorn-knudsen (facts)")
        elif self._credits_page == 2:
            ctx.move_to(0, -30).text("An App by Andy Piper")
            ctx.move_to(0, 10).text("@andypiper@macaw.social")
            ctx.move_to(0, 50).text("You're very welcome.")

        ctx.restore()

        if self._credits_page > 0:
            self._draw_up_arrow(ctx)

        max_page = 2 if self._credits_secret_unlocked else 1
        if self._credits_page < max_page:
            self._draw_down_arrow(ctx)

        self._draw_back_arrow(ctx)

    def _draw_prompt_clear(self, ctx):
        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        ctx.rgb(1, 1, 1)
        ctx.font_size = label_font_size
        ctx.move_to(0, -30).text("Clear quacks?")

        ctx.font_size = small_font_size
        ctx.rgb(0.7, 0.7, 0.7)
        ctx.move_to(0, 15).text("C to confirm")
        ctx.move_to(0, 45).text("Any other to cancel")

        ctx.restore()

    def _draw_stub(self, ctx):
        label = _STUB_LABELS.get(self._view, "Coming soon")
        ctx.save()
        ctx.font_size = label_font_size
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(1, 1, 1)
        ctx.move_to(0, -10).text(label)
        ctx.font_size = small_font_size
        ctx.rgb(0.5, 0.5, 0.5)
        ctx.move_to(0, 15).text("coming soon")
        ctx.restore()
        button_labels(ctx)
        self._draw_back_arrow(ctx)


__app_export__ = DuckFactsApp
