# FSM состояния (чат/не чат)

from aiogram.fsm.state import State, StatesGroup

class ChatFlow(StatesGroup):
    chatting = State()