from aiogram.fsm.state import State, StatesGroup


class TaskForm(StatesGroup):
    title = State()
    deadline = State()
    estimate = State()
    comment_choice = State()
    comment = State()
    edit = State()


class RatingForm(StatesGroup):
    points = State()


class PenaltyForm(StatesGroup):
    target = State()
    kind = State()
    quantity = State()
    comment_choice = State()
    comment = State()
    confirm = State()


class SettingsForm(StatesGroup):
    type_name = State()
    type_emoji = State()
    type_description = State()
    type_method = State()
    type_rate = State()
    type_edit = State()
    target = State()


class ConfirmationForm(StatesGroup):
    action = State()
