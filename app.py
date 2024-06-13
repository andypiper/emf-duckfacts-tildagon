import app
import requests

from app_components import Notification, clear_background
from app_components import layout as layout
from events.input import Buttons, BUTTON_TYPES


class DuckFactsApp(app.App):
    # App to display random duck facts
    # TODO: needs to handle scrolling on the LinearLayout for long facts

    def __init__(self):
        self.fact_url = "https://03vpefsitf.execute-api.eu-west-1.amazonaws.com/prod/"
        self.fact = "Ready for a Duck Fact? \n Press the button!"

        self.df_layout = layout.LinearLayout([layout.TextDisplay(self.fact)])

        self.button_states = Buttons(self)
        self.notification = None

    def select_handler(self):
        self.fact = get_fact(self.fact_url)
        self.notification = Notification("QUACK!")

    def back_handler(self):
        self.button_states.clear()
        self.minimise()

    def update(self, delta):
        if self.notification:
            self.notification.update(delta)
        if self.button_states.get(BUTTON_TYPES["CANCEL"]):
            self.back_handler()
        if self.button_states.get(BUTTON_TYPES["CONFIRM"]):
            self.select_handler()
        # these do get passed through but not working as expected
        # presumably because the layout is not being redrawn here
        # if self.button_states.get(BUTTON_TYPES["UP"]):
        #     print("up")
        #     self.df_layout.button_event(BUTTON_TYPES["UP"])
        # if self.button_states.get(BUTTON_TYPES["DOWN"]):
        #     print("down")
        #     self.df_layout.button_event(BUTTON_TYPES["DOWN"])

    def draw(self, ctx):
        clear_background(ctx)
        ctx.save()

        if self.notification:
            self.notification.draw(ctx)

        # ctx.font = "Comic Mono"
        # font styling not working here?
        ctx.font_size = 24
        # ctx.text_align = ctx.CENTER
        # ctx.text_baseline = ctx.MIDDLE

        self.df_layout = layout.LinearLayout([layout.TextDisplay(self.fact)])
        # self.df_layout.y_offset = 90
        # self.df_layout.x_offset = -50
        self.df_layout.draw(ctx)

        ctx.restore()


def get_fact(url):
    data = requests.get(url).json()
    fact = f"{data['fact']}"

    # print(data)
    # TODO: handle feistyness and quackiness from API
    return fact


__app_export__ = DuckFactsApp
