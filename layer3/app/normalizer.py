from dateutil import parser


def normalize_deadline(deadline: str) -> str:
    try:
        dt = parser.parse(deadline)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return deadline