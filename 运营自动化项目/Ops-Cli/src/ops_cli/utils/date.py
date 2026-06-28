from datetime import date, timedelta


def yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()
