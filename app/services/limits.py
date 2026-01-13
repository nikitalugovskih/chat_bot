# лимиты/бан логика

from datetime import date
from app.db.models import UserSubscription

def is_paid_active(u: UserSubscription, today: date) -> bool:
    return (
        u.subscribe == 1
        and u.end_payment_date is not None
        and today <= u.end_payment_date
    )

def is_banned(u: UserSubscription, today: date) -> bool:
    return u.ban_until is not None and today <= u.ban_until