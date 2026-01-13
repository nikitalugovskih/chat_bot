# FSM состояния (чат/не чат)

from aiogram.fsm.state import State, StatesGroup

class ChatFlow(StatesGroup):
    chatting = State()

class AdminFlow(StatesGroup):
    waiting_chat_id_for_check = State()
    waiting_chat_id_for_grant = State()
    waiting_chat_id_for_reset = State()
    waiting_chat_id_for_delete = State()