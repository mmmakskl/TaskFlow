def duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if not hours:
        return f"{remainder} мин"
    return f"{hours} ч" + (f" {remainder} мин" if remainder else "")
